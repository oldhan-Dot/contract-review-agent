"""金额字段提取：只分析解析后的原文，不执行风险规则或数据库写入。"""

import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.parsing.ir import ParsedDocument, SourceFormat
from app.structuring.anchor import Anchor
from app.structuring.cn_num import cn_amount_to_decimal
from app.structuring.metadata_models import MetadataField, MetadataKey, MetadataResult

_CURRENCY = re.compile(
    r"HK\$|(?<![A-Za-z])(?:CNY|USD|EUR|HKD)(?![A-Za-z])|人民币|美元|欧元|港币|港元|[￥¥$€元]", re.I
)
_LABEL = re.compile(
    r"合同总金额|合同总价|合同金额|合同总额|价税合计|总金额|总价|总额|合计|预付款|首付款|定金|尾款|违约金|保证金|单价|服务费"
)
_TOTAL = {
    "合同总金额",
    "合同总价",
    "合同金额",
    "合同总额",
    "价税合计",
    "总金额",
    "总价",
    "总额",
    "合计",
}
_NUMBER = re.compile(
    r"(?<![0-9０-９.,，．])[-+－＋]?[0-9０-９][0-9０-９,，.．]*(?:\s*[万亿])?(?![A-Za-z0-9０-９.,，．])"
)
_WORDS = re.compile(
    r"[零〇一二两三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟萬億貳參陸]+[元圆圓](?:[零〇一二三四五六七八九壹贰叁肆伍陆柒捌玖角分]*)(?:整|正)?"
)


@dataclass(frozen=True)
class _Money:
    start: int
    end: int
    raw: str
    value: Decimal | None
    words: bool
    total: bool
    scope: int
    currency: tuple[str, int, int] | None


def _currency_code(raw: str) -> str:
    token = raw.upper()
    if token in {"HK$", "HKD", "港币", "港元"}:
        return "HKD"
    if token in {"$", "USD", "美元"}:
        return "USD"
    if token in {"€", "EUR", "欧元"}:
        return "EUR"
    return "CNY"


