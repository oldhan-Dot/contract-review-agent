"""条款切分后的统一结构，与数据库 clause 表映射分开。"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.parsing.ir import ParsedDocument


class ClauseType(str, Enum):
    """PRD 第 8.1 节的十二类条款及 OTHER；这里只定义类型，不执行归类。"""

    PARTY_INFO = "PARTY_INFO"  # 主体信息。
    SUBJECT_MATTER = "SUBJECT_MATTER"  # 标的物。
    PAYMENT = "PAYMENT"  # 金额与付款。
    DELIVERY_ACCEPTANCE = "DELIVERY_ACCEPTANCE"  # 交付与验收。
    LIABILITY = "LIABILITY"  # 违约责任。
    CONFIDENTIALITY = "CONFIDENTIALITY"  # 保密义务。
    IP = "IP"  # 知识产权。
    DATA_SECURITY = "DATA_SECURITY"  # 数据安全。
    DISPUTE = "DISPUTE"  # 争议解决与管辖。
    TERM_EFFECTIVE = "TERM_EFFECTIVE"  # 合同期限与生效。
    TERMINATION = "TERMINATION"  # 解除与终止。
    FORCE_MAJEURE = "FORCE_MAJEURE"  # 不可抗力。
    OTHER = "OTHER"


SplitStrategy = Literal["NUMBER", "STYLE", "PSEUDO_HEADING", "BLANK_LINE", "LENGTH_FALLBACK"]


class Clause(BaseModel):
    """一条待审查条款；供切分器输出、分类器及规则引擎读取。"""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    index: int = Field(ge=1)  # 条款顺序号从 1 开始；段落顺序号仍从 0 开始。
    clause_no: str | None = Field(default=None, min_length=1)  # 原文编号，如「第一条」。
    level: int = Field(ge=0)  # 无编号的兜底段为 0，其余层级由切分器识别。
    title: str | None = None
    text: str = Field(min_length=1)  # 去编号前缀及首尾空白后的正文，供审查使用。

    # 首尾段落编号都包含在内；字符区间左闭右开，指向含编号的原文。
    paragraph_index_start: int = Field(ge=0)
    paragraph_index_end: int = Field(ge=0)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    split_strategy: SplitStrategy

    parent_index: int | None = Field(default=None, ge=1)
    part_no: int | None = Field(default=None, ge=1)  # 长条款拆分出的子条款序号。

    # 分类器填充以下字段；初始 OTHER 表示未归类，不代表已经完成内容判断。
    clause_type: ClauseType = ClauseType.OTHER
    clause_type_secondary: ClauseType | None = None
    type_confidence: float = Field(default=0, ge=0, le=1)
    classify_rule_hit: str | None = None

    @model_validator(mode="after")
    def _check_ranges(self) -> Clause:
        if not self.text.strip():
            raise ValueError("条款正文不能为空白")
        if self.char_end <= self.char_start:
            raise ValueError("条款字符区间必须非空")
        if self.paragraph_index_end < self.paragraph_index_start:
            raise ValueError("条款结束段落不能早于开始段落")
        if (self.page_start is None) != (self.page_end is None):
            raise ValueError("条款起止页码必须同时填写或同时为空")
        if self.page_start is not None and self.page_end < self.page_start:
            raise ValueError("条款结束页不能早于开始页")
        if self.parent_index == self.index:
            raise ValueError("条款不能将自己作为父条款")
        if self.part_no is not None and self.parent_index is None:
            raise ValueError("拆分子条款必须记录父条款编号")
        if (
            self.clause_type_secondary is not None
            and self.clause_type_secondary == self.clause_type
        ):
            raise ValueError("条款次类型不能与主类型相同")
        return self

    def is_cross_page(self) -> bool:
        return self.page_start is not None and self.page_end != self.page_start

    def source_text(self, document: ParsedDocument) -> str:
        """取回含编号的原文，定位时使用它，不能拿清洗后的 text 代替。"""
        return document.slice_text(self.char_start, self.char_end)

    def validate_against_document(self, document: ParsedDocument) -> Clause:
        """切分后显式调用：检查条款区间、正文和段落位置是否对应实际原文。"""
        first = document.by_index(self.paragraph_index_start)
        last = document.by_index(self.paragraph_index_end)
        if first is None or last is None:
            raise ValueError("条款引用的段落不存在")
        if not (first.char_start <= self.char_start < first.char_end):
            raise ValueError("条款起点不在声明的开始段落中")
        if not (last.char_start < self.char_end <= last.char_end):
            raise ValueError("条款终点不在声明的结束段落中")
        if (self.page_start, self.page_end) != (first.page_no, last.page_no):
            raise ValueError("条款页码与原文段落不一致")

        body = self.source_text(document).strip()
        if self.clause_no is not None:
            if not body.startswith(self.clause_no):
                raise ValueError("条款编号与原文前缀不一致")
            body = body[len(self.clause_no) :].strip()
        if body != self.text:
            raise ValueError("条款正文与原文去编号后的内容不一致")
        return self
