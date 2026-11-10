"""FCFF DCF: assumption resolution, WACC, projection, terminal value (Gordon + exit multiple),
EV -> equity bridge, sensitivity grids. All functions are pure (dict in, dict out).

Conventions:
- Unlevered FCF = EBIT*(1-t) + D&A - capex - dNWC, with dNWC = nwc_pct * (rev_t - rev_{t-1}).
- Cash flows discounted at (t - 0.5) under mid-year convention, else t.
- Terminal values (both methods) sit at the end of year N and are discounted N full years.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from pitch.normalize import operating_nwc

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


class DCFError(ValueError):
    pass


# ---------------------------------------------------------------- config

def _deep_merge(base: dict, over: dict) -> dict:
    out = deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(ticker: str | None = None, config_dir: Path = CONFIG_DIR) -> dict:
    """config/assumptions.yaml deep-merged with config/<TICKER>.yaml if present."""
    cfg = yaml.safe_load((config_dir / "assumptions.yaml").read_text())
    if ticker:
        override = config_dir / f"{ticker.upper()}.yaml"
        if override.exists():
            cfg = _deep_merge(cfg, yaml.safe_load(override.read_text()) or {})
    return cfg


# ---------------------------------------------------------------- historical ratios

def _clamp(x: float, bounds) -> float:
    lo, hi = bounds
    return min(max(x, lo), hi)


def _ratios(num: list, den: list) -> list[float]:
    return [n / d for n, d in zip(num, den) if n is not None and d not in (None, 0)]


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    ys, k = sorted(xs), len(xs) // 2
    return ys[k] if len(ys) % 2 else (ys[k - 1] + ys[k]) / 2


# Drivers that use the median rather than the mean (robust to one-off charges in a single year).
MEDIAN_DRIVERS = {"ebit_margin"}


def historical_ratios(hist: dict) -> dict:
    """Historical drivers from normalized data. Each value is None if no usable years."""
    it = hist["items"]
    rev = it["revenue"]
    pairs = [(rev[i], rev[j]) for i, j in zip(range(len(rev)), range(1, len(rev)))]
    valid_rev = [(i, r) for i, r in enumerate(rev) if r]
    cagr = None
    if len(valid_rev) >= 2:
        (i0, r0), (i1, r1) = valid_rev[0], valid_rev[-1]
        if r0 > 0 and r1 > 0:
            cagr = (r1 / r0) ** (1 / (i1 - i0)) - 1
    tax_rates = [t / p for t, p in zip(it["tax"], it["pretax_income"]) if t is not None and p and p > 0]
    nwc = operating_nwc(hist)
    return {
        "revenue_cagr": cagr,
        "revenue_growth_yoy": [None if None in (a, b) or not a else b / a - 1 for a, b in pairs],
        "ebit_margin": _median(_ratios(it["ebit"], rev)),
        "tax_rate": _mean(tax_rates),
        "capex_pct_revenue": _mean(_ratios(it["capex"], rev)),
        "da_pct_revenue": _mean(_ratios(it["da"], rev)),
        "nwc_pct_revenue": _mean(_ratios(nwc, rev)),
        "years_used": {
            "ebit_margin": len(_ratios(it["ebit"], rev)),
            "tax_rate": len(tax_rates),
            "capex_pct_revenue": len(_ratios(it["capex"], rev)),
            "da_pct_revenue": len(_ratios(it["da"], rev)),
            "nwc_pct_revenue": len(_ratios(nwc, rev)),
        },
    }


# ---------------------------------------------------------------- assumptions

def _path(value, n: int) -> list[float]:
    if isinstance(value, (int, float)):
        return [float(value)] * n
    if isinstance(value, list) and len(value) == n:
        return [float(v) for v in value]
    raise DCFError(f"Assumption path must be a number or a list of {n} numbers, got {value!r}")


def fade_path(g0: float, g_term: float, n: int) -> list[float]:
    """Linear fade from g0 in year 1 to g_term in year n."""
    return [g0 + (g_term - g0) * t / (n - 1) for t in range(n)] if n > 1 else [g0]


def resolve_assumptions(hist: dict, cfg: dict) -> dict:
    """Projection drivers: config value if set, else clamped historical average (median for EBIT margin). Records the source of each.

    Returns {"values": {...}, "sources": {...}, "data_quality": [...]}; raises DCFError if a driver has
    neither a config value nor any historical data.
    """
    n = cfg["projection_years"]
    d, clamps = cfg["dcf"], cfg["clamps"]
    h = historical_ratios(hist)
    values, sources, dq = {}, {}, []
    g_term = d["terminal_growth"]

    # Revenue growth path
    if d.get("revenue_growth") is not None:
        values["revenue_growth"] = _path(d["revenue_growth"], n)
        sources["revenue_growth"] = "config"
    elif d.get("revenue_growth_start") is not None:
        g0 = float(d["revenue_growth_start"])
        values["revenue_growth"] = fade_path(g0, g_term, n)
        sources["revenue_growth"] = f"config start {g0:.2%}, linear fade to terminal growth {g_term:.2%}"
    else:
        cagr, last = h["revenue_cagr"], h["revenue_growth_yoy"][-1] if h["revenue_growth_yoy"] else None
        if cagr is None:
            raise DCFError("revenue_growth: no config value and historical CAGR unavailable")
        if last is None:
            raw, desc = cagr, f"historical CAGR {cagr:.2%}"
            dq.append({"item": "revenue_growth", "issue": "missing",
                       "detail": "last-year growth unavailable; starting growth uses CAGR only"})
        else:
            raw = (last + cagr) / 2
            desc = f"avg of last-year growth {last:.2%} and CAGR {cagr:.2%} = {raw:.2%}"
        g0 = _clamp(raw, clamps["revenue_growth"])
        values["revenue_growth"] = fade_path(g0, g_term, n)
        sources["revenue_growth"] = (
            desc + (f" (clamped to {g0:.2%})" if g0 != raw else "") + f", linear fade to terminal growth {g_term:.2%}"
        )
        if g0 != raw:
            dq.append({"item": "revenue_growth", "issue": "clamped", "detail": sources["revenue_growth"]})

    for key in ("ebit_margin", "tax_rate", "capex_pct_revenue", "da_pct_revenue", "nwc_pct_revenue"):
        if d.get(key) is not None:
            values[key] = _path(d[key], n)
            sources[key] = "config"
            continue
        hv = h[key]
        if hv is None:
            raise DCFError(f"{key}: no config value and no historical data")
        cv = _clamp(hv, clamps[key])
        values[key] = [cv] * n
        stat = "median" if key in MEDIAN_DRIVERS else "avg"
        sources[key] = f"historical {stat} {hv:.2%} over {h['years_used'][key]}y" + (f", clamped to {cv:.2%}" if cv != hv else "")
        if cv != hv:
            dq.append({"item": key, "issue": "clamped", "detail": sources[key]})
        if h["years_used"][key] < len(hist["periods"]):
            dq.append({"item": key, "issue": "partial_history",
                       "detail": f"average uses {h['years_used'][key]} of {len(hist['periods'])} years"})

    values["terminal_growth"] = g_term
    sources["terminal_growth"] = "config"
    values["mid_year_convention"] = bool(d["mid_year_convention"])
    sources["mid_year_convention"] = "config"
    return {"values": values, "sources": sources, "historical": h, "data_quality": dq}


# ---------------------------------------------------------------- WACC

def compute_wacc(
    *, risk_free_rate: float, beta: float, equity_risk_premium: float,
    pre_tax_cost_of_debt: float, tax_rate: float, market_cap: float, total_debt: float,
) -> dict:
    """CAPM cost of equity, after-tax cost of debt, market-value weights (book debt as proxy for market)."""
    if market_cap is None or market_cap <= 0:
        raise DCFError("WACC needs a positive market cap")
    if total_debt is None or total_debt < 0:
        raise DCFError("WACC needs total debt (None/negative given)")
    ke = risk_free_rate + beta * equity_risk_premium
    kd_after = pre_tax_cost_of_debt * (1 - tax_rate)
    v = market_cap + total_debt
    we, wd = market_cap / v, total_debt / v
    return {
        "risk_free_rate": risk_free_rate, "beta": beta, "equity_risk_premium": equity_risk_premium,
        "cost_of_equity": ke, "pre_tax_cost_of_debt": pre_tax_cost_of_debt, "tax_rate": tax_rate,
        "after_tax_cost_of_debt": kd_after, "equity_value": market_cap, "debt_value": total_debt,
        "weight_equity": we, "weight_debt": wd, "wacc": we * ke + wd * kd_after,
    }


def wacc_inputs(hist: dict, market: dict, cfg: dict, tax_rate: float) -> tuple[dict, list[dict], dict]:
    """Pick beta / cost of debt from config or data (with clamps + fallbacks). Returns (kwargs, dq, sources)."""
    m, clamps = cfg["market"], cfg["clamps"]
    dq, src = [], {}
    if m.get("beta") is not None:
        beta, src["beta"] = m["beta"], "config"
    elif market.get("beta") is not None:
        raw, w = market["beta"], m.get("blume_weight", 1.0)
        adj = w * raw + (1 - w)
        beta = _clamp(adj, clamps["beta"])
        src["beta"] = (f"yfinance raw {raw:.2f}" + (f", Blume-adjusted ({w:g} x raw + {1 - w:g}) = {adj:.2f}" if w != 1.0 else "")
                       + (f", clamped to {beta:.2f}" if beta != adj else ""))
    else:
        beta, src["beta"] = m["fallback_beta"], "config fallback"
        dq.append({"item": "beta", "issue": "missing", "detail": f"no beta from yfinance; using fallback {beta}"})

    it = hist["items"]
    debt, interest = it["total_debt"][-1], it["interest"][-1]
    if m.get("pre_tax_cost_of_debt") is not None:
        kd, src["pre_tax_cost_of_debt"] = m["pre_tax_cost_of_debt"], "config"
    elif debt and interest is not None:
        raw = interest / debt
        kd = _clamp(raw, clamps["pre_tax_cost_of_debt"])
        src["pre_tax_cost_of_debt"] = f"latest interest / total debt = {raw:.2%}" + (" (clamped)" if kd != raw else "")
    else:
        kd, src["pre_tax_cost_of_debt"] = m["fallback_pre_tax_cost_of_debt"], "config fallback"
        dq.append({"item": "pre_tax_cost_of_debt", "issue": "missing",
                   "detail": f"latest interest expense or total debt missing; using fallback {kd:.2%}"})
    kwargs = dict(
        risk_free_rate=m["risk_free_rate"], beta=beta, equity_risk_premium=m["equity_risk_premium"],
        pre_tax_cost_of_debt=kd, tax_rate=tax_rate, market_cap=market.get("market_cap"), total_debt=debt,
    )
    src.update(risk_free_rate="config", equity_risk_premium="config", market_cap="yfinance",
               total_debt=f"EDGAR {hist['fiscal_years'][-1]}")
    return kwargs, dq, src


# ---------------------------------------------------------------- projection + valuation

def project(base_revenue: float, a: dict, n: int) -> list[dict]:
    """Year-by-year projection of revenue -> FCFF. `a` holds per-year paths (lists of length n)."""
    rows, prev_rev = [], base_revenue
    for t in range(n):
        rev = prev_rev * (1 + a["revenue_growth"][t])
        ebit = rev * a["ebit_margin"][t]
        tax = ebit * a["tax_rate"][t]
        nopat = ebit - tax
        da = rev * a["da_pct_revenue"][t]
        capex = rev * a["capex_pct_revenue"][t]
        d_nwc = a["nwc_pct_revenue"][t] * (rev - prev_rev)
        rows.append({
            "year": t + 1, "revenue": rev, "growth": a["revenue_growth"][t], "ebit": ebit, "ebit_margin": a["ebit_margin"][t],
            "taxes": tax, "nopat": nopat, "da": da, "ebitda": ebit + da, "capex": capex, "change_nwc": d_nwc,
            "fcff": nopat + da - capex - d_nwc,
        })
        prev_rev = rev
    return rows


def discount(rows: list[dict], wacc: float, mid_year: bool) -> list[dict]:
    out = []
    for r in rows:
        period = r["year"] - 0.5 if mid_year else r["year"]
        df = 1 / (1 + wacc) ** period
        out.append({**r, "discount_period": period, "discount_factor": df, "pv_fcff": r["fcff"] * df})
    return out


def terminal_gordon(final_fcff: float, wacc: float, g: float) -> float:
    if wacc <= g:
        raise DCFError(f"WACC {wacc:.2%} must exceed terminal growth {g:.2%}")
    return final_fcff * (1 + g) / (wacc - g)


def terminal_exit(final_ebitda: float, multiple: float) -> float:
    return final_ebitda * multiple


def equity_bridge(ev: float, net_debt: float | None, shares: float | None, price: float | None) -> dict:
    equity = None if net_debt is None else ev - net_debt
    per_share = None if equity is None or not shares else equity / shares
    upside = None if per_share is None or not price else per_share / price - 1
    return {"enterprise_value": ev, "net_debt": net_debt, "equity_value": equity,
            "diluted_shares": shares, "implied_price": per_share, "current_price": price, "upside": upside}


def value(rows: list[dict], wacc: float, g: float, exit_multiple: float, mid_year: bool,
          net_debt: float | None, shares: float | None, price: float | None) -> dict:
    """Full valuation for a projection under both terminal methods."""
    disc = discount(rows, wacc, mid_year)
    pv_fcff = sum(r["pv_fcff"] for r in disc)
    n = rows[-1]["year"]
    tv_df = 1 / (1 + wacc) ** n
    out = {"projection": disc, "sum_pv_fcff": pv_fcff, "terminal_discount_factor": tv_df}
    for method, tv in (("gordon", terminal_gordon(rows[-1]["fcff"], wacc, g)),
                       ("exit_multiple", terminal_exit(rows[-1]["ebitda"], exit_multiple))):
        pv_tv = tv * tv_df
        ev = pv_fcff + pv_tv
        out[method] = {"terminal_value": tv, "pv_terminal_value": pv_tv, "tv_pct_of_ev": pv_tv / ev,
                       **equity_bridge(ev, net_debt, shares, price)}
    out["gordon"]["implied_exit_multiple"] = out["gordon"]["terminal_value"] / rows[-1]["ebitda"]
    out["exit_multiple"]["implied_perpetual_growth"] = (
        (out["exit_multiple"]["terminal_value"] * wacc - rows[-1]["fcff"])
        / (out["exit_multiple"]["terminal_value"] + rows[-1]["fcff"])
    )
    return out


def _axis(center: float, step: float, k: int) -> list[float]:
    return [round(center + step * i, 10) for i in range(-k, k + 1)]


def sensitivities(rows: list[dict], wacc: float, g: float, exit_multiple: float, mid_year: bool,
                  net_debt, shares, cfg_sens: dict) -> dict:
    """Implied price grids: WACC x terminal growth (Gordon) and WACC x exit multiple."""
    k = cfg_sens["steps_each_side"]
    waccs = _axis(wacc, cfg_sens["wacc_step"], k)
    gs = _axis(g, cfg_sens["terminal_growth_step"], k)
    mults = _axis(exit_multiple, cfg_sens["exit_multiple_step"], k)

    def price(w, gg=None, mult=None):
        if w <= 0 or (gg is not None and w <= gg) or (mult is not None and mult <= 0):
            return None
        disc = discount(rows, w, mid_year)
        tv = terminal_gordon(rows[-1]["fcff"], w, gg) if gg is not None else terminal_exit(rows[-1]["ebitda"], mult)
        ev = sum(r["pv_fcff"] for r in disc) + tv / (1 + w) ** rows[-1]["year"]
        return equity_bridge(ev, net_debt, shares, None)["implied_price"]

    return {
        "wacc_x_terminal_growth": {"rows_wacc": waccs, "cols_terminal_growth": gs,
                                   "implied_price": [[price(w, gg=x) for x in gs] for w in waccs]},
        "wacc_x_exit_multiple": {"rows_wacc": waccs, "cols_exit_multiple": mults,
                                 "implied_price": [[price(w, mult=x) for x in mults] for w in waccs]},
    }


def run_dcf(hist: dict, market: dict, cfg: dict, exit_multiple: float | None = None,
            exit_multiple_source: str | None = None) -> dict:
    """End-to-end DCF from normalized historicals + market snapshot + config. Pure."""
    n = cfg["projection_years"]
    res = resolve_assumptions(hist, cfg)
    a, dq = res["values"], list(res["data_quality"])
    sources = dict(res["sources"])

    tax_for_wacc = a["tax_rate"][0]
    w_kwargs, w_dq, w_src = wacc_inputs(hist, market, cfg, tax_for_wacc)
    dq += w_dq
    wacc = compute_wacc(**w_kwargs)
    wacc["wacc_calculated"] = wacc["wacc"]
    wacc["wacc_override"] = cfg["market"].get("wacc_override")
    if wacc["wacc_override"] is not None:
        wacc["wacc"] = float(wacc["wacc_override"])
        w_src["wacc"] = "config override"
    wacc["sources"] = w_src

    if cfg["dcf"].get("exit_ev_ebitda") is not None:
        exit_multiple, exit_multiple_source = cfg["dcf"]["exit_ev_ebitda"], "config"
    elif exit_multiple is None:
        exit_multiple, exit_multiple_source = cfg["dcf"]["fallback_exit_ev_ebitda"], "config fallback"
        dq.append({"item": "exit_ev_ebitda", "issue": "missing",
                   "detail": f"no peer median EV/EBITDA; using fallback {exit_multiple}x"})
    sources["exit_ev_ebitda"] = exit_multiple_source or "argument"

    it = hist["items"]
    base_rev = it["revenue"][-1]
    if base_rev is None:
        raise DCFError("latest revenue missing")
    debt, cash, sti = it["total_debt"][-1], it["cash"][-1], it["short_term_investments"][-1]
    net_debt = None if debt is None or cash is None else debt - cash - (sti or 0)
    if net_debt is None:
        dq.append({"item": "net_debt", "issue": "missing", "detail": "latest total debt or cash missing; no implied price"})
    shares, shares_src = it["shares_diluted"][-1], f"EDGAR diluted weighted avg {hist['fiscal_years'][-1]}"
    if shares is None:
        shares, shares_src = market.get("shares_outstanding"), "yfinance shares outstanding (basic)"
        dq.append({"item": "diluted_shares", "issue": "missing", "detail": "EDGAR diluted shares missing; using yfinance"})

    rows = project(base_rev, a, n)
    val = value(rows, wacc["wacc"], a["terminal_growth"], exit_multiple, a["mid_year_convention"],
                net_debt, shares, market.get("price"))
    sens = sensitivities(rows, wacc["wacc"], a["terminal_growth"], exit_multiple, a["mid_year_convention"],
                         net_debt, shares, cfg["sensitivity"])
    return {
        "assumptions": {**a, "exit_ev_ebitda": exit_multiple},
        "assumption_sources": sources,
        "historical_drivers": res["historical"],
        "wacc": wacc,
        "base_year": {"fiscal_year": hist["fiscal_years"][-1], "revenue": base_rev},
        "bridge_inputs": {"total_debt": debt, "cash": cash, "short_term_investments": sti,
                          "net_debt": net_debt, "diluted_shares": shares, "diluted_shares_source": shares_src},
        **val,
        "sensitivity": sens,
        "data_quality": dq,
    }
