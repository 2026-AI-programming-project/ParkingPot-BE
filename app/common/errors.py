from fastapi import HTTPException


def failure(status: int, message: str, **headers) -> HTTPException:
    return HTTPException(status_code=status, detail=message, headers=headers or None)
