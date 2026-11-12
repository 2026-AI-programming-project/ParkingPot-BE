from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.modules.auth.schemas import public_user
from app.modules.auth.service import AuthService

bearer = HTTPBearer(auto_error=False)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def authenticated_account(service: AuthServiceDep, credentials: Credentials):
    return service.authenticate(credentials.credentials if credentials else None)


AuthenticatedAccount = Annotated[dict, Depends(authenticated_account)]


def current_user(account: AuthenticatedAccount) -> dict[str, str]:
    return public_user(account)


CurrentUser = Annotated[dict[str, str], Depends(current_user)]


def client_ip(request: Request) -> str:
    # Trust only uvicorn's configured proxy IPs, never raw forwarding headers here.
    return request.client.host if request.client else "unknown"
