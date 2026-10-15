"""解析通道共用的失败原因，执行器后续据此更新任务阻塞状态。"""

from typing import Literal

BlockReason = Literal[
    "ATTACHMENT_MISSING",
    "ATTACHMENT_CORRUPT",
    "ENCRYPTED_FILE",
    "EMPTY_BODY",
    "PARSE_ERROR",
    "UNSUPPORTED_FORMAT",
    "OCR_REQUIRED",
]


class ParseBlocked(Exception):
    def __init__(self, reason: BlockReason, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail
