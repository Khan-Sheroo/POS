from __future__ import annotations

from decimal import Decimal


def money_to_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")

