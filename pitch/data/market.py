"""yfinance wrapper: price, shares, market cap, beta, debt/cash, 52-week range. Cached per day.

Usage: python -m pitch.data.market AAPL
"""

from __future__ import annotations

import json
import math
import sys

from pitch.data.cache import cached_json

FIELDS = {
    "price": ("currentPrice", "regularMarketPrice"),
    "shares_outstanding": ("sharesOutstanding", "impliedSharesOutstanding"),
    "market_cap": ("marketCap",),
    "beta": ("beta",),
    "total_debt": ("totalDebt",),
    "total_cash": ("totalCash",),
    "enterprise_value": ("enterpriseValue",),
    "week52_low": ("fiftyTwoWeekLow",),
    "week52_high": ("fiftyTwoWeekHigh",),
    "trailing_pe": ("trailingPE",),
    "currency": ("currency",),
    "name": ("longName", "shortName"),
    "sector": ("sector",),
    "industry": ("industry",),
}


def _clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def _fetch(ticker: str) -> dict:
    import yfinance as yf  # imported lazily: slow import

    info = yf.Ticker(ticker).info or {}
    if not info or info.get("quoteType") is None:
        raise RuntimeError(f"yfinance returned no data for {ticker!r}")
    out = {"ticker": ticker.upper()}
    for key, candidates in FIELDS.items():
        out[key] = next((_clean(info[c]) for c in candidates if info.get(c) is not None), None)
    return out


def get_market_data(ticker: str) -> dict:
    """Snapshot of market data for a ticker. Missing fields are None (never 0)."""
    data = dict(cached_json("market", ticker, lambda: _fetch(ticker)))
    if data.get("total_debt") is not None and data.get("total_cash") is not None:
        data["net_debt"] = data["total_debt"] - data["total_cash"]
    else:
        data["net_debt"] = None
    return data


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: python -m pitch.data.market TICKER", file=sys.stderr)
        return 2
    print(json.dumps(get_market_data(argv[0].upper()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
