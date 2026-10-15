"""字段提取结果的数据格式，与提取算法和数据库 ORM 分开定义。"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.parsing.ir import ParsedDocument
from app.structuring.anchor import Anchor


class MetadataKey(str, Enum):
    CONTRACT_TITLE = "contract_title"
    CONTRACT_NO = "contract_no"
    PARTY_A_NAME = "party_a_name"
    PARTY_A_USCC = "party_a_uscc"
    PARTY_B_NAME = "party_b_name"
    PARTY_B_USCC = "party_b_uscc"
    AMOUNT = "amount"
    CURRENCY = "currency"
    AMOUNT_IN_WORDS = "amount_in_words"
    AMOUNT_IN_WORDS_VALUE = "amount_in_words_value"
    PERFORM_START_DATE = "perform_start_date"
    PERFORM_END_DATE = "perform_end_date"
    CONTRACT_PERIOD_DAYS = "contract_period_days"
    EFFECTIVE_CONDITION = "effective_condition"
    SIGN_DATE = "sign_date"


# 技术方案 6.3.1 的字段目录。这里只约定标签和存储类型，不规定提取正则。
FIELD_DEFINITIONS = {
    MetadataKey.CONTRACT_TITLE: ("合同名称", "string"),
    MetadataKey.CONTRACT_NO: ("合同编号", "string"),
    MetadataKey.PARTY_A_NAME: ("甲方名称", "string"),
    MetadataKey.PARTY_A_USCC: ("甲方统一社会信用代码", "string"),
    MetadataKey.PARTY_B_NAME: ("乙方名称", "string"),
    MetadataKey.PARTY_B_USCC: ("乙方统一社会信用代码", "string"),
    MetadataKey.AMOUNT: ("合同金额", "decimal"),
    MetadataKey.CURRENCY: ("币种", "string"),
    MetadataKey.AMOUNT_IN_WORDS: ("大写金额", "string"),
    MetadataKey.AMOUNT_IN_WORDS_VALUE: ("大写金额数值", "decimal"),
    MetadataKey.PERFORM_START_DATE: ("履行开始日期", "date"),
    MetadataKey.PERFORM_END_DATE: ("履行结束日期", "date"),
    MetadataKey.CONTRACT_PERIOD_DAYS: ("合同期限天数", "integer"),
    MetadataKey.EFFECTIVE_CONDITION: ("生效条件", "string"),
    MetadataKey.SIGN_DATE: ("签署日期", "date"),
}


class MetadataField(BaseModel):
    """一个字段的提取值、原文证据、来源和缺失状态。"""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    field_key: MetadataKey
    field_label: str = Field(min_length=1, max_length=64)
    # 与 contract_metadata 表一致使用字符串；金额用纯数字、日期用 ISO 日期格式。
    # 值的业务合法性由提取模块检查，错误候选可保留原文和警告供法务核对。
    field_value: str | None = Field(default=None, max_length=512)
    field_value_raw: str | None = Field(default=None, max_length=1024)
    value_type: Literal["string", "decimal", "date", "integer"]
    is_required: bool = False  # 是否必提由后续业务规则决定，不在这里猜测。
    is_missing: bool = True
    source: Literal["auto", "manual"] = "auto"
    confidence: float = Field(default=0, ge=0, le=1)
    anchor: Anchor | None = None
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _fill_field_definition(cls, data):
        """校验前按字段名补上固定标签和类型，不修改调用者传入的字典。"""
        if isinstance(data, dict):
            key = data.get("field_key")
            if isinstance(key, str) and key in FIELD_DEFINITIONS:
                label, value_type = FIELD_DEFINITIONS[key]
                return {"field_label": label, "value_type": value_type, **data}
        return data

    @model_validator(mode="after")
    def _check_consistency(self) -> MetadataField:
        if self.value_type != FIELD_DEFINITIONS[self.field_key][1]:
            raise ValueError("字段类型与字段目录不一致")
        if self.is_missing:
            if self.field_value is not None or self.confidence != 0 or self.anchor is not None:
                raise ValueError("未识别字段必须无标准值、置信度为 0 且无原文位置")
        elif self.field_value is None or not self.field_value.strip():
            raise ValueError("已识别字段必须有非空标准值")
        if self.anchor is not None and self.anchor.anchor_type != "metadata":
            raise ValueError("字段证据必须使用 metadata 类型的原文位置")
        return self


class MetadataResult(BaseModel):
    """同一原件的一组字段结果，后续由结构化节点放入共享状态。"""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    file_id: int = Field(gt=0, le=2**64 - 1)
    items: list[MetadataField] = Field(default_factory=list)

    @classmethod
    def empty(cls, file_id: int) -> MetadataResult:
        """准备字段目录，全部标为未识别；不会读取或分析合同。"""
        return cls(file_id=file_id, items=[MetadataField(field_key=key) for key in MetadataKey])

    def get(self, field_key: MetadataKey | str) -> MetadataField | None:
        return next((item for item in self.items if item.field_key == field_key), None)

    @model_validator(mode="after")
    def _check_fields(self) -> MetadataResult:
        keys = [item.field_key for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("同一份结果不能重复记录相同字段")
        if any(item.anchor and item.anchor.file_id != self.file_id for item in self.items):
            raise ValueError("字段证据必须属于同一原件")
        return self

    def validate_against_document(self, document: ParsedDocument) -> MetadataResult:
        """标准化值可以不同于原文写法，但所有已记录的原文证据必须真实存在。"""
        if self.file_id != document.file_id:
            raise ValueError("字段结果与文档的文件 ID 不一致")
        for item in self.items:
            if item.anchor is not None:
                item.anchor.validate_against_document(document)
        return self
