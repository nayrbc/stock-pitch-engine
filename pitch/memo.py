"""Fill templates/memo.md from a pitch pack. Numbers only; prose is written later (the [PROSE] slots).

Scenario definitions (mirrored by formulas on the Excel Summary tab):
- Base = average of DCF perpetual-growth and exit-multiple implied prices (= price target).
- Bull = average of the two sensitivity grids one step toward value: WACC - 1 step, g / multiple + 1 step.
- Bear = the same, one step against: WACC + 1 step, g / multiple - 1 step.
- Rating: base upside >= buy_threshold -> BUY, <= sell_threshold -> SELL, else HOLD.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "memo.md"
DEFAULT_MEMO_CFG = {"buy_threshold": 0.15, "sell_threshold": -0.15}


def _avg(*xs):
    return None if any(x is None for x in xs) else sum(xs) / len(xs)


def _upside(p, price):
    return None if p is None or not price else p / price - 1


def scenarios(pack: dict) -> dict:
    """Bull/base/bear prices, price target and rating from a pitch pack. Pure."""
    sens = pack["sensitivity"]
    g, x = sens["wacc_x_terminal_growth"], sens["wacc_x_exit_multiple"]
    k = len(g["rows_wacc"]) // 2
    up, dn = (k - 1, k + 1), (k + 1, k - 1)  # (row, col) for bull / bear
    if k == 0:
        up = dn = (0, 0)
    price = pack["valuation_summary"]["current_price"]
    base = _avg(pack["dcf"]["gordon"]["implied_price"], pack["dcf"]["exit_multiple"]["implied_price"])
    out = {"current_price": price}
    for name, (r, c) in (("bull", up), ("bear", dn)):
        out[name] = _avg(g["implied_price"][r][c], x["implied_price"][r][c])
        out[f"{name}_wacc"] = g["rows_wacc"][r]
        out[f"{name}_g"] = g["cols_terminal_growth"][c]
        out[f"{name}_mult"] = x["cols_exit_multiple"][c]
    out["base"] = out["price_target"] = base
    for name in ("bull", "base", "bear"):
        out[f"{name}_upside"] = _upside(out[name], price)
    out["upside"] = out["base_upside"]
    mcfg = {**DEFAULT_MEMO_CFG, **pack["assumptions"]["config"].get("memo", {})}
    u = out["upside"]
    out["rating"] = ("NOT RATED" if u is None else "BUY" if u >= mcfg["buy_threshold"]
                     else "SELL" if u <= mcfg["sell_threshold"] else "HOLD")
    return out


def _usd(x):
    return "n/a" if x is None else f"${x:,.2f}"


def _big(x):
    if x is None:
        return "n/a"
    return f"${x / 1e9:,.1f}B" if abs(x) >= 1e9 else f"${x / 1e6:,.1f}M"


def _pct(x):
    return "n/a" if x is None else f"{x:.1%}"


def _signed(x):
    return "n/a" if x is None else f"{x:+.1%}"


def _mult(x):
    return "n/a" if x is None else f"{x:.1f}x"


def _rng(r, key):
    return _usd(r[key]) if r else "n/a"


def fields(pack: dict) -> dict[str, str]:
    """All template placeholders as formatted strings."""
    s = scenarios(pack)
    vs, a, w = pack["valuation_summary"], pack["assumptions"]["values"], pack["wacc"]
    it, fys = pack["historicals"]["items"], pack["historicals"]["fiscal_years"]
    comps = pack["comps"]
    rev, prev = it["revenue"][-1], it["revenue"][-2] if len(it["revenue"]) > 1 else None
    dq = pack["data_quality"]
    dq_lines = [f"- {d.get('source', '')}: {d['item']} {d.get('period', '')} {d['issue']}: {d['detail']}".replace("  ", " ")
                for d in dq if d["issue"] != "info"]
    return {
        "name": pack["meta"]["name"] or pack["meta"]["ticker"], "ticker": pack["meta"]["ticker"],
        "date": date.today().isoformat(), "peers": ", ".join(pack["meta"]["peers"]) or "none",
        "rating": s["rating"], "price_target": _usd(s["price_target"]), "current_price": _usd(s["current_price"]),
        "upside": _signed(s["upside"]),
        "wacc": _pct(w["wacc"]), "terminal_growth": _pct(a["terminal_growth"]), "exit_multiple": _mult(a["exit_ev_ebitda"]),
        "dcf_gordon": _usd(vs["dcf_gordon"]["implied_price"]), "dcf_exit": _usd(vs["dcf_exit_multiple"]["implied_price"]),
        "target_ev_ebitda": _mult(comps["target"]["ev_ebitda"]),
        "peer_ev_ebitda": _mult(comps["peer_stats"]["ev_ebitda"]["median"]),
        **{n: _usd(s[n]) for n in ("bull", "base", "bear")},
        **{f"{n}_upside": _signed(s[f"{n}_upside"]) for n in ("bull", "base", "bear")},
        **{f"{n}_wacc": _pct(s[f"{n}_wacc"]) for n in ("bull", "bear")},
        **{f"{n}_g": _pct(s[f"{n}_g"]) for n in ("bull", "bear")},
        **{f"{n}_mult": _mult(s[f"{n}_mult"]) for n in ("bull", "bear")},
        "gordon_low": _rng(vs["dcf_range_gordon_sensitivity"], "low"),
        "gordon_high": _rng(vs["dcf_range_gordon_sensitivity"], "high"),
        "exit_low": _rng(vs["dcf_range_exit_sensitivity"], "low"),
        "exit_high": _rng(vs["dcf_range_exit_sensitivity"], "high"),
        "comps_low": _rng(vs["comps_range"], "low"), "comps_high": _rng(vs["comps_range"], "high"),
        "week52_low": _usd(vs["week52_range"]["low"]), "week52_high": _usd(vs["week52_range"]["high"]),
        "last_fy": fys[-1], "revenue": _big(rev),
        "revenue_growth": _signed(None if rev is None or not prev else rev / prev - 1),
        "ebit_margin": _pct(None if it["ebit"][-1] is None or not rev else it["ebit"][-1] / rev),
        "net_income": _big(it["net_income"][-1]),
        "growth_path": " → ".join(_pct(x) for x in a["revenue_growth"]),
        "margin_path": " → ".join(_pct(x) for x in a["ebit_margin"]),
        "tax_rate": _pct(a["tax_rate"][0]),
        "cost_of_equity": _pct(w["cost_of_equity"]), "rf": _pct(w["risk_free_rate"]), "beta": f"{w['beta']:.2f}",
        "erp": _pct(w["equity_risk_premium"]), "cost_of_debt": _pct(w["pre_tax_cost_of_debt"]),
        "weights": f"{w['weight_equity']:.1%} / {w['weight_debt']:.1%}",
        "tv_pct_gordon": _pct(pack["dcf"]["gordon"]["tv_pct_of_ev"]),
        "tv_pct_exit": _pct(pack["dcf"]["exit_multiple"]["tv_pct_of_ev"]),
        "dq_count": str(len(dq_lines)), "data_quality": "\n".join(dq_lines) or "- none",
    }


def render(pack: dict, template: str) -> str:
    f = fields(pack)

    def sub(m):
        if m.group(1) not in f:
            raise KeyError(f"memo template placeholder {{{{{m.group(1)}}}}} has no value")
        return f[m.group(1)]

    return re.sub(r"\{\{(\w+)\}\}", sub, template)


def write_memo(pack: dict, path: Path, template_path: Path = TEMPLATE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(pack, template_path.read_text()))
    return path
