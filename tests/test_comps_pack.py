import json

import pytest

from pitch.comps import build_comps, company_metrics, summarize
from pitch.normalize import normalize
from pitch.pack import build_pack, is_financial, validate_pack, write_pack
from tests.conftest import base_series, make_facts

SHARES = {"WeightedAverageNumberOfDilutedSharesOutstanding": "shares"}


def hist(scale=1.0, **drop):
    s = base_series(scale)
    for tag in drop:
        s.pop(tag)
    return normalize(make_facts(s, SHARES))


def mkt(price, mcap, **kw):
    return {"price": price, "market_cap": mcap, "beta": 1.0, "shares_outstanding": 10, "name": None,
            "week52_low": 15.0, "week52_high": 25.0, **kw}


def test_company_metrics_hand_checked():
    m = company_metrics("T", hist(), mkt(20, 200))
    # EV = 200 + 20 debt - 10 cash = 210; rev 146.41; EBITDA 29.282+7.3205 = 36.6025; NI 21.2115
    assert m["enterprise_value"] == 210
    assert m["ev_revenue"] == pytest.approx(210 / 146.41)
    assert m["ev_ebitda"] == pytest.approx(210 / 36.6025)
    assert m["pe"] == pytest.approx(200 / 21.2115)
    assert m["revenue_growth"] == pytest.approx(0.10)
    assert m["ebit_margin"] == pytest.approx(0.20)


def test_missing_market_cap_flags_not_zero():
    m = company_metrics("T", hist(), mkt(20, None))
    assert m["ev_revenue"] is None and m["pe"] is None
    assert {d["item"] for d in m["data_quality"]} >= {"comps.T.ev_revenue", "comps.T.pe"}


def test_summarize_median_mean_ignores_none():
    rows = [{"ev_revenue": 1.0, "ev_ebitda": 10.0, "pe": None, "revenue_growth": 0.1, "ebit_margin": 0.2},
            {"ev_revenue": 3.0, "ev_ebitda": 14.0, "pe": 20.0, "revenue_growth": 0.3, "ebit_margin": 0.4},
            {"ev_revenue": 8.0, "ev_ebitda": None, "pe": 30.0, "revenue_growth": 0.2, "ebit_margin": 0.3}]
    s = summarize(rows)
    assert s["ev_revenue"] == {"median": 3.0, "mean": 4.0, "n": 3}
    assert s["ev_ebitda"]["median"] == 12.0 and s["pe"]["n"] == 2


def test_implied_price_at_peer_median():
    peers = {"P1": (hist(), mkt(30, 300)), "P2": (hist(), mkt(40, 400))}
    c = build_comps("T", hist(), mkt(20, 200), peers, diluted_shares=10)
    # Peer P/E: 300/21.2115, 400/21.2115 -> median 350/21.2115; implied equity = 350 -> price 35
    assert c["implied"]["pe"]["implied_price"] == pytest.approx(35.0)
    # Peer EV/Rev median = 360/146.41 -> EV 360 - net debt 10 = 350 -> 35
    assert c["implied"]["ev_revenue"]["implied_price"] == pytest.approx(35.0)
    assert c["implied"]["pe"]["upside"] == pytest.approx(0.75)
    assert c["implied"]["range"] == {"low": pytest.approx(35.0), "high": pytest.approx(35.0)}


def test_is_financial():
    assert is_financial(6021) and is_financial(6331)
    assert not is_financial(3571) and not is_financial(None) and not is_financial(6798)


def test_build_and_write_pack(tmp_path, cfg):
    peers = {"P1": (hist(2), mkt(30, 600)), "P2": (hist(3), mkt(40, 900))}
    pack = build_pack("TEST", hist(), mkt(20, 200), peers, cfg)
    validate_pack(pack)
    assert pack["assumptions"]["sources"]["exit_ev_ebitda"].startswith("peer median")
    assert pack["valuation_summary"]["dcf_gordon"]["implied_price"] is not None
    path = write_pack(pack, tmp_path)
    assert json.loads(path.read_text())["meta"]["ticker"] == "TEST"


def test_validate_rejects_nan_and_missing_keys(cfg):
    pack = build_pack("TEST", hist(), mkt(20, 200), {}, cfg)
    pack["dcf"]["sum_pv_fcff"] = float("nan")
    with pytest.raises(ValueError, match="non-finite"):
        validate_pack(pack)
    with pytest.raises(ValueError, match="missing keys"):
        validate_pack({"meta": {}})


def test_pack_without_peers_flags_and_falls_back(cfg):
    pack = build_pack("TEST", hist(), mkt(20, 200), {}, cfg)
    items = {d["item"] for d in pack["data_quality"]}
    assert {"comps", "exit_ev_ebitda"} <= items
    assert pack["assumptions"]["values"]["exit_ev_ebitda"] == cfg["dcf"]["fallback_exit_ev_ebitda"]
