import pytest

from pitch.dcf import (
    DCFError, _deep_merge, compute_wacc, load_config, margin_ramp, project, resolve_assumptions, reverse_dcf,
    run_dcf, sensitivities, terminal_gordon, value,
)
from pitch.normalize import normalize

# Hand-checkable case: base revenue 100, 2 years, 10% growth, 20% EBIT margin, 25% tax,
# D&A 5% rev, capex 5% rev, NWC 10% rev, WACC 10%, g 2%, exit 10x EBITDA, net debt 20, 10 shares.
#
#   Y1: rev 110, EBIT 22,   NOPAT 16.5,  D&A 5.5,  capex 5.5,  dNWC 1.0 -> FCFF 15.50
#   Y2: rev 121, EBIT 24.2, NOPAT 18.15, D&A 6.05, capex 6.05, dNWC 1.1 -> FCFF 17.05
#   PV (end-year): 15.5/1.1 = 14.0909, 17.05/1.21 = 14.0909 -> 28.1818
#   Gordon TV = 17.05*1.02/0.08 = 217.3875, PV = /1.21 = 179.6591, EV = 207.8409
#     equity = 187.8409 -> price 18.7841
#   Exit TV = (24.2+6.05)*10 = 302.5, PV = 250.0, EV = 278.1818 -> equity 258.1818 -> price 25.8182
A = {
    "revenue_growth": [0.10, 0.10], "ebit_margin": [0.20, 0.20], "tax_rate": [0.25, 0.25],
    "da_pct_revenue": [0.05, 0.05], "capex_pct_revenue": [0.05, 0.05], "nwc_pct_revenue": [0.10, 0.10],
}


def test_projection_hand_checked():
    rows = project(100.0, A, 2)
    assert [r["revenue"] for r in rows] == pytest.approx([110, 121])
    assert [r["nopat"] for r in rows] == pytest.approx([16.5, 18.15])
    assert [r["change_nwc"] for r in rows] == pytest.approx([1.0, 1.1])
    assert [r["fcff"] for r in rows] == pytest.approx([15.5, 17.05])
    assert rows[-1]["ebitda"] == pytest.approx(30.25)


def test_valuation_hand_checked_end_year():
    v = value(project(100.0, A, 2), wacc=0.10, g=0.02, exit_multiple=10, mid_year=False,
              net_debt=20, shares=10, price=20)
    assert v["sum_pv_fcff"] == pytest.approx(28.181818, abs=1e-6)
    g = v["gordon"]
    assert g["terminal_value"] == pytest.approx(217.3875)
    assert g["pv_terminal_value"] == pytest.approx(179.659091, abs=1e-6)
    assert g["enterprise_value"] == pytest.approx(207.840909, abs=1e-6)
    assert g["equity_value"] == pytest.approx(187.840909, abs=1e-6)
    assert g["implied_price"] == pytest.approx(18.784091, abs=1e-6)
    assert g["upside"] == pytest.approx(18.784091 / 20 - 1, abs=1e-6)
    e = v["exit_multiple"]
    assert e["terminal_value"] == pytest.approx(302.5)
    assert e["pv_terminal_value"] == pytest.approx(250.0)
    assert e["implied_price"] == pytest.approx(25.818182, abs=1e-6)
    # Cross-checks between methods
    assert g["implied_exit_multiple"] == pytest.approx(217.3875 / 30.25)
    tv = 302.5
    assert e["implied_perpetual_growth"] == pytest.approx((tv * 0.10 - 17.05) / (tv + 17.05))


def test_mid_year_convention():
    v = value(project(100.0, A, 2), 0.10, 0.02, 10, True, 20, 10, None)
    expected = 15.5 / 1.1 ** 0.5 + 17.05 / 1.1 ** 1.5
    assert v["sum_pv_fcff"] == pytest.approx(expected)
    # terminal value still discounted N full years
    assert v["gordon"]["pv_terminal_value"] == pytest.approx(179.659091, abs=1e-6)
    assert v["gordon"]["upside"] is None  # no price -> no upside, not 0


def test_missing_net_debt_gives_no_price():
    v = value(project(100.0, A, 2), 0.10, 0.02, 10, False, None, 10, 20)
    assert v["gordon"]["enterprise_value"] == pytest.approx(207.840909, abs=1e-6)
    assert v["gordon"]["implied_price"] is None


