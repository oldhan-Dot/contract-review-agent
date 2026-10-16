"""合同名称与编号提取；仅分析解析结果，不访问数据库或文件。"""

import json
import re
from dataclasses import dataclass

from app.parsing.ir import ParsedDocument, SourceFormat
from app.structuring.anchor import Anchor
from app.structuring.metadata_models import MetadataField, MetadataKey, MetadataResult

_TITLE_LABEL = re.compile(r"(?:合同名称|协议名称|契约名称)\s*[:：]\s*")
_NUMBER_LABEL = re.compile(r"(?:(?<!原)(?<!主)(?<!关联)合同(?:编号|编码)|^\s*编号)\s*[:：]\s*")
_TITLE_END = re.compile(r"(?:合同|协议|契约)书?(?:[（(][^（）()]{1,20}[）)])?$")
_NUMBER_VALUE = re.compile(r"[^\s，,。；;：:（）()]+")
_PLACEHOLDER = re.compile(r"[_＿\-—\s]*|待填(?:写)?|待补充|暂无|无|N/A", re.I)


@dataclass(frozen=True)
class _Candidate:
    value: str
    start: int
    end: int
    warnings: tuple[str, ...] = ()


def _label_values(document, pattern):
    """标签与值可在同一段；独占一段的标签只尝试紧邻的下一段。"""
    for index, paragraph in enumerate(document.paragraphs):
        for label in pattern.finditer(paragraph.text):
            text, offset = paragraph.text, paragraph.char_start
            begin = label.end()
            if not text[begin:].strip() and index + 1 < len(document.paragraphs):
                following = document.paragraphs[index + 1]
                # 不把下一项「甲方：」或其他标签当作空白栏位的值。
                if re.search(r"[:：]", following.text):
                    continue
                text, offset, begin = following.text, following.char_start, 0
            while begin < len(text) and text[begin].isspace():
                begin += 1
            if begin < len(text):
                yield text, offset, begin


def _titles(document):
    candidates = []
    # DOCX 无可靠封面页码，明确将自动标题搜索限定为前 20 段、前 2000 字符。
    cover = {item.index for item in document.paragraphs[:20] if item.char_start < 2000}
    for text, offset, begin in _label_values(document, _TITLE_LABEL):
        if not any(
            item.index in cover and item.char_start <= offset < item.char_end
            for item in document.paragraphs
        ):
            continue
        raw = re.split(r"[\n，,。；;]|(?:合同编号|合同编码)\s*[:：]", text[begin:], maxsplit=1)[
            0
        ].rstrip()
        if not raw or _PLACEHOLDER.fullmatch(raw):
            continue
        warnings = (
            ()
            if 6 <= len(raw) <= 60 and re.search(r"合同|协议|契约", raw)
            else ("明确名称栏的内容不符合 6～60 字及合同关键词规则，请人工核对",)
        )
        candidates.append(_Candidate(raw, offset + begin, offset + begin + len(raw), warnings))
    for paragraph in document.paragraphs[:20]:
        if paragraph.char_start >= 2000:
            break
        raw = paragraph.text.strip()
        if not 6 <= len(raw) <= 60 or not _TITLE_END.search(raw):
            continue
        if re.search(r"[:：，,。；;]|^第.+条|^\d+[.、)]|^(?:本合同|本协议|根据|依照|双方应)", raw):
            continue
        if re.search(r"本合同|本协议|本契约|应当|以下简称", raw):
            continue
        begin = paragraph.char_start + len(paragraph.text) - len(paragraph.text.lstrip())
        candidates.append(_Candidate(raw, begin, begin + len(raw)))
    return candidates


def _numbers(document):
    candidates = []
    for text, offset, begin in _label_values(document, _NUMBER_LABEL):
        match = _NUMBER_VALUE.match(text, begin)
        if match is None or _PLACEHOLDER.fullmatch(match.group()):
            continue
        raw = match.group()
        valid = re.fullmatch(r"[A-Za-z0-9\-_/]{4,32}", raw) and re.search(r"[A-Za-z0-9]", raw)
        warnings = () if valid else ("合同编号应为 4～32 位字母、数字或 -_/，请人工核对",)
        candidates.append(_Candidate(raw, offset + match.start(), offset + match.end(), warnings))
    return candidates


def _field(document, key, candidates):
    candidates = sorted(set(candidates), key=lambda item: (item.start, -(item.end - item.start)))
    if not candidates:
        return MetadataField(field_key=key, warnings=["未识别该字段，请人工补充"])
    chosen = candidates[0]
    warnings = list(chosen.warnings)
    if len({item.value for item in candidates}) > 1:
        warnings.append("存在多个不同候选，暂取最早位置，请人工核对")
    entries = [
        {"raw": item.value[:200], "start": item.start, "end": item.end} for item in candidates[:10]
    ]
    while entries and len(json.dumps(entries, ensure_ascii=False)) > 1024:
        entries.pop()
    raw = json.dumps(entries, ensure_ascii=False)
    if len(chosen.value) > 512:
        return MetadataField(
            field_key=key,
            field_value_raw=raw,
            warnings=[*warnings, "字段超出存储长度，未保存标准值"],
        )
    return MetadataField(
        field_key=key,
        field_value=chosen.value,
        field_value_raw=raw,
        is_missing=False,
        confidence=0.3 if warnings else 0.9,
        warnings=warnings,
        anchor=Anchor.from_document_range(document, chosen.start, chosen.end),
    )


def extract_contract_metadata(
    document: ParsedDocument, *, task_name: str | None = None
) -> MetadataResult:
    """名称取开头候选、编号取明确标签；仅显式传入任务名时允许名称兜底。"""
    if document.source_format not in (
        SourceFormat.DOCX,
        SourceFormat.PDF_TEXT,
        SourceFormat.PDF_OCR,
        SourceFormat.IMAGE_OCR,
    ):
        raise ValueError("当前合同名称与编号提取支持 DOCX、文本 PDF、扫描 PDF 和图片 OCR 解析结果")
    title = _field(document, MetadataKey.CONTRACT_TITLE, _titles(document))
    # 技术方案允许任务名兜底；这是自动回退，不能伪装成人工修改或原文证据。
    if title.is_missing and task_name and task_name.strip():
        value = task_name.strip()
        if len(value) <= 512:
            title = MetadataField(
                field_key=MetadataKey.CONTRACT_TITLE,
                field_value=value,
                is_missing=False,
                source="auto",
                confidence=0.3,
                warnings=["正文未识别合同名称，使用任务名兜底；无合同原文位置，请人工核对"],
            )
        else:
            title.warnings.append("任务名超出存储长度，未用作名称兜底")
    updates = {
        MetadataKey.CONTRACT_TITLE: title,
        MetadataKey.CONTRACT_NO: _field(document, MetadataKey.CONTRACT_NO, _numbers(document)),
    }
    return MetadataResult(
        file_id=document.file_id,
        items=[
            updates.get(item.field_key, item)
            for item in MetadataResult.empty(document.file_id).items
        ],
    ).validate_against_document(document)
