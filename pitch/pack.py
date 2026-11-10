"""Assemble outputs/<TICKER>/pitch_pack.json from EDGAR + yfinance + config.

Usage: python -m pitch.pack AAPL --peers MSFT,GOOGL
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

from pitch.comps import build_comps
from pitch.data import edgar, market
from pitch.dcf import load_config, run_dcf
from pitch.normalize import normalize

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "outputs"
SCHEMA_VERSION = 2
REQUIRED_KEYS = ["schema_version", "meta", "market", "historicals", "assumptions", "wacc", "dcf",
                 "sensitivity", "reverse_dcf", "comps", "valuation_summary", "data_quality"]

# Banks, credit institutions, brokers (6000-6299) and insurers (6300-6411): FCFF DCF doesn't apply.
FINANCIAL_SIC_RANGES = [(6000, 6299), (6300, 6411)]


class UnsupportedCompany(RuntimeError):
    pass


def is_financial(sic: int | None) -> bool:
    return sic is not None and any(lo <= sic <= hi for lo, hi in FINANCIAL_SIC_RANGES)


def _grid_range(grid: list[list]) -> dict | None:
    xs = [x for row in grid for x in row if x is not None]
    return {"low": min(xs), "high": max(xs)} if xs else None


def build_pack(ticker: str, hist: dict, mkt: dict, peers: dict[str, tuple[dict, dict]], cfg: dict,
               meta_extra: dict | None = None) -> dict:
    """Pure assembly: normalized historicals + market data + peers + config -> pitch pack dict."""
    dq = [{"source": "normalize", **d} for d in hist["data_quality"]]
    for t, (h, _) in peers.items():
        dq += [{"source": f"normalize:{t}", **d} for d in h["data_quality"] if d["issue"] == "missing"]

    shares = hist["items"]["shares_diluted"][-1] or mkt.get("shares_outstanding")
    comps = build_comps(ticker, hist, mkt, peers, shares)
    dq += [{"source": "comps", **d} for d in comps.pop("data_quality")]

    peer_ev_ebitda = comps["peer_stats"]["ev_ebitda"]["median"]
    dcf = run_dcf(hist, mkt, cfg, exit_multiple=peer_ev_ebitda,
                  exit_multiple_source=f"peer median EV/EBITDA ({', '.join(peers)})" if peer_ev_ebitda else None)
    dq += [{"source": "dcf", **d} for d in dcf.pop("data_quality")]
    sens = dcf.pop("sensitivity")
    rev = dcf.pop("reverse_dcf")

    summary = {
        "current_price": mkt.get("price"),
        "dcf_gordon": {"implied_price": dcf["gordon"]["implied_price"], "upside": dcf["gordon"]["upside"]},
        "dcf_exit_multiple": {"implied_price": dcf["exit_multiple"]["implied_price"],
                              "upside": dcf["exit_multiple"]["upside"]},
        "dcf_range_gordon_sensitivity": _grid_range(sens["wacc_x_terminal_growth"]["implied_price"]),
        "dcf_range_exit_sensitivity": _grid_range(sens["wacc_x_exit_multiple"]["implied_price"]),
        "comps_range": comps["implied"]["range"],
        "week52_range": {"low": mkt.get("week52_low"), "high": mkt.get("week52_high")},
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "meta": {"ticker": ticker, "name": mkt.get("name") or hist.get("entity_name"), "cik": hist.get("cik"),
                 "peers": list(peers), "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "units": "USD (absolute), shares (absolute), ratios as decimals",
                 "sources": {"financials": "SEC EDGAR companyfacts (10-K)", "market": "yfinance"},
                 **(meta_extra or {})},
        "market": mkt,
        "historicals": {k: hist[k] for k in ("periods", "fiscal_years", "items", "sources")},
        "assumptions": {"values": dcf.pop("assumptions"), "sources": dcf.pop("assumption_sources"),
                        "config": cfg},
        "wacc": dcf.pop("wacc"),
        "dcf": dcf,
        "sensitivity": sens,
        "reverse_dcf": rev,
        "comps": comps,
        "valuation_summary": summary,
        "data_quality": dq,
    }


def _finite(obj, path="$"):
    if isinstance(obj, float) and not math.isfinite(obj):
        raise ValueError(f"non-finite number at {path}")
    if isinstance(obj, dict):
        for k, v in obj.items():
            _finite(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _finite(v, f"{path}[{i}]")


def validate_pack(pack: dict) -> None:
    """Raise ValueError if the pack is missing required sections or contains NaN/inf."""
    missing = [k for k in REQUIRED_KEYS if k not in pack]
    if missing:
        raise ValueError(f"pitch pack missing keys: {missing}")
    n = len(pack["historicals"]["periods"])
    for item, vals in pack["historicals"]["items"].items():
        if len(vals) != n:
            raise ValueError(f"historicals.{item} has {len(vals)} values, expected {n}")
    _finite(pack)


def write_pack(pack: dict, out_dir: Path = OUTPUT_DIR) -> Path:
    validate_pack(pack)
    path = out_dir / pack["meta"]["ticker"] / "pitch_pack.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pack, indent=2, allow_nan=False))
    return path


def fetch_company(ticker: str, years: int) -> tuple[dict, dict]:
    return normalize(edgar.get_company_facts(ticker), years), market.get_market_data(ticker)


def fetch_inputs(ticker: str, peer_list: list[str], history_years: int) -> dict:
    """All network I/O for a pack: SIC check, target + peer financials and market data."""
    ticker = ticker.upper()
    sic = edgar.get_sic(ticker)
    if is_financial(sic):
        raise UnsupportedCompany(f"{ticker} has SIC {sic} (bank/insurer); FCFF DCF doesn't apply. Out of scope.")
    hist, mkt = fetch_company(ticker, history_years)
    peers, skipped = {}, []
    for p in peer_list:
        try:
            peers[p] = fetch_company(p, history_years)
        except Exception as e:  # one bad peer shouldn't kill the pack
            skipped.append({"source": "comps", "item": f"peer:{p}", "issue": "missing", "detail": str(e)})
    return {"ticker": ticker, "sic": sic, "hist": hist, "mkt": mkt, "peers": peers, "skipped": skipped}


def assemble(inputs: dict, cfg: dict) -> dict:
    """Pure: fetched inputs + config -> validated pitch pack dict."""
    pack = build_pack(inputs["ticker"], inputs["hist"], inputs["mkt"], inputs["peers"], cfg,
                      meta_extra={"sic": inputs["sic"]})
    pack["data_quality"] += inputs["skipped"]
    validate_pack(pack)
    return pack


def run(ticker: str, peer_list: list[str], out_dir: Path = OUTPUT_DIR) -> Path:
    """I/O wrapper: fetch everything, build, validate, write."""
    cfg = load_config(ticker)
    return write_pack(assemble(fetch_inputs(ticker, peer_list, cfg["history_years"]), cfg), out_dir)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pitch.pack", description="Build outputs/<TICKER>/pitch_pack.json")
    ap.add_argument("ticker")
    ap.add_argument("--peers", default="", help="Comma-separated peer tickers")
    args = ap.parse_args(argv)
    peers = [p.strip().upper() for p in args.peers.split(",") if p.strip()]
    try:
        path = run(args.ticker, peers)
    except (UnsupportedCompany, edgar.EdgarError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    pack = json.loads(path.read_text())
    s = pack["valuation_summary"]
    fmt = lambda x: "n/a" if x is None else f"{x:,.2f}"
    print(f"wrote {path}")
    print(f"price {fmt(s['current_price'])} | DCF gordon {fmt(s['dcf_gordon']['implied_price'])} | "
          f"DCF exit {fmt(s['dcf_exit_multiple']['implied_price'])} | WACC {pack['wacc']['wacc']:.2%} | "
          f"data_quality flags: {len(pack['data_quality'])}")
    r, pct = pack["reverse_dcf"], lambda x: "n/a" if x is None else f"{x:.2%}"
    print(f"reverse DCF @ {fmt(s['current_price'])}: constant revenue growth {pct(r['revenue_growth']['price_target'])} "
          f"| terminal EBIT margin {pct(r['ebit_margin']['price_target'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
