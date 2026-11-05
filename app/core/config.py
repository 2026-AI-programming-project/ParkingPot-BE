import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    environment: str = "development"
    database_path: Path = Path("data/parkingpot.sqlite3")
    auth_secret: str = field(default_factory=lambda: secrets.token_hex(32))
    session_ttl_seconds: int = 604800
    code_ttl_seconds: int = 600
    code_resend_seconds: int = 60
    code_max_attempts: int = 5
    cors_origins: tuple[str, ...] = ("http://localhost:3000", "http://localhost:5173")
    mail_backend: str = "file"
    mail_outbox_dir: Path = Path("data/outbox")
    mail_from: str = "ParkingPot <noreply@parkingpot.example>"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_security: str = "starttls"
    smtp_timeout_seconds: int = 10

    def __post_init__(self):
        if self.environment not in {"development", "production", "test"}:
            raise ValueError("APP_ENV must be development, production, or test")
        if self.mail_backend not in {"file", "smtp"}:
            raise ValueError("MAIL_BACKEND must be file or smtp")
        if self.smtp_security not in {"starttls", "ssl", "none"}:
            raise ValueError("SMTP_SECURITY must be starttls, ssl, or none")
        if self.mail_backend == "smtp" and not self.smtp_host:
            raise ValueError("SMTP_HOST is required for SMTP delivery")
        if (
            min(
                self.session_ttl_seconds,
                self.code_ttl_seconds,
                self.code_max_attempts,
                self.smtp_timeout_seconds,
            )
            < 1
            or self.code_resend_seconds < 0
        ):
            raise ValueError("TTL, timeout and attempts must be positive")
        if self.environment == "production":
            if len(self.auth_secret) < 32:
                raise ValueError("Production requires AUTH_SECRET with at least 32 characters")
            if self.mail_backend != "smtp" or self.smtp_security == "none":
                raise ValueError("Production requires SMTP with TLS")
            if "*" in self.cors_origins:
                raise ValueError("Production requires explicit CORS origins")

    @classmethod
    def from_env(cls):
        load_dotenv()
        environment = os.getenv("APP_ENV", "development")
        secret = os.getenv("AUTH_SECRET", "")
        if environment == "production" and not secret:
            raise ValueError("AUTH_SECRET is required in production")
        return cls(
            environment=environment,
            database_path=Path(os.getenv("DATABASE_PATH", "data/parkingpot.sqlite3")),
            auth_secret=secret or secrets.token_hex(32),
            session_ttl_seconds=int(os.getenv("SESSION_TTL_SECONDS", "604800")),
            code_ttl_seconds=int(os.getenv("CODE_TTL_SECONDS", "600")),
            code_resend_seconds=int(os.getenv("CODE_RESEND_SECONDS", "60")),
            code_max_attempts=int(os.getenv("CODE_MAX_ATTEMPTS", "5")),
            cors_origins=tuple(
                x.strip()
                for x in os.getenv(
                    "CORS_ORIGINS", "http://localhost:3000,http://localhost:5173"
                ).split(",")
                if x.strip()
            ),
            mail_backend=os.getenv("MAIL_BACKEND", "file"),
            mail_outbox_dir=Path(os.getenv("MAIL_OUTBOX_DIR", "data/outbox")),
            mail_from=os.getenv("MAIL_FROM", "ParkingPot <noreply@parkingpot.example>"),
            smtp_host=os.getenv("SMTP_HOST", ""),
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_username=os.getenv("SMTP_USERNAME", ""),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            smtp_security=os.getenv("SMTP_SECURITY", "starttls"),
            smtp_timeout_seconds=int(os.getenv("SMTP_TIMEOUT_SECONDS", "10")),
        )
