"""Peer multiples table + implied target price at peer median. Pure functions.

"LTM" = latest reported fiscal year (quarterly data is out of scope for v1).
EV = market cap (yfinance) + total debt - cash - ST investments (EDGAR latest FY), same basis as the DCF bridge.
"""

from __future__ import annotations

from statistics import mean, median

MULTIPLES = ["ev_revenue", "ev_ebitda", "pe"]
METRICS = MULTIPLES + ["revenue_growth", "ebit_margin"]


def _div(a, b):
    return None if a is None or b is None or b == 0 else a / b


def company_metrics(ticker: str, hist: dict, market: dict) -> dict:
    """Valuation multiples and operating metrics for one company. Missing inputs -> None + flag."""
    it = hist["items"]
    rev, ebit, da, ni = it["revenue"][-1], it["ebit"][-1], it["da"][-1], it["net_income"][-1]
    prev_rev = it["revenue"][-2] if len(it["revenue"]) > 1 else None
    debt, cash, sti = it["total_debt"][-1], it["cash"][-1], it["short_term_investments"][-1]
    mcap = market.get("market_cap")
    net_debt = None if debt is None or cash is None else debt - cash - (sti or 0)
    ev = None if mcap is None or net_debt is None else mcap + net_debt
    ebitda = None if ebit is None or da is None else ebit + da
    out = {
        "ticker": ticker, "name": market.get("name") or hist.get("entity_name"),
        "fiscal_year": hist["fiscal_years"][-1], "price": market.get("price"), "market_cap": mcap,
        "net_debt": net_debt, "enterprise_value": ev, "revenue": rev, "ebitda": ebitda, "net_income": ni,
        "ev_revenue": _div(ev, rev),
        "ev_ebitda": _div(ev, ebitda) if ebitda and ebitda > 0 else None,
        "pe": _div(mcap, ni) if ni and ni > 0 else None,
        "revenue_growth": None if _div(rev, prev_rev) is None else rev / prev_rev - 1,
        "ebit_margin": _div(ebit, rev),
    }
    out["data_quality"] = [
        {"item": f"comps.{ticker}.{k}", "issue": "missing", "detail": "inputs missing or non-positive denominator"}
        for k in METRICS if out[k] is None
    ]
    return out


def summarize(rows: list[dict]) -> dict:
    """Median and mean of each metric across rows, ignoring None. Count of contributing peers included."""
    stats = {}
    for k in METRICS:
        xs = [r[k] for r in rows if r.get(k) is not None]
        stats[k] = {"median": median(xs) if xs else None, "mean": mean(xs) if xs else None, "n": len(xs)}
    return stats


def implied_prices(target: dict, stats: dict, diluted_shares: float | None) -> dict:
    """Target implied price at peer median for each multiple. EV multiples go through the net-debt bridge."""
    out = {}
    bases = {"ev_revenue": target["revenue"], "ev_ebitda": target["ebitda"], "pe": target["net_income"]}
    for k in MULTIPLES:
        m, base = stats[k]["median"], bases[k]
        if m is None or base is None or not diluted_shares:
            out[k] = None
            continue
        if k == "pe":
            equity = m * base
        else:
            equity = None if target["net_debt"] is None else m * base - target["net_debt"]
        out[k] = None if equity is None else {
            "peer_median_multiple": m, "target_metric": base, "implied_equity_value": equity,
            "implied_price": equity / diluted_shares,
            "upside": equity / diluted_shares / target["price"] - 1 if target.get("price") else None,
        }
    prices = [v["implied_price"] for v in out.values() if v]
    out["range"] = {"low": min(prices), "high": max(prices)} if prices else None
    return out


def build_comps(target_ticker: str, target_hist: dict, target_market: dict,
                peers: dict[str, tuple[dict, dict]], diluted_shares: float | None) -> dict:
    """peers: {ticker: (normalized_hist, market_data)}."""
    target = company_metrics(target_ticker, target_hist, target_market)
    rows = [company_metrics(t, h, m) for t, (h, m) in peers.items()]
    stats = summarize(rows)
    dq = [d for r in [target, *rows] for d in r.pop("data_quality")]
    if not rows:
        dq.append({"item": "comps", "issue": "missing", "detail": "no peers supplied"})
    return {
        "target": target, "peers": rows, "peer_stats": stats,
        "implied": implied_prices(target, stats, diluted_shares),
        "basis": "LTM = latest fiscal year (10-K); EV = market cap + total debt - cash - ST investments",
        "data_quality": dq,
    }
