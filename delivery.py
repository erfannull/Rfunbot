from __future__ import annotations

import re
from dataclasses import dataclass


PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
MULTIPLIERS = {
    "میلیاردتومان": 1_000_000_000,
    "میلیاردتومن": 1_000_000_000,
    "میلیونتومان": 1_000_000,
    "میلیونتومن": 1_000_000,
    "ملیونتومان": 1_000_000,
    "ملیونتومن": 1_000_000,
    "هزارتومان": 1_000,
    "هزارتومن": 1_000,
    "هزار": 1_000,
    "تومان": 1,
    "تومن": 1,
    "میلیون": 1_000_000,
    "ملیون": 1_000_000,
    "میلیارد": 1_000_000_000,
}


@dataclass(frozen=True)
class ParsedAmount:
    title: str
    amount: int


def normalize_digits(value: str) -> str:
    return value.translate(PERSIAN_DIGITS).replace("٬", ",")


def format_rial(amount: int) -> str:
    return f"{amount:,}".replace(",", "،") + " تومان"


def parse_amount_message(text: str) -> ParsedAmount | None:
    clean = normalize_digits(text.strip())
    if not clean:
        return None

    pattern = re.compile(
        r"(?P<number>\d+(?:[,.]\d+)?)\s*(?P<unit>میلیارد\s*تومان|میلیارد\s*تومن|میلیون\s*تومان|میلیون\s*تومن|ملیون\s*تومان|ملیون\s*تومن|هزار\s*تومان|هزار\s*تومن|میلیارد|میلیون|ملیون|هزار|تومان|تومن)?"
    )
    matches = list(pattern.finditer(clean))
    if not matches:
        return None

    match = matches[-1]
    number_raw = match.group("number").replace(",", ".")
    unit_raw = (match.group("unit") or "").replace(" ", "")
    try:
        number = float(number_raw)
    except ValueError:
        return None

    multiplier = MULTIPLIERS.get(unit_raw, 1)
    amount = int(number * multiplier)
    if amount <= 0:
        return None

    title = (clean[: match.start()] + clean[match.end() :]).strip(" -،,")
    if not title:
        title = "مورد بدون عنوان"

    return ParsedAmount(title=title, amount=amount)
