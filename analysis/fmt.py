"""Display formatting. The page shows these strings verbatim, so every number on
the page exists in results.json exactly as printed."""

from __future__ import annotations

import math


def pct(x: float | None, digits: int = 1) -> str:
    return "n/a" if x is None else f"{x * 100:.{digits}f}%"


def pts(x: float | None) -> str:
    if x is None:
        return "n/a"
    v = x * 100
    return ("+" if v >= 0 else "\u2212") + f"{abs(v):.1f} pts"


def _decimals(x: float) -> int:
    if x == 0:
        return 2
    return max(2, 2 - int(math.floor(math.log10(abs(x)))))


def usd(x: float | None, signed: bool = False) -> str:
    if x is None:
        return "n/a"
    if x == 0:
        return "$0"
    d = _decimals(x)
    s = f"${abs(x):.{d}f}"
    if signed:
        return ("+" if x > 0 else "−") + s
    return ("-" if x < 0 else "") + s


def secs(ms: float | None, signed: bool = False) -> str:
    if ms is None:
        return "n/a"
    s = ms / 1000.0
    if signed:
        return ("+" if s >= 0 else "−") + f"{abs(s):.2f} s"
    return f"{s:.2f} s"


def ms(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0f} ms"


def num(x: float | None, digits: int = 2) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def integer(x: int | float | None) -> str:
    return "n/a" if x is None else f"{int(round(x)):,}"


def interval(iv: dict, f, **kw) -> dict:
    """Attach display strings to an evalkit interval dict."""
    out = dict(iv)
    out["display"] = f(iv.get("point"), **kw)
    lo, hi = iv.get("lo"), iv.get("hi")
    out["display_ci"] = "n/a" if lo is None or hi is None else f"[{f(lo, **kw)}, {f(hi, **kw)}]"
    return out
