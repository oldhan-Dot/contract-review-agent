"""前后端统一的响应结构。"""

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel

DataT = TypeVar("DataT")


class ApiResponse(BaseModel, Generic[DataT]):
    code: Literal[0] = 0
    message: str = "ok"
    data: DataT


class FieldError(BaseModel):
    field: str
    message: str


class ErrorResponse(BaseModel):
    code: int
    message: str
    data: None = None
    errors: list[FieldError] | None = None
