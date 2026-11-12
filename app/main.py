from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.core.config import Settings
from app.db.database import Database
from app.modules.auth.service import AuthService
from app.modules.auth.tables import AUTH_TABLES


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = Database(settings.database_path)
        database.initialize(AUTH_TABLES)
        app.state.database = database
        app.state.auth_service = AuthService(settings, database)
        yield

    app = FastAPI(
        title="ParkingPot 회원 API",
        version="0.1.0",
        description=(
            "회원가입, 이메일 인증, 세션 인증 및 마이페이지 API. 비밀번호는 반환하지 않습니다."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["Retry-After"],
    )

    @app.middleware("http")
    async def no_store(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Pydantic's default HTTP error body includes submitted inputs, including passwords.
        errors = [
            {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.get("/health", tags=["상태"])
    def health():
        return {"status": "ok"}

    app.include_router(api_router)
    return app


app = create_app()
