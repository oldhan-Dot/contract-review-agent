"""中文金额转换的正常值和不能猜测的写法。"""

from decimal import Decimal

import pytest

from app.structuring.cn_num import cn_amount_to_decimal


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("壹佰贰拾万元整", "1200000"),
        ("叁亿伍仟万元", "350000000"),
        ("壹万贰仟叁佰肆拾伍元陆角柒分", "12345.67"),
        ("壹亿零伍佰万元", "105000000"),
        ("五千元", "5000"),
        ("壹佰元", "100"),
        ("叁佰零伍元零角贰分", "305.02"),
        ("貳萬參仟圓整", "23000"),
        ("伍分", ".05"),
        ("十二元", "12"),
    ],
)
def test_conversion(raw, expected):
    assert cn_amount_to_decimal(raw) == Decimal(expected)


@pytest.mark.parametrize(
    "raw",
    ["", "零元", "一千二元", "一万二元", "壹贰元", "壹佰佰元", "负壹元", "一元半", "一角二角"],
)
def test_ambiguous_or_invalid(raw):
    assert cn_amount_to_decimal(raw) is None
