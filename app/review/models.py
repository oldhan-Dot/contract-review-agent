"""风险项的数据格式与证据约束；不执行审查、不生成数据库主键。"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.review.context import RuleReviewInput
from app.structuring.anchor import Anchor
from app.structuring.metadata_models import MetadataKey
from app.structuring.models import ClauseType


class RiskLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class RiskType(str, Enum):
    CLAUSE_RISK = "CLAUSE_RISK"
    MISSING_CLAUSE = "MISSING_CLAUSE"
    METADATA_RISK = "METADATA_RISK"


class RiskSource(str, Enum):
    RULE = "rule"
    LLM = "llm"
    RULE_LLM = "rule+llm"


class RiskItem(BaseModel):
    """一条待保存风险；clause_index 是本次切分的顺序号，不是 clause 表 ID。"""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    risk_name: str = Field(min_length=1, max_length=128)
    risk_type: RiskType
    risk_level: RiskLevel
    source: RiskSource
    rule_code: str | None = Field(default=None, max_length=16, pattern=r"^R-[A-Z]{2,8}-[0-9]{2}$")
    clause_index: int | None = Field(default=None, gt=0, strict=True)
    clause_type: ClauseType | None = None
    # 元数据风险可能同时涉及多个字段，例如大小写金额不一致。
    metadata_keys: list[MetadataKey] = Field(default_factory=list)
    risk_reason: str = Field(min_length=1)
    legal_basis: str = Field(default="依据：待法务确认", min_length=1)
    # 风险证据保留不超过 500 字的完整原文；不使用首尾拼接的截断证据。
    original_text: str | None = Field(default=None, min_length=1, max_length=500)
    anchor: Anchor | None = None

    @field_validator("risk_name", "risk_reason", "legal_basis")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("风险名称、成因和依据不能是空白")
        return value

    @field_validator("original_text")
    @classmethod
    def _evidence_nonblank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("风险原文不能只有空白")
        return value  # 证据不 strip，字符位置必须保持原样。

    @model_validator(mode="after")
    def _check_shape(self) -> RiskItem:
        # 1. 规则来源必须能追溯到规则编号；纯模型发现不能冒称命中规则。
        if self.source != RiskSource.LLM and self.rule_code is None:
            raise ValueError("规则或合并来源的风险必须记录 rule_code")
        if self.source == RiskSource.LLM and self.rule_code is not None:
            raise ValueError("纯 LLM 来源不能记录规则命中编号")
        if len(self.metadata_keys) != len(set(self.metadata_keys)):
            raise ValueError("风险引用的元数据字段不能重复")

        # 2. 原文与位置必须成对出现，且不能把截断片段当成连续原文。
        if (self.original_text is None) != (self.anchor is None):
            raise ValueError("风险原文与 Anchor 必须同时提供或同时为空")
        if self.anchor is not None and (
            self.anchor.matched_text_truncated or self.original_text != self.anchor.matched_text
        ):
            raise ValueError("风险证据必须与 Anchor 中的完整原文一致")

        # 3. 三种风险有不同的证据要求，缺项风险不能编造定位。
        if self.risk_type == RiskType.CLAUSE_RISK:
            if self.clause_index is None or self.clause_type is None or self.anchor is None:
                raise ValueError("条款风险必须有条款顺序号、类型及原文证据")
            if self.anchor.anchor_type != "clause" or self.metadata_keys:
                raise ValueError("条款风险使用 clause 证据，不引用元数据字段")
        elif self.risk_type == RiskType.MISSING_CLAUSE:
            if self.clause_type is None or self.clause_type == ClauseType.OTHER:
                raise ValueError("缺项风险必须说明缺少哪种明确条款类型")
            if self.clause_index is not None or self.anchor is not None or self.metadata_keys:
                raise ValueError("缺项风险不能有条款顺序号、原文位置或元数据字段")
        else:
            if self.clause_index is not None or not self.metadata_keys:
                raise ValueError("元数据风险必须引用字段且不使用条款顺序号")
            if self.anchor is not None and self.anchor.anchor_type != "metadata":
                raise ValueError("元数据风险使用 metadata 类型的原文证据")
        return self

    def validate_against_input(self, context: RuleReviewInput) -> RiskItem:
        """格式校验后再核对实际原文、条款归属和引用字段，不判断规则是否命中。"""
        if self.anchor is not None:
            self.anchor.validate_against_document(context.document)
        if self.risk_type == RiskType.CLAUSE_RISK:
            clause = next(
                (item for item in context.clauses if item.index == self.clause_index), None
            )
            if clause is None or clause.clause_type != self.clause_type:
                raise ValueError("风险引用的条款不存在或类型不一致")
            if (
                not clause.char_start
                <= self.anchor.global_char_start
                < self.anchor.global_char_end
                <= clause.char_end
            ):
                raise ValueError("风险证据超出引用条款的原文范围")
        elif self.risk_type == RiskType.METADATA_RISK:
            fields = [context.metadata.get(key) for key in self.metadata_keys]
            if any(item is None for item in fields):
                raise ValueError("风险引用的元数据字段未包含在审查输入中")
            if self.anchor is None:
                if all(not item.is_missing for item in fields) and any(
                    item.anchor for item in fields
                ):
                    raise ValueError("已有可定位原文的元数据异常必须保留证据")
            else:
                related = [item.anchor for item in fields if item.anchor is not None]
                start, end = self.anchor.global_char_start, self.anchor.global_char_end
                if not any(
                    anchor.global_char_start <= start < end <= anchor.global_char_end
                    or start <= anchor.global_char_start < anchor.global_char_end <= end
                    for anchor in related
                ):
                    raise ValueError("元数据风险证据与引用字段的原文范围无关")
        return self


class RiskResult(BaseModel):
    """一次审查的风险集合；空列表仅表示当前集合为空，不表示审查已完成。"""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    task_id: int = Field(gt=0, le=2**64 - 1, strict=True)
    file_id: int = Field(gt=0, le=2**64 - 1, strict=True)
    review_version: int = Field(gt=0, le=2**32 - 1, strict=True)
    items: list[RiskItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_scope(self) -> RiskResult:
        seen = set()
        for item in self.items:
            if item.anchor and item.anchor.file_id != self.file_id:
                raise ValueError("风险证据与结果集合的 file_id 不一致")
            if item.rule_code is not None:
                key = (item.rule_code, item.clause_index)
                if key in seen:
                    raise ValueError("同一规则在同一条款或文档上不能重复记录风险")
                seen.add(key)
        return self

    def validate_against_input(self, context: RuleReviewInput) -> RiskResult:
        if (self.task_id, self.file_id, self.review_version) != (
            context.task_id,
            context.file_id,
            context.review_version,
        ):
            raise ValueError("风险结果与规则输入的任务、文件或审查版本不一致")
        for item in self.items:
            item.validate_against_input(context)
        return self
