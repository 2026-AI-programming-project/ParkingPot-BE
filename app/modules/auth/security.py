import hashlib
import hmac

from pwdlib import PasswordHash

password_hasher = PasswordHash.recommended()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_code(secret: str, email: str, purpose: str, user_id: str | None, code: str) -> str:
    value = f"{email}\0{purpose}\0{user_id or ''}\0{code}"
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()
