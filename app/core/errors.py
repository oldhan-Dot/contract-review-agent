"""将业务错误和请求格式错误转换成前端可识别的 JSON。"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.schemas.common import ErrorResponse, FieldError


class AppError(Exception):
    def __init__(self, status_code: int, code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError):
        body = ErrorResponse(code=exc.code, message=exc.message)
        return JSONResponse(
            status_code=exc.status_code, content=body.model_dump(exclude={"errors"})
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        messages = {
            "int_parsing": "必须是整数",
            "int_type": "必须是整数",
            "greater_than": "必须大于 0",
            "less_than_equal": "超过允许范围",
            "literal_error": "取值不在允许范围内",
            "missing": "缺少必填项",
        }
        # 不返回原始 input、请求体或异常上下文，避免回显敏感数据。
        errors = [
            FieldError(
                field=".".join(str(part) for part in error["loc"]),
                message=messages.get(error["type"], "格式不合法"),
            )
            for error in exc.errors()
        ]
        body = ErrorResponse(code=40001, message="请求参数校验失败", errors=errors)
        # 保留 FastAPI 的 422 状态；40001 是项目内的参数错误业务码。
        return JSONResponse(status_code=422, content=body.model_dump())