def test_wacc_hand_checked():
    w = compute_wacc(risk_free_rate=0.04, beta=1.2, equity_risk_premium=0.05, pre_tax_cost_of_debt=0.05,
                     tax_rate=0.20, market_cap=800, total_debt=200)
    assert w["cost_of_equity"] == pytest.approx(0.10)
    assert w["after_tax_cost_of_debt"] == pytest.approx(0.04)
    assert w["wacc"] == pytest.approx(0.088)


def test_gordon_rejects_wacc_below_growth():
    with pytest.raises(DCFError):
        terminal_gordon(10, 0.02, 0.03)


def test_sensitivity_center_matches_base():
    rows = project(100.0, A, 2)
    s = sensitivities(rows, 0.10, 0.02, 10, False, 20, 10,
                      {"wacc_step": 0.01, "terminal_growth_step": 0.005, "exit_multiple_step": 2, "steps_each_side": 1})
    gg = s["wacc_x_terminal_growth"]
    assert gg["rows_wacc"] == pytest.approx([0.09, 0.10, 0.11])
    assert gg["cols_terminal_growth"] == pytest.approx([0.015, 0.02, 0.025])
    assert gg["implied_price"][1][1] == pytest.approx(18.784091, abs=1e-6)
    assert s["wacc_x_exit_multiple"]["implied_price"][1][1] == pytest.approx(25.818182, abs=1e-6)
    # higher WACC -> lower price; higher g -> higher price
    assert gg["implied_price"][0][1] > gg["implied_price"][1][1] > gg["implied_price"][2][1]
    assert gg["implied_price"][1][0] < gg["implied_price"][1][2]


def test_sensitivity_invalid_cell_is_none():
    rows = project(100.0, A, 2)
    s = sensitivities(rows, 0.03, 0.025, 10, False, 20, 10,
                      {"wacc_step": 0.005, "terminal_growth_step": 0.005, "exit_multiple_step": 2, "steps_each_side": 1})
    assert s["wacc_x_terminal_growth"]["implied_price"][0][2] is None  # wacc 2.5% <= g 3%


def test_resolve_assumptions_from_history(facts, cfg):
    r = resolve_assumptions(normalize(facts), cfg)
    v = r["values"]
    assert v["revenue_growth"][0] == pytest.approx(0.10)              # CAGR of a clean 10% series
    assert v["revenue_growth"][-1] == pytest.approx(cfg["dcf"]["terminal_growth"])
    assert v["ebit_margin"] == pytest.approx([0.20] * 5)
    assert v["tax_rate"][0] == pytest.approx(0.25)
    assert v["capex_pct_revenue"][0] == pytest.approx(0.05)
    assert "historical" in r["sources"]["ebit_margin"]


def test_config_overrides_and_clamps(facts, cfg):
    cfg = _deep_merge(cfg, {"dcf": {"ebit_margin": [0.3, 0.3, 0.3, 0.3, 0.3], "tax_rate": 0.21},
                            "clamps": {"revenue_growth": [-0.1, 0.05]}})
    r = resolve_assumptions(normalize(facts), cfg)
    assert r["values"]["ebit_margin"] == [0.3] * 5 and r["sources"]["ebit_margin"] == "config"
    assert r["values"]["tax_rate"] == [0.21] * 5
    assert r["values"]["revenue_growth"][0] == pytest.approx(0.05)
    assert any(d["issue"] == "clamped" for d in r["data_quality"])


def test_bad_path_length_rejected(facts, cfg):
    cfg = _deep_merge(cfg, {"dcf": {"ebit_margin": [0.3, 0.3]}})
    with pytest.raises(DCFError):
        resolve_assumptions(normalize(facts), cfg)


def test_run_dcf_end_to_end(facts, cfg):
    mkt = {"price": 20.0, "market_cap": 200.0, "beta": 1.0, "shares_outstanding": 10}
    out = run_dcf(normalize(facts), mkt, cfg, exit_multiple=10.0, exit_multiple_source="test")
    assert out["bridge_inputs"]["net_debt"] == 10  # 20 debt - 10 cash
    assert out["wacc"]["pre_tax_cost_of_debt"] == pytest.approx(0.05)  # 1 / 20
    assert out["assumption_sources"]["exit_ev_ebitda"] == "test"
    assert out["gordon"]["implied_price"] > 0 and out["exit_multiple"]["implied_price"] > 0
    assert len(out["projection"]) == cfg["projection_years"]


