"""SEC EDGAR fetchers (ticker->CIK, companyfacts, submissions) with on-disk caching.

Usage: python -m pitch.data.edgar AAPL
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date

import requests
from dotenv import load_dotenv

from pitch.data.cache import cached_json

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

# Ordered fallback list; normalize.py owns the full mapping, this is for the CLI demo.
REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueGoodsNet",
]

_MIN_INTERVAL = 0.12  # SEC fair-access limit is 10 req/s
_last_request = 0.0


class EdgarError(RuntimeError):
    pass


def _user_agent() -> str:
    load_dotenv()
    ua = os.environ.get("EDGAR_USER_AGENT", "").strip()
    if not ua or "example.com" in ua:
        raise EdgarError(
            "EDGAR_USER_AGENT not set. Copy .env.example to .env and set it to 'Your Name your@email'."
        )
    return ua


def _get(url: str) -> dict:
    global _last_request
    wait = _MIN_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    resp = requests.get(url, headers={"User-Agent": _user_agent(), "Accept-Encoding": "gzip, deflate"}, timeout=30)
    _last_request = time.monotonic()
    if resp.status_code == 404:
        raise EdgarError(f"Not found on EDGAR: {url}")
    resp.raise_for_status()
    return resp.json()


def ticker_map() -> dict[str, dict]:
    """{TICKER: {"cik": int, "title": str}} from SEC's company_tickers.json."""
    raw = cached_json("edgar", "_tickers", lambda: _get(TICKERS_URL), verbose=False)
    return {row["ticker"].upper(): {"cik": int(row["cik_str"]), "title": row["title"]} for row in raw.values()}


def get_cik(ticker: str) -> str:
    """10-digit zero-padded CIK for a ticker."""
    entry = ticker_map().get(ticker.upper().replace(".", "-"))
    if entry is None:
        raise EdgarError(f"Ticker {ticker!r} not found in SEC ticker list")
    return f"{entry['cik']:010d}"


def get_company_facts(ticker: str) -> dict:
    """Full XBRL companyfacts JSON for a ticker (cached per day)."""
    cik = get_cik(ticker)
    return cached_json("edgar", f"{ticker}_companyfacts", lambda: _get(FACTS_URL.format(cik=cik)))


def get_submissions(ticker: str) -> dict:
    """Submissions JSON (includes SIC code, fiscal year end, filing index), cached per day."""
    cik = get_cik(ticker)
    return cached_json("edgar", f"{ticker}_submissions", lambda: _get(SUBMISSIONS_URL.format(cik=cik)))


def get_sic(ticker: str) -> int | None:
    sic = get_submissions(ticker).get("sic")
    return int(sic) if sic else None


def annual_values(facts: dict, tag: str, unit: str = "USD", taxonomy: str = "us-gaap") -> dict[str, dict]:
    """Annual (10-K, ~12-month duration) values for one tag, keyed by period end date.

    For duplicate periods (comparatives restated in later 10-Ks) the most recently filed value wins.
    Instant (balance-sheet) facts have no 'start' and are kept as-is.
    """
    try:
        rows = facts["facts"][taxonomy][tag]["units"][unit]
    except KeyError:
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        if not r.get("form", "").startswith("10-K"):
            continue
        if "start" in r:
            days = (date.fromisoformat(r["end"]) - date.fromisoformat(r["start"])).days
            if not 350 <= days <= 380:
                continue
        prev = out.get(r["end"])
        if prev is None or r["filed"] > prev["filed"]:
            out[r["end"]] = {**r, "tag": tag}
    return out


def annual_series(facts: dict, tags: list[str], years: int = 5, unit: str = "USD") -> list[dict]:
    """Last `years` fiscal years for a line item, using the first tag (in order) that reports each period."""
    merged: dict[str, dict] = {}
    for tag in tags:
        for end, row in annual_values(facts, tag, unit).items():
            merged.setdefault(end, row)
    return [merged[end] for end in sorted(merged)[-years:]]


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: python -m pitch.data.edgar TICKER", file=sys.stderr)
        return 2
    ticker = argv[0].upper()
    try:
        facts = get_company_facts(ticker)
    except EdgarError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    series = annual_series(facts, REVENUE_TAGS)
    if not series:
        print(f"No annual revenue found for {ticker}", file=sys.stderr)
        return 1
    print(f"{facts.get('entityName', ticker)} ({ticker}) — annual revenue, SEC EDGAR 10-K")
    print(f"{'FY':<8}{'Period end':<12}{'Revenue ($M)':>14}  Tag")
    for r in series:
        print(f"{'FY' + r['end'][:4]:<8}{r['end']:<12}{r['val'] / 1e6:>14,.0f}  {r['tag']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
