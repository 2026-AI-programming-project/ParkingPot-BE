"""Display the most recent development email: python -m scripts.read_mail."""

from email import policy
from email.parser import BytesParser

from app.core.config import Settings

if __name__ == "__main__":
    settings = Settings.from_env()
    files = list(settings.mail_outbox_dir.glob("*.eml"))
    if not files:
        raise SystemExit("저장된 메일이 없습니다. 인증번호 발송 API를 먼저 호출해 주세요.")
    latest = max(files, key=lambda path: path.stat().st_mtime_ns)
    message = BytesParser(policy=policy.default).parsebytes(latest.read_bytes())
    print(f"To: {message['To']}\nSubject: {message['Subject']}\n{message.get_content()}")
