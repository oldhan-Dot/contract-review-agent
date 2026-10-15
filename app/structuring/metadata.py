"""主体信息提取：定位明确的甲乙方声明，匹配名称和信用代码并保留证据。"""

import json
import re
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date

from app.parsing.ir import ParagraphNode, ParsedDocument, SourceFormat
from app.structuring.anchor import Anchor
from app.structuring.contract_metadata import extract_contract_metadata
from app.structuring.date_metadata import extract_date_metadata
from app.structuring.metadata_models import MetadataField, MetadataKey, MetadataResult
from app.structuring.money_metadata import extract_money_metadata
from app.structuring.splitter import match_clause_no


def extract_metadata(
    document: ParsedDocument, *, today: date | None = None, task_name: str | None = None
) -> MetadataResult:
    """组合名称、编号、主体、金额和日期提取，保留各自的原文证据。"""
    parties = extract_party_metadata(document)
    money = extract_money_metadata(document)
    dates = extract_date_metadata(document, today=today)
    contract = extract_contract_metadata(document, task_name=task_name)
    money_keys = {
        MetadataKey.AMOUNT,
        MetadataKey.CURRENCY,
        MetadataKey.AMOUNT_IN_WORDS,
        MetadataKey.AMOUNT_IN_WORDS_VALUE,
    }
    date_keys = {
        MetadataKey.PERFORM_START_DATE,
        MetadataKey.PERFORM_END_DATE,
        MetadataKey.CONTRACT_PERIOD_DAYS,
        MetadataKey.EFFECTIVE_CONDITION,
        MetadataKey.SIGN_DATE,
    }
    updates = {item.field_key: item for item in money.items if item.field_key in money_keys}
    updates.update({item.field_key: item for item in dates.items if item.field_key in date_keys})
    contract_keys = {MetadataKey.CONTRACT_TITLE, MetadataKey.CONTRACT_NO}
    updates.update(
        {item.field_key: item for item in contract.items if item.field_key in contract_keys}
    )
    return MetadataResult(
        file_id=document.file_id,
        items=[updates.get(item.field_key, item) for item in parties.items],
    ).validate_against_document(document)


USCC_CHARS = "0123456789ABCDEFGHJKLMNPQRTUWXY"
USCC_WEIGHTS = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)
_SPACE = r"[^\S\n]*"
_ROLE = rf"[甲乙]{_SPACE}方"
_ALIASES = r"买方|卖方|供方|需方|采购方|供应商|委托方|受托方|用人单位|劳动者"

