"""FastAPI application factory."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import router
from app.config import Settings
from app.db import init_db
from app.realtime import RealtimeRefresher


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(settings)
        refresher = None
        if settings.status_service_key and settings.realtime_refresh_seconds > 0:
            refresher = RealtimeRefresher(settings)
            refresher.start()
        try:
            yield
        finally:
            if refresher is not None:
                refresher.stop()

    app = FastAPI(title="ParkingPot API", lifespan=lifespan)
    app.state.settings = settings

    @app.middleware("http")
    async def no_cache_parking(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/parking-lots"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(router)
    return app


app = create_app()

