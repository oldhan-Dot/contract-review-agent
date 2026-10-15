"""规则审查的内存输入，复用结构化结果，不携带数据库会话。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.parsing.ir import ParsedDocument
from app.structuring.metadata_models import MetadataResult
from app.structuring.models import Clause

if TYPE_CHECKING:
    from app.graph.state import ReviewState


# 两处共用同一取值约定：State 传递字段，规则输入执行运行时校验。
# Literal 是类型别名，不是额外的 State 或数据库表。
ContractType = Literal["PURCHASE", "SALES", "SERVICE", "LABOR"]
OurPartyRole = Literal["A", "B"]


class RuleReviewInput(BaseModel):
    """先确认执行标识、原文、条款和字段来自同一份文件，再交给规则。"""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    task_id: int = Field(gt=0, le=2**64 - 1, strict=True)
    file_id: int = Field(gt=0, le=2**64 - 1, strict=True)
    review_version: int = Field(gt=0, le=2**32 - 1, strict=True)
    # 送审人明确填写，不能根据金额、标题或甲乙方排列猜测。
    contract_type: ContractType
    our_party_role: OurPartyRole
    document: ParsedDocument
    clauses: list[Clause] = Field(min_length=1)
    metadata: MetadataResult

    @classmethod
    def from_state(cls, state: ReviewState) -> RuleReviewInput:
        """只读取审查需要的字段，允许 State 中已有摘要、降级等其他信息。"""
        return cls.model_validate({name: state[name] for name in cls.model_fields if name in state})

    @model_validator(mode="after")
    def _check_sources(self) -> RuleReviewInput:
        if self.document.file_id != self.file_id:
            raise ValueError("规则输入的 document 与 file_id 不一致")
        self.metadata.validate_against_document(self.document)
        seen = {}
        for clause in self.clauses:
            if clause.index in seen:
                raise ValueError("规则输入的条款顺序号不能重复")
            clause.validate_against_document(self.document)
            if clause.parent_index is not None:
                parent = seen.get(clause.parent_index)
                if parent is None:
                    raise ValueError("规则输入的父条款必须先于子条款存在")
                if clause.part_no is not None and not (
                    parent.char_start <= clause.char_start < clause.char_end <= parent.char_end
                ):
                    raise ValueError("拆分子条款不能超出父条款范围")
            seen[clause.index] = clause
        return self

    def review_clauses(self) -> list[Clause]:
        """长条款有拆分子段时只审查子段，保留普通层级父条款的自身正文。"""
        split_parents = {item.parent_index for item in self.clauses if item.part_no is not None}
        return [item for item in self.clauses if item.index not in split_parents]
