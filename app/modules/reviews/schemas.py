"""Request and response schemas for parking reviews."""

from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field


class ReviewInput(BaseModel):
    content: str = Field(min_length=1, max_length=300)


class Review(BaseModel):
    id: str
    content: str
    user_id: UUID
    user_nickname: str
    created_at: AwareDatetime


class ReviewListResponse(BaseModel):
    items: list[Review]
    total: int


class ReviewCreatedResponse(BaseModel):
    message: str
    review: Review
