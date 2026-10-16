"""日期与期限提取；保留观察值和证据，不推测合同未写明的日期。"""

import json
import re
from dataclasses import dataclass
from datetime import date

from app.parsing.ir import ParsedDocument, SourceFormat
from app.structuring.anchor import Anchor
from app.structuring.metadata_models import MetadataField, MetadataKey, MetadataResult

# 两种形式分开定义，避免把 2026-01/02 等混合分隔符当成合法格式。
_DATE = (
    r"(?<![0-9０-９])(?:[0-9０-９]{4}\s*年\s*[0-9０-９]{1,2}\s*月\s*"
    r"[0-9０-９]{1,2}\s*日|[0-9０-９]{4}(?P<sep>[-/.])[0-9０-９]{1,2}"
    r"(?P=sep)[0-9０-９]{1,2})(?![0-9０-９])"
)
# 嵌入多个日期时必须使用不同的正则组名。
_DATE_END = _DATE.replace("sep", "end_sep")
_TERM = (
    r"合同履行期限|合同有效期|合同期限|履行期限|履约期限|服务期限|服务期|"
    r"(?<!报价)(?<!投标)(?<!质保)(?<!保修)(?<!优惠)(?<!授权)有效期"
)
_START_LABEL = r"履行开始日期|履行起始日期|履约开始日期|合同开始日期|合同起始日期|服务开始日期"
_END_LABEL = r"履行结束日期|履行截止日期|履约结束日期|合同到期日期|合同终止日期|服务结束日期"
_SIGN_LABEL = r"签署日期|签订日期|签约日期|签订时间|签署时间|签约时间"
_RANGE = re.compile(
    rf"(?:{_TERM}|本合同)\s*[:：]?\s*(?:为\s*)?(?:自|从)?\s*"
    rf"(?P<start>{_DATE})\s*起?\s*(?:至|到|—|~|～|－|-)\s*(?P<end>{_DATE_END})"
)
_START = re.compile(rf"(?:{_START_LABEL})\s*[:：]?\s*(?P<value>{_DATE})")
_END = re.compile(rf"(?:{_END_LABEL})\s*[:：]?\s*(?P<value>{_DATE})")
_OPEN_START = re.compile(rf"(?:{_TERM})\s*[:：]?\s*(?:自|从)\s*(?P<value>{_DATE})\s*(?:起|开始)")
_OPEN_END = re.compile(rf"(?:{_TERM})\s*[:：]?\s*(?:至|到)\s*(?P<value>{_DATE})\s*(?:止|届满)?")
_SIGN = re.compile(rf"(?:{_SIGN_LABEL})\s*[:：]?\s*(?P<value>{_DATE})")
_DATE_PATTERN = re.compile(_DATE)
_DURATION = re.compile(
    r"(?<![\d\-－])(?P<number>[-－]?[0-9０-９]{1,4})\s*(?:个)?\s*"
    r"(?P<unit>工作日|自然日|日|天|月|年)(?!\d)"
)
_COMPONENT = re.compile(r"付款|支付|交付|交货|质保|保修|保证金|通知|验收|提前|续约|续签|自动延长")


@dataclass(frozen=True)
class _Candidate:
    value: str
    start: int
    end: int
    warnings: tuple[str, ...] = ()


def _parse_date(raw: str) -> date | None:
    parts = re.findall(r"[0-9０-９]+", raw)
    try:
        return date(*(int(part) for part in parts))
    except (ValueError, TypeError):
        return None


def _date_candidate(match, group: str) -> _Candidate:
    raw = match.group(group)
    parsed = _parse_date(raw)
    return _Candidate(
        value=parsed.isoformat() if parsed else raw,
        start=match.start(group),
        end=match.end(group),
        warnings=() if parsed else ("日期不是合法公历日期，请人工核对",),
    )


def _raw_candidates(document, candidates):
    entries = [
        {
            "raw": document.full_text[item.start : item.end][:200],
            "start": item.start,
            "end": item.end,
        }
        for item in candidates[:10]
    ]
    while entries and len(json.dumps(entries, ensure_ascii=False)) > 1024:
        entries.pop()
    return json.dumps(entries, ensure_ascii=False) if entries else None


