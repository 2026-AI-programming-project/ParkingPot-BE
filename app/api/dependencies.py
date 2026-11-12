from typing import Annotated

from fastapi import Depends, Request

from app.db.database import Database
from app.modules.auth.dependencies import CurrentUser as CurrentUser


def get_database(request: Request) -> Database:
    return request.app.state.database


DatabaseDep = Annotated[Database, Depends(get_database)]
