"""Parking review endpoints."""

from fastapi import APIRouter, Request, status

from app.api.dependencies import CurrentUser
from app.modules.reviews.schemas import ReviewCreatedResponse, ReviewInput, ReviewListResponse
from app.modules.reviews.service import create_review, list_reviews

router = APIRouter()


@router.get(
    "/parking-lots/{parking_id}/reviews",
    response_model=ReviewListResponse,
    tags=["주차장 리뷰"],
)
def get_reviews(parking_id: str, request: Request) -> dict:
    return list_reviews(request.app.state.settings, parking_id)


@router.post(
    "/parking-lots/{parking_id}/reviews",
    response_model=ReviewCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["주차장 리뷰"],
)
def post_review(parking_id: str, body: ReviewInput, request: Request, user: CurrentUser) -> dict:
    return create_review(request.app.state.settings, parking_id, user, body.content)