def _field(document, key, candidates, *, last=False, warnings=()):
    # 重复模式可定位到同一日期；去重后按原文位置排序，签署日期取最后一个。
    candidates = sorted(set(candidates), key=lambda item: (item.start, -(item.end - item.start)))
    notices = list(warnings)
    if not candidates:
        return MetadataField(field_key=key, warnings=[*notices, "未识别该字段，请人工补充"])
    chosen = candidates[-1] if last else candidates[0]
    notices.extend(chosen.warnings)
    if len({item.value for item in candidates}) > 1:
        notices.append(
            "存在多个不同候选，暂取最后位置，请人工核对"
            if last
            else "存在多个不同候选，暂取最早位置，请人工核对"
        )
    return MetadataField(
        field_key=key,
        field_value=chosen.value,
        field_value_raw=_raw_candidates(document, candidates),
        is_missing=False,
        confidence=0.3 if notices else 0.9,
        warnings=notices,
        anchor=Anchor.from_document_range(document, chosen.start, chosen.end),
    )


def _signature_dates(document):
    candidates = [_date_candidate(match, "value") for match in _SIGN.finditer(document.full_text)]
    # DOCX 没有可靠页码：只在明确签章段落及紧邻的日期行中识别签署日期。
    for index, paragraph in enumerate(document.paragraphs):
        if not re.search(
            r"[甲乙]方.{0,12}(?:签字|签章|盖章)|法定代表人.{0,6}(?:签字|签章)", paragraph.text
        ):
            continue
        for match in _DATE_PATTERN.finditer(paragraph.text):
            item = _date_candidate(match, 0)
            candidates.append(
                _Candidate(
                    item.value,
                    paragraph.char_start + item.start,
                    paragraph.char_start + item.end,
                    item.warnings,
                )
            )
        if index + 1 < len(document.paragraphs):
            following = document.paragraphs[index + 1]
            match = re.fullmatch(rf"\s*(?:日期\s*[:：]?\s*)?(?P<value>{_DATE})\s*", following.text)
            if match:
                item = _date_candidate(match, "value")
                candidates.append(
                    _Candidate(
                        item.value,
                        following.char_start + item.start,
                        following.char_start + item.end,
                        item.warnings,
                    )
                )
    return candidates


def _durations(document):
    candidates, notices = [], []
    for sentence in re.finditer(r"[^\n；;。]+", document.full_text):
        text, offset = sentence.group(), sentence.start()
        contexts = list(re.finditer(_TERM + r"|本合同自(?:签订|签署|生效)之日起", text))
        dates = list(_DATE_PATTERN.finditer(text))
        for match in _DURATION.finditer(text):
            if any(item.start() <= match.start() < item.end() for item in dates):
                continue
            preceding = [item for item in contexts if item.end() <= match.start()]
            if not preceding:
                continue
            gap = text[preceding[-1].end() : match.start()]
            if len(gap) > 80 or _COMPONENT.search(gap):
                continue
            number, unit = int(match.group("number").replace("－", "-")), match.group("unit")
            candidates.append((number, unit, offset + match.start(), offset + match.end()))
    return candidates, notices


def _period_days(document, start, end):
    durations, notices = _durations(document)
    first = _parse_date(start.field_value) if not start.is_missing else None
    last = _parse_date(end.field_value) if not end.is_missing else None
    date_days = (last - first).days if first and last and first <= last else None
    if first and last and first > last:
        notices.append("起止日期先后有误，未依日期推导期限")
    candidates = []
    observed = [
        _Candidate(document.full_text[begin:finish], begin, finish)
        for _, _, begin, finish in durations
    ]
    for number, unit, begin, finish in durations:
        if number <= 0:
            candidates.append(
                _Candidate(str(number), begin, finish, ("文字合同期限必须为正数，请人工核对",))
            )
            continue
        if unit == "工作日":
            notices.append("工作日需结合节假日日历，未换算成自然日")
            continue
        days = None
        if unit in {"日", "天", "自然日"}:
            days = number
        elif first:
            # 月和年按公历同日计算；如 1 月 31 日加一个月不存在，不擅自改成月末。
            months = number * (12 if unit == "年" else 1)
            year, month = divmod(first.year * 12 + first.month - 1 + months, 12)
            try:
                days = (date(year, month + 1, first.day) - first).days
            except ValueError:
                pass
        if days is None:
            notices.append("月/年期限缺少可用起始日期或对应公历日期不存在，未换算天数")
        else:
            candidates.append(_Candidate(str(days), begin, finish))
    if date_days is not None:
        notices.extend(warning for item in candidates for warning in item.warnings)
        if any(int(item.value) != date_days for item in candidates):
            notices.append("文字期限与起止日期天数不一致，以日期差为准，请核对是否包含首尾日")
        # 派生天数的证据覆盖两端日期；不生成原文未写明的起止日期。
        begin = min(start.anchor.global_char_start, end.anchor.global_char_start)
        finish = max(start.anchor.global_char_end, end.anchor.global_char_end)
        derived = _Candidate(str(date_days), begin, finish)
        field = _field(document, MetadataKey.CONTRACT_PERIOD_DAYS, [derived], warnings=notices)
        if observed:
            field.field_value_raw = _raw_candidates(document, [derived, *observed])
        return field
    field = _field(document, MetadataKey.CONTRACT_PERIOD_DAYS, candidates, warnings=notices)
    if observed:
        field.field_value_raw = _raw_candidates(document, observed)
    return field


