"""Request and response schemas for parking reviews."""

from pydantic import BaseModel, Field


class ReviewInput(BaseModel):
    content: str = Field(min_length=1, max_length=300)
