import hmac
import logging
import secrets
import smtplib
import time
from uuid import uuid4

from app.common.errors import failure
from app.core.config import Settings
from app.db.database import Database
from app.integrations.mailer import Mailer
from app.modules.auth.schemas import ProfileUpdateRequest, SignupRequest, public_user
from app.modules.auth.security import hash_code, hash_token, password_hasher

logger = logging.getLogger(__name__)

PURPOSE_LABELS = {
    "signup": "회원가입",
    "email_change": "이메일 변경",
    "password_change": "비밀번호 변경",
}


class AuthService:
    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database
        self.mailer = Mailer(settings)
        self.now = time.time
        # Unknown addresses take the same password verification path as known ones.
        self.dummy_password_hash = password_hasher.hash(secrets.token_urlsafe(32))

    def rate_limit(self, key: str, limit: int, window: int):
        now = int(self.now())
        bucket = hash_token(key)
        blocked = False
        retry_after = window
        with self.database.connect(write=True) as db:
            db.execute("DELETE FROM rate_limits WHERE reset_at <= ?", (now,))
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            row = db.execute("SELECT * FROM rate_limits WHERE bucket = ?", (bucket,)).fetchone()
            if row is None:
                db.execute("INSERT INTO rate_limits VALUES (?, 1, ?)", (bucket, now + window))
            elif row["count"] >= limit:
                blocked = True
                retry_after = max(1, row["reset_at"] - now)
            else:
                db.execute("UPDATE rate_limits SET count = count + 1 WHERE bucket = ?", (bucket,))
        if blocked:
            raise failure(
                429,
                "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
                **{"Retry-After": str(retry_after)},
            )

    def authenticate(self, token: str | None):
        if not token or len(token) > 256:
            raise failure(401, "로그인이 필요합니다.", **{"WWW-Authenticate": "Bearer"})
        with self.database.connect() as db:
            user = db.execute(
                "SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id "
                "WHERE token_hash = ? AND expires_at > ?",
                (hash_token(token), int(self.now())),
            ).fetchone()
        if user is None:
            raise failure(
                401, "로그인이 만료되었거나 유효하지 않습니다.", **{"WWW-Authenticate": "Bearer"}
            )
        return dict(user)

    def request_code(self, email: str, purpose: str, user: dict | None, ip: str):
        self.rate_limit(f"mail:ip:{ip}", 10, 3600)
        now = int(self.now())
        with self.database.connect(write=True) as db:
            user_id = None
            if purpose == "signup":
                if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
                    raise failure(409, "이미 사용 중인 이메일입니다.")
            else:
                if user is None:
                    raise failure(401, "로그인이 필요합니다.")
                current = db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
                if current is None:
                    raise failure(401, "로그인이 필요합니다.")
                user_id = current["id"]
                if purpose == "password_change" and email != current["email"]:
                    raise failure(400, "비밀번호 변경 인증은 현재 이메일로 요청해 주세요.")
                if purpose == "email_change":
                    if email == current["email"]:
                        raise failure(400, "현재 이메일과 다른 이메일을 입력해 주세요.")
                    if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
                        raise failure(409, "이미 사용 중인 이메일입니다.")
            existing = db.execute(
                "SELECT sent_at FROM email_verifications WHERE email = ? AND purpose = ?",
                (email, purpose),
            ).fetchone()
            if existing and existing["sent_at"] + self.settings.code_resend_seconds > now:
                remaining = existing["sent_at"] + self.settings.code_resend_seconds - now
                raise failure(
                    429,
                    "인증번호 재발송 대기 시간이 지나지 않았습니다.",
                    **{"Retry-After": str(remaining)},
                )
            code = f"{secrets.randbelow(1_000_000):06d}"
            digest = hash_code(self.settings.auth_secret, email, purpose, user_id, code)
            db.execute(
                "INSERT INTO email_verifications "
                "(email, purpose, user_id, code_hash, expires_at, sent_at, attempts) "
                "VALUES (?, ?, ?, ?, ?, ?, 0) "
                "ON CONFLICT(email, purpose) DO UPDATE SET user_id = excluded.user_id, "
                "code_hash = excluded.code_hash, expires_at = excluded.expires_at, "
                "sent_at = excluded.sent_at, attempts = 0",
                (email, purpose, user_id, digest, now + self.settings.code_ttl_seconds, now),
            )
            try:
                self.mailer.send(
                    email,
                    f"[ParkingPot] {PURPOSE_LABELS[purpose]} 인증번호",
                    f"인증번호: {code}\n"
                    f"유효 시간: {self.settings.code_ttl_seconds}초\n"
                    "요청하지 않았다면 이 메일을 무시해 주세요.\n",
                )
            except (OSError, smtplib.SMTPException):
                # Never log SMTP exceptions: a provider may include recipient data or credentials.
                logger.warning("Verification email delivery failed")
                raise failure(
                    503, "이메일을 발송하지 못했습니다. 잠시 후 다시 시도해 주세요."
                ) from None

    def check_code(self, db, email: str, purpose: str, user_id: str | None, code: str | None):
        """Return an error so failed-attempt increments can commit before raising it."""
        row = db.execute(
            "SELECT * FROM email_verifications WHERE email = ? AND purpose = ?",
            (email, purpose),
        ).fetchone()
        if row is None or row["user_id"] != user_id:
            return failure(400, "이메일 인증번호를 먼저 요청해 주세요.")
        if row["expires_at"] <= int(self.now()):
            return failure(400, "인증번호가 만료되었습니다. 다시 요청해 주세요.")
        if row["attempts"] >= self.settings.code_max_attempts:
            return failure(429, "인증 시도 횟수를 초과했습니다. 인증번호를 다시 요청해 주세요.")
        expected = hash_code(self.settings.auth_secret, email, purpose, user_id, code or "")
        if code is None or not hmac.compare_digest(row["code_hash"], expected):
            db.execute(
                "UPDATE email_verifications SET attempts = attempts + 1 "
                "WHERE email = ? AND purpose = ?",
                (email, purpose),
            )
            return failure(400, "이메일 인증번호가 일치하지 않습니다.")
        return None

    @staticmethod
    def consume_code(db, email: str, purpose: str):
        db.execute(
            "DELETE FROM email_verifications WHERE email = ? AND purpose = ?", (email, purpose)
        )

    def signup(self, data: SignupRequest, ip: str):
        self.rate_limit(f"signup:ip:{ip}", 20, 600)
        encoded_password = password_hasher.hash(data.password)
        now = int(self.now())
        with self.database.connect(write=True) as db:
            if db.execute("SELECT 1 FROM users WHERE email = ?", (data.email,)).fetchone():
                raise failure(409, "이미 사용 중인 이메일입니다.")
            if db.execute("SELECT 1 FROM users WHERE nickname = ?", (data.nickname,)).fetchone():
                raise failure(409, "이미 사용 중인 닉네임입니다.")
            error = self.check_code(db, data.email, "signup", None, data.verification_code)
            if error is None:
                user_id = str(uuid4())
                db.execute(
                    "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, data.email, data.nickname, encoded_password, now, now),
                )
                self.consume_code(db, data.email, "signup")
        if error:
            raise error
        return {"id": user_id, "email": data.email, "nickname": data.nickname}

    def login(self, email: str, password: str, ip: str):
        self.rate_limit(f"login:ip:{ip}", 50, 300)
        self.rate_limit(f"login:email:{email}", 10, 300)
        with self.database.connect() as db:
            user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        digest = user["password_hash"] if user else self.dummy_password_hash
        valid = password_hasher.verify(password, digest)
        if not user or not valid:
            raise failure(
                401, "이메일 또는 비밀번호가 올바르지 않습니다.", **{"WWW-Authenticate": "Bearer"}
            )
        token = secrets.token_urlsafe(32)
        with self.database.connect(write=True) as db:
            # Account/password may have changed while Argon2 was verifying.
            current = db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
            if current is None or current["password_hash"] != digest or current["email"] != email:
                raise failure(401, "계정 정보가 변경되었습니다. 다시 로그인해 주세요.")
            db.execute(
                "INSERT INTO sessions VALUES (?, ?, ?)",
                (
                    hash_token(token),
                    user["id"],
                    int(self.now()) + self.settings.session_ttl_seconds,
                ),
            )
        return token, public_user(current)

    def logout(self, token: str):
        with self.database.connect(write=True) as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),))

    def verify_password(self, user: dict, password: str, ip: str):
        self.rate_limit(f"password-verify:{user['id']}:{ip}", 10, 300)
        if not password_hasher.verify(password, user["password_hash"]):
            raise failure(400, "비밀번호가 일치하지 않습니다.")

    def update_profile(self, user: dict, data: ProfileUpdateRequest):
        encoded_password = password_hasher.hash(data.password) if data.password else None
        error = None
        with self.database.connect(write=True) as db:
            current = db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
            if current is None:
                raise failure(401, "로그인이 필요합니다.")
            email = data.email or current["email"]
            nickname = data.nickname or current["nickname"]
            email_changed = email != current["email"]
            duplicate = db.execute(
                "SELECT 1 FROM users WHERE id != ? AND (email = ? OR nickname = ?)",
                (user["id"], email, nickname),
            ).fetchone()
            if duplicate:
                raise failure(409, "이미 사용 중인 이메일 또는 닉네임입니다.")
            if email_changed:
                error = self.check_code(
                    db, email, "email_change", user["id"], data.email_verification_code
                )
            elif data.email_verification_code is not None:
                raise failure(400, "변경할 이메일이 현재 이메일과 같습니다.")
            if error is None and encoded_password:
                error = self.check_code(
                    db,
                    current["email"],
                    "password_change",
                    user["id"],
                    data.password_verification_code,
                )
            if error is None:
                db.execute(
                    "UPDATE users SET email = ?, nickname = ?, password_hash = ?, updated_at = ? "
                    "WHERE id = ?",
                    (
                        email,
                        nickname,
                        encoded_password or current["password_hash"],
                        int(self.now()),
                        user["id"],
                    ),
                )
                if email_changed or encoded_password:
                    # Revoke every device and every outstanding account-bound code.
                    db.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
                    db.execute("DELETE FROM email_verifications WHERE user_id = ?", (user["id"],))
                    # A signup challenge issued before this address was adopted is now stale.
                    self.consume_code(db, email, "signup")
        if error:
            raise error
        return {"id": user["id"], "email": email, "nickname": nickname}, bool(
            email_changed or encoded_password
        )

    def delete_account(self, user: dict):
        with self.database.connect(write=True) as db:
            self.consume_code(db, user["email"], "signup")
            db.execute("DELETE FROM users WHERE id = ?", (user["id"],))