def extract_date_metadata(document: ParsedDocument, *, today: date | None = None) -> MetadataResult:
    """提取五个日期/期限字段；today 可注入，使五年校验可重复验证。"""
    if document.source_format not in (
        SourceFormat.DOCX,
        SourceFormat.PDF_TEXT,
        SourceFormat.PDF_OCR,
        SourceFormat.IMAGE_OCR,
    ):
        raise ValueError("当前日期提取支持 DOCX、文本 PDF、扫描 PDF 和图片 OCR 解析结果")
    today = today or date.today()
    # 1. 先定位明确起止标签和有合同期限语境的日期区间。
    starts = [
        _date_candidate(match, "value")
        for pattern in (_START, _OPEN_START)
        for match in pattern.finditer(document.full_text)
    ]
    ends = [
        _date_candidate(match, "value")
        for pattern in (_END, _OPEN_END)
        for match in pattern.finditer(document.full_text)
    ]
    for match in _RANGE.finditer(document.full_text):
        starts.append(_date_candidate(match, "start"))
        ends.append(_date_candidate(match, "end"))
    start = _field(document, MetadataKey.PERFORM_START_DATE, starts)
    end = _field(document, MetadataKey.PERFORM_END_DATE, ends)
    first = _parse_date(start.field_value) if not start.is_missing else None
    last = _parse_date(end.field_value) if not end.is_missing else None
    if first and last and first > last:
        for item in (start, end):
            item.warnings.append("履行开始日期晚于结束日期，请人工核对")
            item.confidence = 0.3

    # 2. 签署区取最后一个日期；历史日期只提示，不丢弃合同原文。
    sign = _field(document, MetadataKey.SIGN_DATE, _signature_dates(document), last=True)
    signed = _parse_date(sign.field_value) if not sign.is_missing else None
    try:
        cutoff = today.replace(year=today.year - 5)
    except ValueError:
        cutoff = today.replace(year=today.year - 5, day=28)
    if signed and signed < cutoff:
        sign.warnings.append("签署日期早于五年前，请人工核对")
        sign.confidence = 0.3

    # 3. 生效条件按技术方案保留含「生效」的段落，超长时保留含关键词的 200 字。
    effective = []
    for paragraph in document.paragraphs:
        if "生效" not in paragraph.text:
            continue
        begin = max(0, paragraph.text.index("生效") - 100) if len(paragraph.text) > 200 else 0
        raw = paragraph.text[begin : begin + 200]
        effective.append(
            _Candidate(
                raw,
                paragraph.char_start + begin,
                paragraph.char_start + begin + len(raw),
                ("生效条件段落过长，仅截取含关键词的 200 字，请核对完整段落",)
                if len(paragraph.text) > 200
                else (),
            )
        )
    updates = {
        MetadataKey.PERFORM_START_DATE: start,
        MetadataKey.PERFORM_END_DATE: end,
        MetadataKey.SIGN_DATE: sign,
        MetadataKey.CONTRACT_PERIOD_DAYS: _period_days(document, start, end),
        MetadataKey.EFFECTIVE_CONDITION: _field(
            document, MetadataKey.EFFECTIVE_CONDITION, effective
        ),
    }
    result = MetadataResult.empty(document.file_id)
    return MetadataResult(
        file_id=document.file_id, items=[updates.get(item.field_key, item) for item in result.items]
    ).validate_against_document(document)