def test_run_dcf_flags_cost_of_debt_fallback(cfg):
    from tests.conftest import base_series, make_facts
    s = base_series()
    s.pop("InterestExpense")
    h = normalize(make_facts(s, {"WeightedAverageNumberOfDilutedSharesOutstanding": "shares"}))
    out = run_dcf(h, {"price": 20.0, "market_cap": 200.0, "beta": None}, cfg)
    items = {d["item"] for d in out["data_quality"]}
    assert {"pre_tax_cost_of_debt", "beta", "exit_ev_ebitda"} <= items
    assert out["wacc"]["pre_tax_cost_of_debt"] == cfg["market"]["fallback_pre_tax_cost_of_debt"]


def test_load_config_ticker_override(tmp_path):
    (tmp_path / "assumptions.yaml").write_text("dcf:\n  terminal_growth: 0.02\n  mid_year_convention: true\n")
    (tmp_path / "XYZ.yaml").write_text("dcf:\n  terminal_growth: 0.03\n")
    cfg = load_config("xyz", tmp_path)
    assert cfg["dcf"] == {"terminal_growth": 0.03, "mid_year_convention": True}
    assert load_config("abc", tmp_path)["dcf"]["terminal_growth"] == 0.02


def test_starting_growth_averages_last_year_and_cagr(cfg):
    from tests.conftest import base_series, make_facts
    s = base_series()
    # Revenue 100 -> 200 over 4 years (CAGR 2^(1/4)-1 = 18.92%), last year 160 -> 200 = 25%.
    s["Revenues"] = ([100, 120, 140, 160, 200], True)
    h = normalize(make_facts(s, {"WeightedAverageNumberOfDilutedSharesOutstanding": "shares"}))
    r = resolve_assumptions(h, cfg)
    g0 = (0.25 + (2 ** 0.25 - 1)) / 2
    path = r["values"]["revenue_growth"]
    assert path[0] == pytest.approx(g0)
    assert path[-1] == pytest.approx(cfg["dcf"]["terminal_growth"])
    assert path[2] == pytest.approx((g0 + cfg["dcf"]["terminal_growth"]) / 2)  # linear fade
    assert "last-year growth" in r["sources"]["revenue_growth"]


def test_default_market_assumptions(cfg):
    assert cfg["market"]["equity_risk_premium"] == pytest.approx(0.045)
    assert cfg["market"]["blume_weight"] == pytest.approx(0.67)


def test_blume_adjusted_beta(facts, cfg):
    h = normalize(facts)
    out = run_dcf(h, {"price": 20.0, "market_cap": 200.0, "beta": 1.6, "shares_outstanding": 10}, cfg, exit_multiple=10.0)
    assert out["wacc"]["beta"] == pytest.approx(0.67 * 1.6 + 0.33)
    assert "Blume" in out["wacc"]["sources"]["beta"]
    # Clamp applies after adjustment: raw 0.1 -> 0.397 -> clamped to 0.50.
    low = run_dcf(h, {"price": 20.0, "market_cap": 200.0, "beta": 0.1}, cfg, exit_multiple=10.0)
    assert low["wacc"]["beta"] == pytest.approx(cfg["clamps"]["beta"][0])
    # An explicit config beta is used as-is.
    fixed = run_dcf(h, {"price": 20.0, "market_cap": 200.0, "beta": 1.6}, _deep_merge(cfg, {"market": {"beta": 1.2}}),
                    exit_multiple=10.0)
    assert fixed["wacc"]["beta"] == 1.2