def _numeric(raw: str) -> Decimal | None:
    text = unicodedata.normalize("NFKC", raw).replace(" ", "")
    scale = Decimal(1)
    if text.endswith(("万", "亿")):
        scale = Decimal(10000 if text[-1] == "万" else 100000000)
        text = text[:-1]
    # 不修复错误千分位，不舍入超过两位的小数。
    if not re.fullmatch(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?", text):
        return None
    if len(text) > 40:
        return None
    try:
        return Decimal(text.replace(",", "")) * scale
    except InvalidOperation:
        return None


def _candidates(document: ParsedDocument) -> list[_Money]:
    candidates = []
    # 句号/分号/换行隔开金额语境，避免拿付款条款的币种或大写值配合同总价。
    for scope, sentence in enumerate(re.finditer(r"[^\n；;。]+", document.full_text)):
        text, offset = sentence.group(), sentence.start()
        labels = list(_LABEL.finditer(text))
        currencies = list(_CURRENCY.finditer(text))
        for pattern, words in ((_NUMBER, False), (_WORDS, True)):
            for match in pattern.finditer(text):
                preceding = [label for label in labels if label.end() <= match.start()]
                label = preceding[-1] if preceding else None
                if label and label.group() not in _TOTAL:
                    continue  # 分项金额不作为整份合同总金额。
                nearby = []
                for currency in currencies:
                    if currency.end() <= match.start():
                        gap = text[currency.end() : match.start()]
                    elif currency.start() >= match.end():
                        gap = text[match.end() : currency.start()]
                    else:
                        # 大写金额包含「元」，它本身就是人民币证据。
                        if words and currency.group() == "元":
                            nearby.append((0, currency))
                        continue
                    if re.fullmatch(r"[\s:：（(）)]{0,8}", gap):
                        nearby.append((len(gap), currency))
                selected = (
                    min(nearby, key=lambda item: (item[0], item[1].start()))[1] if nearby else None
                )
                if not words and not selected and not label:
                    continue
                if not selected and label:
                    gap = text[label.end() : match.start()]
                    gap = _WORDS.sub("", gap)
                    gap = _CURRENCY.sub("", gap)
                    gap = re.sub(r"大写|小写|金额", "", gap)
                    if not re.fullmatch(r"[\s:：（(）)]*", gap):
                        continue  # 「合同金额：见第 101 条」中的编号不是金额。
                if text[match.end() :].lstrip().startswith(("%", "％")):
                    continue
                suffix = text[match.end() :].lstrip()
                if (
                    suffix.startswith(("年", "月", "化"))
                    or suffix.startswith("日")
                    and not suffix.startswith("日元")
                ):
                    continue
                raw = match.group()
                value = cn_amount_to_decimal(raw) if words else _numeric(raw)
                currency = (
                    None
                    if selected is None
                    else (
                        _currency_code(selected.group()),
                        offset + selected.start(),
                        offset + selected.end(),
                    )
                )
                candidates.append(
                    _Money(
                        offset + match.start(),
                        offset + match.end(),
                        raw,
                        value,
                        words,
                        label is not None,
                        scope,
                        currency,
                    )
                )
    totals = [candidate for candidate in candidates if candidate.total]
    return sorted(totals or candidates, key=lambda item: (item.start, -(item.end - item.start)))


def _field(document, key, candidate, value, warnings, raw):
    if candidate is None or value is None:
        return MetadataField(field_key=key, warnings=warnings, field_value_raw=raw)
    if len(value) > 512:
        return MetadataField(
            field_key=key, warnings=[*warnings, "金额文字过长，请人工核对"], field_value_raw=raw
        )
    return MetadataField(
        field_key=key,
        field_value=value,
        field_value_raw=raw,
        is_missing=False,
        confidence=0.3 if warnings else 0.9,
        anchor=Anchor.from_document_range(document, candidate.start, candidate.end),
        warnings=warnings,
    )


def extract_money_metadata(document: ParsedDocument) -> MetadataResult:
    """数字金额优先，大写金额兜底；缺失和冲突作为字段提示返回。"""
    if document.source_format not in (
        SourceFormat.DOCX,
        SourceFormat.PDF_TEXT,
        SourceFormat.PDF_OCR,
        SourceFormat.IMAGE_OCR,
    ):
        raise ValueError("当前金额提取支持 DOCX、文本 PDF、扫描 PDF 和图片 OCR 解析结果")
    candidates = _candidates(document)
    # OCR 可能把 100000.00 识别为“1 0 0 0 0 0 . 0 0”。不能把第一个 1 当总金额，
    # 也不静默删空格猜值；保留原识别文字，标为待人工核对。
    fragmented = (
        list(re.finditer(r"[0-9０-９](?:[ \t]+[0-9０-９])+[ \t0-9０-９.．]*", document.full_text))
        if document.source_format in (SourceFormat.PDF_OCR, SourceFormat.IMAGE_OCR)
        else []
    )
    candidates = [
        item
        for item in candidates
        if not any(item.start < match.end() and item.end > match.start() for match in fragmented)
    ]
    numeric = next((item for item in candidates if not item.words), None)
    chinese = next(
        (
            item
            for item in candidates
            if item.words and (numeric is None or item.scope == numeric.scope)
        ),
        None,
    )
    chosen = numeric or chinese
    warnings = []
    if fragmented:
        warnings.append("OCR 数字存在拆分空格，相关候选不作为标准金额，请对照原件核对")
    if chosen is None:
        warnings.append("未识别合同总金额，请人工补充")
    elif chosen.value is None or not Decimal(0) < chosen.value <= Decimal(10**12):
        warnings.append("金额格式无效或超出 (0, 10^12] 范围，请人工核对")
    alternatives = {
        item.value if item.value is not None else item.raw
        for item in candidates
        if item.words == (chosen.words if chosen else False)
    }
    if len(alternatives) > 1:
        warnings.append("发现多个不同的合同金额，暂取最早位置，请人工核对")
    if numeric and chinese and numeric.scope == chinese.scope:
        if numeric.currency and chinese.currency and numeric.currency[0] != chinese.currency[0]:
            warnings.append("数字金额与大写金额币种不同，请人工核对")
        elif (
            numeric.value is not None
            and chinese.value is not None
            and abs(numeric.value - chinese.value) > Decimal("0.01")
        ):
            warnings.append("数字金额与大写金额不一致，请人工核对")
    # 原始候选用完整 JSON 保存；超限时逐个减少，避免截断成无效 JSON。
    entries = [
        {"raw": item.raw[:160], "start": item.start, "end": item.end} for item in candidates[:10]
    ]
    while entries and len(json.dumps(entries, ensure_ascii=False)) > 1024:
        entries.pop()
    raw = json.dumps(entries, ensure_ascii=False) if entries else None
    value = (
        None
        if chosen is None
        else (format(chosen.value, ".2f") if chosen.value is not None else chosen.raw)
    )
    updates = {
        MetadataKey.AMOUNT: _field(document, MetadataKey.AMOUNT, chosen, value, warnings, raw)
    }
    chinese_warnings = []
    if chinese and (chinese.value is None or not Decimal(0) < chinese.value <= Decimal(10**12)):
        chinese_warnings.append("大写金额无法转换或超出范围，请人工核对")
    updates[MetadataKey.AMOUNT_IN_WORDS] = _field(
        document,
        MetadataKey.AMOUNT_IN_WORDS,
        chinese,
        chinese.raw if chinese else None,
        chinese_warnings,
        raw,
    )
    updates[MetadataKey.AMOUNT_IN_WORDS_VALUE] = _field(
        document,
        MetadataKey.AMOUNT_IN_WORDS_VALUE,
        chinese,
        format(chinese.value, ".2f") if chinese and chinese.value is not None else None,
        chinese_warnings,
        raw,
    )
    currency = chosen.currency if chosen else None
    if chosen and currency is None:
        # 仅接受明确的独立币种声明，不从其他付款金额推断币种。
        declaration = re.search(
            r"(?:合同币种|结算币种|币种)\s*[:：]\s*(?P<currency>[^\s；;，,。\n]+)",
            document.full_text,
        )
        if declaration:
            marker = _CURRENCY.fullmatch(declaration.group("currency"))
            if marker:
                currency = (
                    _currency_code(marker.group()),
                    declaration.start("currency"),
                    declaration.end("currency"),
                )
    if currency:
        code, start, end = currency
        updates[MetadataKey.CURRENCY] = MetadataField(
            field_key=MetadataKey.CURRENCY,
            field_value=code,
            field_value_raw=document.full_text[start:end],
            is_missing=False,
            confidence=0.9,
            anchor=Anchor.from_document_range(document, start, end),
        )
    else:
        updates[MetadataKey.CURRENCY] = MetadataField(
            field_key=MetadataKey.CURRENCY, warnings=["未识别所选金额的币种，请人工补充"]
        )
    result = MetadataResult.empty(document.file_id)
    result.items = [updates.get(item.field_key, item) for item in result.items]
    return result.validate_against_document(document)
