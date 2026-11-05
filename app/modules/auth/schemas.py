from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

Code = Annotated[str, Field(pattern=r"^[0-9]{6}$")]
Password = Annotated[str, Field(min_length=8, max_length=128)]
Nickname = Annotated[str, Field(min_length=2, max_length=30)]
Purpose = Literal["signup", "email_change", "password_change"]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    @field_validator("email", check_fields=False)
    @classmethod
    def normalize_email(cls, value):
        return str(value).lower() if value is not None else None

    @field_validator("nickname", mode="before", check_fields=False)
    @classmethod
    def trim_nickname(cls, value):
        return value.strip() if isinstance(value, str) else value


class MessageResponse(BaseModel):
    message: str


class UserResponse(BaseModel):
    id: str
    email: EmailStr
    nickname: str


class EmailCodeRequest(RequestModel):
    email: EmailStr
    purpose: Purpose = "signup"


class SignupRequest(RequestModel):
    email: EmailStr
    nickname: Nickname
    verification_code: Code
    password: Password
    password_confirm: Password

    @model_validator(mode="after")
    def passwords_match(self):
        if self.password != self.password_confirm:
            raise ValueError("비밀번호와 비밀번호 확인이 일치하지 않습니다.")
        return self


class LoginRequest(RequestModel):
    email: EmailStr
    password: Annotated[str, Field(min_length=1, max_length=128)]


class SignupResponse(MessageResponse):
    user: UserResponse


class LoginResponse(MessageResponse):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserResponse


class EmailCodeResponse(MessageResponse):
    expires_in: int
    resend_after: int


class PasswordVerifyRequest(RequestModel):
    password: Annotated[str, Field(min_length=1, max_length=128)]


class ProfileUpdateRequest(RequestModel):
    email: EmailStr | None = None
    nickname: Nickname | None = None
    password: Password | None = None
    password_confirm: Password | None = None
    email_verification_code: Code | None = None
    password_verification_code: Code | None = None

    @model_validator(mode="after")
    def validate_changes(self):
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("수정할 필드에는 null을 입력할 수 없습니다.")
        if not self.model_fields_set & {"email", "nickname", "password"}:
            raise ValueError("수정할 정보를 하나 이상 입력해 주세요.")
        if self.password is not None:
            if self.password != self.password_confirm:
                raise ValueError("비밀번호와 비밀번호 확인이 일치하지 않습니다.")
            if self.password_verification_code is None:
                raise ValueError("비밀번호 변경에는 현재 이메일의 인증번호가 필요합니다.")
        elif self.password_confirm is not None or self.password_verification_code is not None:
            raise ValueError("비밀번호 변경 정보를 함께 입력해 주세요.")
        if self.email is None and self.email_verification_code is not None:
            raise ValueError("변경할 이메일을 입력해 주세요.")
        return self


class ProfileUpdateResponse(MessageResponse):
    user: UserResponse
    requires_login: bool


def public_user(user) -> dict:
    return {key: user[key] for key in ("id", "email", "nickname")}
