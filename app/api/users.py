"""当前用户接口：让前端验证所选择的演示身份。"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.security import get_current_user
from app.models import AppUser
from app.schemas.common import ApiResponse, ErrorResponse
from app.schemas.user import UserOut

router = APIRouter(prefix="/api/users", tags=["用户身份"])


@router.get(
    "/me",
    response_model=ApiResponse[UserOut],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
    summary="检查当前演示用户",
)
def read_current_user(user: Annotated[AppUser, Depends(get_current_user)]):
    # 进入接口时格式与身份已经通过校验；响应模型限制输出字段和类型。
    return ApiResponse[UserOut](data=UserOut.model_validate(user))
