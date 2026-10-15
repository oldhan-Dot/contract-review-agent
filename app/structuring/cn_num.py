"""中文金额转 Decimal；只做数值转换，不搜索合同或判断法律风险。"""

import re
from decimal import Decimal

_DIGITS = dict(zip("零一二三四五六七八九", range(10)))
_DIGITS.update(dict(zip("零壹贰叁肆伍陆柒捌玖", range(10))))
_DIGITS["两"] = 2
_UNITS = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
_TRANSLATE = str.maketrans(
    {"〇": "零", "萬": "万", "億": "亿", "圓": "元", "圆": "元", "貳": "贰", "參": "叁", "陸": "陆"}
)
_DIGIT_PATTERN = "零一二两三四五六七八九壹贰叁肆伍陆柒捌玖"
_FRACTION = re.compile(
    rf"零?(?:(?P<jiao>[{_DIGIT_PATTERN}])角)?零?(?:(?P<fen>[{_DIGIT_PATTERN}])分)?"
)


def _under_ten_thousand(text: str) -> int | None:
    if not text:
        return 0
    if text == "零":
        return 0
    if text.startswith("零"):
        text = text[1:]  # 亿/万之后补位的零不参与乘法。
    if not text:
        return None
    total, pending, last_unit = 0, None, 10000
    padded = False
    for char in text:
        if char in _DIGITS:
            digit = _DIGITS[char]
            if digit == 0:
                if pending is not None or padded:
                    return None
                padded = True
            else:
                if pending is not None:
                    return None  # 「壹贰元」不是明确金额，不能静默取最后一位。
                pending = digit
        elif char in _UNITS:
            unit = _UNITS[char]
            if unit >= last_unit:
                return None
            coefficient = pending
            if coefficient is None:
                if total != 0 or unit != 10:
                    return None
                coefficient = 1  # 「十二元」中的十可省略前面的「一」。
            total += coefficient * unit
            pending, last_unit, padded = None, unit, False
        else:
            return None
    if pending is not None:
        if last_unit > 10 and last_unit != 10000 and not padded:
            return None  # 不猜「一千二」是 1002 还是 1200。
        total += pending
    elif padded:
        return None  # 金额不能停在补位零上。
    return total


def _with_wan(text: str) -> int | None:
    if "万" not in text:
        return _under_ten_thousand(text)
    if text.count("万") != 1:
        return None
    left, right = text.split("万")
    high, low = _under_ten_thousand(left), _under_ten_thousand(right)
    if high is None or high == 0 or low is None:
        return None
    if right and not right.startswith("零") and low < 1000:
        return None  # 「一万二」可能省略千位，必须人工确认。
    return high * 10000 + low


def _integer(text: str) -> int | None:
    if "亿" not in text:
        return _with_wan(text)
    if text.count("亿") != 1:
        return None
    left, right = text.split("亿")
    high, low = _with_wan(left), _with_wan(right)
    if high is None or high == 0 or low is None or low >= 100000000:
        return None
    if right and not right.startswith("零") and low < 10000000:
        return None
    return high * 100000000 + low


def cn_amount_to_decimal(raw: str) -> Decimal | None:
    """支持元、角、分及万/亿；不能确定或非正数时返回 None，不猜测金额。"""
    text = re.sub(r"\s+", "", raw).translate(_TRANSLATE)
    text = re.sub(r"^(?:人民币|大写|[￥¥:：])+", "", text)
    if text.endswith(("整", "正")):
        text = text[:-1]
    if not text or len(text) > 128:
        return None
    if "元" in text:
        if text.count("元") != 1:
            return None
        integer_text, fraction = text.split("元")
        if not integer_text:
            return None
    elif "角" in text or "分" in text:
        integer_text, fraction = "零", text
    else:
        integer_text, fraction = text, ""
    integer = _integer(integer_text)
    if integer is None:
        return None
    cents = 0
    if fraction:
        match = _FRACTION.fullmatch(fraction)
        if match is None or not (match.group("jiao") or match.group("fen")):
            return None
        cents = _DIGITS.get(match.group("jiao"), 0) * 10 + _DIGITS.get(match.group("fen"), 0)
    result = Decimal(integer) + Decimal(cents) / 100
    return result.quantize(Decimal("0.01")) if result > 0 else None
