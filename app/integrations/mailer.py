import smtplib
import ssl
from email.message import EmailMessage
from uuid import uuid4

from app.core.config import Settings

PURPOSE_LABELS = {
    "signup": "회원가입",
    "email_change": "이메일 변경",
    "password_change": "비밀번호 변경",
}


class Mailer:
    def __init__(self, settings: Settings):
        self.settings = settings

    def send_code(self, email: str, purpose: str, code: str):
        settings = self.settings
        message = EmailMessage()
        message["From"] = settings.mail_from
        message["To"] = email
        message["Subject"] = f"[ParkingPot] {PURPOSE_LABELS[purpose]} 인증번호"
        message.set_content(
            f"인증번호: {code}\n"
            f"유효 시간: {settings.code_ttl_seconds}초\n"
            "요청하지 않았다면 이 메일을 무시해 주세요.\n"
        )
        if settings.mail_backend == "file":
            settings.mail_outbox_dir.mkdir(parents=True, exist_ok=True)
            path = settings.mail_outbox_dir / f"{uuid4().hex}.eml"
            with path.open("xb") as output:
                output.write(message.as_bytes())
            return
        smtp_class = smtplib.SMTP_SSL if settings.smtp_security == "ssl" else smtplib.SMTP
        kwargs = {"timeout": settings.smtp_timeout_seconds}
        if settings.smtp_security == "ssl":
            kwargs["context"] = ssl.create_default_context()
        with smtp_class(settings.smtp_host, settings.smtp_port, **kwargs) as smtp:
            if settings.smtp_security == "starttls":
                smtp.starttls(context=ssl.create_default_context())
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
