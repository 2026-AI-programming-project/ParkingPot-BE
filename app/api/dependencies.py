from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.modules.auth.service import AuthService

bearer = HTTPBearer(auto_error=False)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def current_user(service: AuthServiceDep, credentials: Credentials):
    return service.authenticate(credentials.credentials if credentials else None)


CurrentUser = Annotated[dict, Depends(current_user)]


def client_ip(request: Request) -> str:
    # Trust only uvicorn's configured proxy IPs, never raw forwarding headers here.
    return request.client.host if request.client else "unknown"