def test_ebit_margin_uses_median_robust_to_one_off_charge(cfg):
    """Regression: a single impairment year must not drag the default margin down (KO FY2024 case)."""
    from tests.conftest import base_series, make_facts
    s = base_series()
    rev = s["Revenues"][0]
    # Margins 20%, 20%, 20%, 5% (one-off charge), 22% -> median 20%; mean would be 17.4%.
    s["OperatingIncomeLoss"] = ([r * m for r, m in zip(rev, [0.20, 0.20, 0.20, 0.05, 0.22])], True)
    h = normalize(make_facts(s, {"WeightedAverageNumberOfDilutedSharesOutstanding": "shares"}))
    r = resolve_assumptions(h, cfg)
    assert r["values"]["ebit_margin"] == pytest.approx([0.20] * 5)
    assert r["sources"]["ebit_margin"].startswith("historical median")
    # Even-length history -> mean of the middle two.
    from pitch.dcf import _median
    assert _median([0.1, 0.4, 0.2, 0.3]) == pytest.approx(0.25)


# ---------------------------------------------------------------- reverse DCF

def _px(a, **kw):
    v = value(project(100.0, a, 2), 0.10, 0.02, 10, False, 20, 10, None)
    pg, pe = v["gordon"]["implied_price"], v["exit_multiple"]["implied_price"]
    return {"gordon": pg, "exit_multiple": pe, "price_target": (pg + pe) / 2}


def test_reverse_dcf_round_trip_at_model_price():
    # Price the stock at the model's own price target -> solved drivers recover the model's 10% / 20%.
    pt = _px(A)["price_target"]  # (18.784091 + 25.818182) / 2
    r = reverse_dcf(100.0, A, 2, 0.10, 0.02, 10, False, 20, 10, pt)
    assert r["revenue_growth"]["price_target"] == pytest.approx(0.10, abs=1e-8)
    assert r["ebit_margin"]["price_target"] == pytest.approx(0.20, abs=1e-8)
    assert r["revenue_growth"]["gap_vs_model"] == pytest.approx(0, abs=1e-8)
    assert r["model"]["revenue_growth_cagr"] == pytest.approx(0.10)
    # Per-method solves bracket the blended one (Gordon < PT < exit at the model's drivers)
    assert r["revenue_growth"]["gordon"] > 0.10 > r["revenue_growth"]["exit_multiple"]


@pytest.mark.parametrize("price", [12.0, 20.0, 35.0])
def test_reverse_dcf_solutions_reprice_exactly(price):
    r = reverse_dcf(100.0, A, 2, 0.10, 0.02, 10, False, 20, 10, price)
    for basis in ("price_target", "gordon", "exit_multiple"):
        g = r["revenue_growth"][basis]
        assert _px({**A, "revenue_growth": [g, g]})[basis] == pytest.approx(price, abs=1e-6)
        m = r["ebit_margin"][basis]
        assert _px({**A, "ebit_margin": margin_ramp(0.20, m, 2)})[basis] == pytest.approx(price, abs=1e-6)


def test_reverse_dcf_monotonic_in_price():
    lo = reverse_dcf(100.0, A, 2, 0.10, 0.02, 10, False, 20, 10, 15.0)
    hi = reverse_dcf(100.0, A, 2, 0.10, 0.02, 10, False, 20, 10, 30.0)
    assert lo["revenue_growth"]["price_target"] < hi["revenue_growth"]["price_target"]
    assert lo["ebit_margin"]["price_target"] < hi["ebit_margin"]["price_target"]


def test_margin_ramp_hand_checked():
    assert margin_ramp(0.20, 0.30, 3) == pytest.approx([0.20, 0.25, 0.30])
    assert margin_ramp(0.20, 0.30, 1) == [0.30]


def test_reverse_dcf_no_solution_and_missing_inputs():
    # A price far beyond anything reachable inside the search bounds -> None, not a bogus value
    r = reverse_dcf(100.0, A, 2, 0.10, 0.02, 10, False, 20, 10, 1e6)
    assert r["revenue_growth"]["price_target"] is None and r["revenue_growth"]["gap_vs_model"] is None
    r = reverse_dcf(100.0, A, 2, 0.10, 0.02, 10, False, 20, 10, None)
    assert "note" in r and r["ebit_margin"]["price_target"] is None


def test_run_dcf_includes_reverse_dcf(facts, cfg):
    out = run_dcf(normalize(facts), {"price": 20, "market_cap": 200, "beta": 1.0}, cfg, exit_multiple=10)
    r = out["reverse_dcf"]
    assert r["target_price"] == 20
    g = r["revenue_growth"]["price_target"]
    assert g is not None and -0.5 < g < 1.0
