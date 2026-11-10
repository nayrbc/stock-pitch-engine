import pytest

from pitch.dcf import (
    DCFError, _deep_merge, compute_wacc, load_config, project, resolve_assumptions, run_dcf,
    sensitivities, terminal_gordon, value,
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