# 只接受明确的甲/乙角色；支持「甲方（买方）：」和「买方（甲方）：」。
_PARTY_LABEL = re.compile(
    rf"(?:(?:{_ALIASES}){_SPACE}[（(]{_SPACE})?"
    rf"(?P<role>{_ROLE})(?:{_SPACE}[）)])?"
    rf"(?:{_SPACE}[（(][^()（）\n]{{1,20}}[）)])?"
    rf"(?:{_SPACE}(?:单位名称|公司名称|名称))?{_SPACE}"
    rf"(?:(?P<colon>[:：]){_SPACE}|(?=\n|$))"
)
_CODE = re.compile(
    rf"(?:(?P<role_before>{_ROLE}){_SPACE})?"
    rf"(?:统一社会信用代码|社会信用代码|信用代码|税号)"
    rf"(?:{_SPACE}[（(](?P<role_after>{_ROLE})[）)])?"
    rf"{_SPACE}[:：]?{_SPACE}(?:\n{_SPACE})?"
    r"(?P<value>[0-9A-Za-z_-]{1,64})(?![0-9A-Za-z_-])"
)
_NAME_LABEL = re.compile(
    rf"(?:单位名称|公司名称|主体名称|名称){_SPACE}(?:[:：]{_SPACE}|\n{_SPACE})"
)
_NAME_END = re.compile(
    r"[\n，,。；;]|统一社会信用代码|社会信用代码|信用代码|税号|"
    r"(?:法定代表人|联系地址|注册地址|地址|联系人|电话|联系方式|开户行|银行账号|账号)\s*[:：]"
)
_ORGANIZATION = re.compile(r"公司|中心|部|厂|所|院|集团|个体|事务所|合作社")
_PLACEHOLDER = re.compile(
    r"[_＿\-—…\s]*$|^(?:待填|待填写|待补充|暂无|无|N/A|[（(](?:盖章|签字|签章)[）)])$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _Section:
    """一个明确角色声明到下一个声明/正文标题之间的区段。"""

    role: str
    start: int
    content_start: int
    end: int
    paragraph: ParagraphNode
    bare: bool


@dataclass(frozen=True)
class _Candidate:
    value: str
    start: int
    end: int
    confidence: float = 0.9
    warnings: tuple[str, ...] = ()


def validate_uscc(code: str) -> bool:
    """按技术方案校验 18 位信用代码字符集与校验位，不查询企业登记状态。"""
    normalized = code.strip().upper()
    if len(normalized) != 18 or any(char not in USCC_CHARS for char in normalized):
        return False
    total = sum(
        USCC_CHARS.index(char) * weight for char, weight in zip(normalized[:17], USCC_WEIGHTS)
    )
    return normalized[-1] == USCC_CHARS[(31 - total % 31) % 31]


def _role(text: str) -> str:
    return "a" if "甲" in text else "b"


def _sections(document: ParsedDocument) -> list[_Section]:
    text = document.full_text
    starts = [paragraph.char_start for paragraph in document.paragraphs]
    matches = []
    for match in _PARTY_LABEL.finditer(text):
        prefix = text[text.rfind("\n", 0, match.start()) + 1 : match.start()]
        if re.search(r"(?:统一社会信用代码|社会信用代码|信用代码|税号)\s*[（(]\s*$", prefix):
            continue  # 「信用代码（甲方）：」是代码标签，不是主体名称声明。
        # 「以下简称甲方」等句尾指代不能当作裸角色表头。
        if prefix.rstrip().endswith("以下简称") or not match.group("colon") and prefix.strip():
            continue
        matches.append(match)
    sections = []
    for index, match in enumerate(matches):
        paragraph = document.paragraphs[bisect_right(starts, match.start()) - 1]
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        # 信用代码必须位于主体信息段；进入编号条款或下一正文标题后停止沿用角色。
        for following in document.paragraphs[paragraph.index + 1 :]:
            if following.char_start >= end:
                break
            if following.heading_level is not None or match_clause_no(following.text):
                end = following.char_start
                break
        sections.append(
            _Section(
                role=_role(match.group("role")),
                start=match.start(),
                content_start=match.end(),
                end=end,
                paragraph=paragraph,
                bare=not bool(match.group("colon")),
            )
        )
    return sections


def _ambiguous_tables(sections: list[_Section]) -> set[str]:
    """IR 暂无单元格行列，连续裸甲乙方表头之后的内容不能靠线性顺序猜归属。"""
    return {
        first.paragraph.table_id
        for first, second in zip(sections, sections[1:])
        if first.bare
        and second.bare
        and first.role != second.role
        and first.paragraph.table_id is not None
        and first.paragraph.table_id == second.paragraph.table_id
        and second.paragraph.index == first.paragraph.index + 1
    }


def _name_candidate(document: ParsedDocument, section: _Section) -> _Candidate | None:
    text = document.full_text
    start = section.content_start
    while start < section.end and text[start].isspace():
        start += 1
    label = _NAME_LABEL.match(text, start, section.end)
    if label:
        start = label.end()
        while start < section.end and text[start].isspace():
            start += 1
    stop = _NAME_END.search(text, start, section.end)
    end = stop.start() if stop else section.end
    while end > start and text[end - 1].isspace():
        end -= 1
    if start >= end:
        return None
    value = text[start:end]
    if _PLACEHOLDER.fullmatch(value):
        return None
    warnings = []
    if not _ORGANIZATION.search(value):
        warnings.append("名称未包含常见主体后缀，请核对是否为自然人或名称不完整")
    if len(value) > 60:
        warnings.append("主体名称超过规则常见的 60 字符范围，请人工核对")
    return _Candidate(value, start, end, 0.3 if warnings else 0.9, tuple(warnings))


def _raw_candidates(
    document: ParsedDocument, candidates: list[_Candidate]
) -> tuple[str, list[str]]:
    if len(candidates) == 1:
        raw = document.slice_text(candidates[0].start, candidates[0].end)
        return raw[:1024], ["原文候选超过 1024 字符，展示内容已截断"] if len(raw) > 1024 else []
    kept = []
    for candidate in candidates:
        raw = document.slice_text(candidate.start, candidate.end)
        if len(json.dumps(kept + [raw], ensure_ascii=False)) > 1024:
            break
        kept.append(raw)
    warnings = ["同一字段存在不同候选，按原文位置最靠前、同位置最长的规则选择，请人工核对"]
    if len(kept) < len(candidates):
        warnings.append("候选过多，原文候选列表未全部展开")
    if not kept:
        raw = document.slice_text(candidates[0].start, candidates[0].end)
        return raw[:1024], [*warnings, "候选列表超出字段长度，仅保留首个候选原文"]
    return json.dumps(kept, ensure_ascii=False), warnings


def _field(
    document: ParsedDocument, key: MetadataKey, candidates: list[_Candidate], warnings: list[str]
) -> MetadataField:
    # 位置优先；相同值重复出现时复用第一处证据，不误报为不同候选。
    unique = {}
    for candidate in sorted(
        candidates, key=lambda candidate: (candidate.start, -len(candidate.value))
    ):
        unique.setdefault(candidate.value, candidate)
    ordered = list(unique.values())
    if not ordered:
        return MetadataField(
            field_key=key, is_required=True, warnings=[*warnings, "未识别到可归属的主体信息"]
        )
    chosen = ordered[0]
    raw, extra_warnings = _raw_candidates(document, ordered)
    if len(chosen.value) > 512:
        return MetadataField(
            field_key=key,
            is_required=True,
            field_value_raw=raw,
            warnings=[*warnings, *extra_warnings, "候选值超过 512 字符，未作为标准值保存"],
        )
    return MetadataField(
        field_key=key,
        is_required=True,
        is_missing=False,
        field_value=chosen.value,
        field_value_raw=raw,
        confidence=chosen.confidence,
        anchor=Anchor.from_document_range(document, chosen.start, chosen.end),
        warnings=[*warnings, *chosen.warnings, *extra_warnings],
    )


def extract_party_metadata(document: ParsedDocument) -> MetadataResult:
    """只提取四个主体字段；金额、日期等字段保持未识别，不调用模型或数据库。"""
    if document.source_format not in (SourceFormat.DOCX, SourceFormat.PDF_TEXT):
        raise ValueError("当前主体提取仅支持 DOCX 和文本型 PDF")
    # 1. 划分明确的甲乙方信息区段，保守识别无法还原行列的并列表头。
    sections = _sections(document)
    ambiguous_tables = _ambiguous_tables(sections)
    names = {"a": [], "b": []}
    codes = {"a": [], "b": []}
    warnings = {"a": [], "b": []}
    for section in sections:
        if section.bare and section.paragraph.table_id in ambiguous_tables:
            warnings[section.role].append("甲乙方并列表格缺少行列定位信息，仅使用显式角色标记")
            continue
        candidate = _name_candidate(document, section)
        if candidate is not None:
            names[section.role].append(candidate)

    # 2. 代码优先使用显式甲乙标签；没有标签时仅归入所在主体信息区段。
    starts = [paragraph.char_start for paragraph in document.paragraphs]
    for match in _CODE.finditer(document.full_text):
        before, after = match.group("role_before"), match.group("role_after")
        if before and after and _role(before) != _role(after):
            for role in warnings:
                warnings[role].append("信用代码前后角色标签冲突，未分配该代码")
            continue
        paragraph = document.paragraphs[bisect_right(starts, match.start()) - 1]
        explicit_role = before or after
        owner = _role(explicit_role) if explicit_role else None
        if owner is None and paragraph.table_id not in ambiguous_tables:
            owner_section = next(
                (
                    s
                    for s in reversed(sections)
                    if s.content_start <= match.start() and match.end() <= s.end
                ),
                None,
            )
            owner = owner_section.role if owner_section is not None else None
        if owner is None:
            continue
        raw = match.group("value")
        if _PLACEHOLDER.fullmatch(raw):
            continue
        valid = validate_uscc(raw)
        codes[owner].append(
            _Candidate(
                value=raw.upper(),
                start=match.start("value"),
                end=match.end("value"),
                confidence=0.9 if valid else 0.3,
                warnings=() if valid else ("信用代码长度、字符集或校验位未通过，请人工核对",),
            )
        )

    # 3. 构造四个主体字段，其他目录字段留空；返回前逐项核对原文证据。
    updates = {
        MetadataKey.PARTY_A_NAME: _field(
            document, MetadataKey.PARTY_A_NAME, names["a"], warnings["a"]
        ),
        MetadataKey.PARTY_A_USCC: _field(
            document, MetadataKey.PARTY_A_USCC, codes["a"], warnings["a"]
        ),
        MetadataKey.PARTY_B_NAME: _field(
            document, MetadataKey.PARTY_B_NAME, names["b"], warnings["b"]
        ),
        MetadataKey.PARTY_B_USCC: _field(
            document, MetadataKey.PARTY_B_USCC, codes["b"], warnings["b"]
        ),
    }
    result = MetadataResult.empty(document.file_id)
    result = MetadataResult(
        file_id=document.file_id, items=[updates.get(item.field_key, item) for item in result.items]
    )
    return result.validate_against_document(document)
