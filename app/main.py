"""FastAPI application with membership and parking routes."""

from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.config import Settings as ParkingSettings
from app.core.config import Settings as AuthSettings
from app.db.database import Database
from app.modules.auth.service import AuthService
from app.modules.auth.tables import AUTH_TABLES
from app.parking_api import router as parking_router
from app.parking_db import init_db as init_parking_db
from app.realtime import RealtimeRefresher


def create_app(
    settings: AuthSettings | None = None,
    parking_settings: ParkingSettings | None = None,
) -> FastAPI:
    if settings is None:
        settings = AuthSettings.from_env()
        if parking_settings is not None:
            settings = replace(
                settings,
                database_path=Path(parking_settings.database_path).with_suffix(".auth.sqlite3"),
            )
    if parking_settings is None:
        parking_settings = (
            ParkingSettings(database_path=str(settings.database_path.parent / "parking.sqlite3"))
            if settings.database_path != Path("data/parkingpot.sqlite3")
            else ParkingSettings.from_env()
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = Database(settings.database_path)
        database.initialize(AUTH_TABLES)
        init_parking_db(parking_settings)
        app.state.database = database
        app.state.auth_service = AuthService(settings, database)
        refresher = None
        if parking_settings.status_service_key and parking_settings.realtime_refresh_seconds > 0:
            refresher = RealtimeRefresher(parking_settings)
            refresher.start()
        try:
            yield
        finally:
            if refresher is not None:
                refresher.stop()

    app = FastAPI(
        title="ParkingPot API",
        version="0.1.0",
        description="회원 및 주차장 검색, 혼잡도, 즐겨찾기 API",
        lifespan=lifespan,
    )
    app.state.settings = parking_settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
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
        errors = [
            {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.get("/health", tags=["상태"])
    def health():
        return {"status": "ok"}

    app.include_router(api_router)
    app.include_router(parking_router)
    return app


app = create_app()
