from fastapi import APIRouter, Request

from app.modules.auth.dependencies import (
    AuthenticatedAccount,
    AuthServiceDep,
    Credentials,
    CurrentUser,
    client_ip,
)
from app.modules.auth.schemas import (
    EmailCodeRequest,
    EmailCodeResponse,
    LoginRequest,
    LoginResponse,
    MessageResponse,
    PasswordVerifyRequest,
    ProfileUpdateRequest,
    ProfileUpdateResponse,
    SignupRequest,
    SignupResponse,
    UserResponse,
)

router = APIRouter()


@router.post(
    "/auth/email-verifications",
    response_model=EmailCodeResponse,
    status_code=202,
    tags=["인증"],
    summary="이메일 인증번호 발송",
)
def send_code(
    data: EmailCodeRequest, request: Request, service: AuthServiceDep, credentials: Credentials
):
    user = None
    if data.purpose != "signup":
        user = service.authenticate(credentials.credentials if credentials else None)
    service.request_code(str(data.email), data.purpose, user, client_ip(request))
    return {
        "message": "인증번호가 발송되었습니다.",
        "expires_in": service.settings.code_ttl_seconds,
        "resend_after": service.settings.code_resend_seconds,
    }


@router.post(
    "/auth/signup",
    response_model=SignupResponse,
    status_code=201,
    tags=["인증"],
    summary="회원가입",
)
def signup(data: SignupRequest, request: Request, service: AuthServiceDep):
    user = service.signup(data, client_ip(request))
    return {"message": "계정이 생성되었습니다.", "user": user}


@router.post("/auth/login", response_model=LoginResponse, tags=["인증"], summary="로그인")
def login(data: LoginRequest, request: Request, service: AuthServiceDep):
    token, user = service.login(str(data.email), data.password, client_ip(request))
    return {
        "message": "로그인이 되었습니다.",
        "access_token": token,
        "token_type": "bearer",
        "expires_in": service.settings.session_ttl_seconds,
        "user": user,
    }


@router.post("/auth/logout", response_model=MessageResponse, tags=["인증"], summary="로그아웃")
def logout(service: AuthServiceDep, credentials: Credentials, user: CurrentUser):
    service.logout(credentials.credentials)
    return {"message": "로그아웃이 되었습니다."}


@router.get("/users/me", response_model=UserResponse, tags=["마이페이지"], summary="내 정보 조회")
def me(user: CurrentUser):
    return user


@router.post(
    "/users/me/password/verify",
    response_model=MessageResponse,
    tags=["마이페이지"],
    summary="현재 비밀번호 확인",
)
def verify_password(
    data: PasswordVerifyRequest,
    request: Request,
    service: AuthServiceDep,
    user: AuthenticatedAccount,
):
    service.verify_password(user, data.password, client_ip(request))
    return {"message": "비밀번호가 확인되었습니다."}


@router.patch(
    "/users/me",
    response_model=ProfileUpdateResponse,
    tags=["마이페이지"],
    summary="내 정보 수정",
)
def update_me(data: ProfileUpdateRequest, service: AuthServiceDep, user: CurrentUser):
    updated, requires_login = service.update_profile(user, data)
    return {
        "message": "수정이 완료 되었습니다",
        "user": updated,
        "requires_login": requires_login,
    }


@router.delete("/users/me", response_model=MessageResponse, tags=["마이페이지"], summary="회원탈퇴")
def delete_me(service: AuthServiceDep, user: CurrentUser):
    service.delete_account(user)
    return {"message": "회원 탈퇴가 완료되었습니다"}
