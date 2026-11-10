"""Streamlit front end. Launch with `uv run pitch-ui`.

Thin layer over the existing modules: pack.fetch_inputs (cached I/O) -> pack.assemble with slider
overrides deep-merged into the config -> memo.scenarios / excel.build_workbook / memo.render.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path


def launch() -> None:
    """Console-script entry point: `streamlit run` this file."""
    from streamlit.web import cli as stcli

    sys.argv = ["streamlit", "run", str(Path(__file__).resolve()), *sys.argv[1:]]
    sys.exit(stcli.main())


def _app() -> None:
    import altair as alt
    import pandas as pd
    import streamlit as st

    from pitch import pack as pack_mod
    from pitch.data import edgar
    from pitch.dcf import DCFError, _deep_merge, load_config
    from pitch.excel import build_workbook
    from pitch.memo import TEMPLATE, render, scenarios
    from pitch.normalize import NormalizeError

    st.set_page_config(page_title="Stock Pitch Engine", layout="wide")
    st.title("Stock Pitch Engine")

    @st.cache_data(show_spinner="Fetching EDGAR + market data…")
    def fetch(ticker: str, peers: tuple[str, ...], years: int) -> dict:
        return pack_mod.fetch_inputs(ticker, list(peers), years)

    with st.sidebar.form("company"):
        ticker_in = st.text_input("Ticker", value=st.session_state.get("ticker", "AAPL"))
        peers_in = st.text_input("Peers (comma-separated)", value=st.session_state.get("peers", "MSFT,GOOGL"))
        if st.form_submit_button("Load", type="primary"):
            st.session_state["ticker"] = ticker_in.strip().upper()
            st.session_state["peers"] = peers_in
    ticker = st.session_state.get("ticker")
    if not ticker:
        st.info("Enter a ticker and peers in the sidebar, then press **Load**.")
        return
    peers = tuple(p.strip().upper() for p in st.session_state["peers"].split(",") if p.strip())

    base_cfg = load_config(ticker)
    try:
        inputs = fetch(ticker, peers, base_cfg["history_years"])
        base_pack = pack_mod.assemble(inputs, base_cfg)
    except (pack_mod.UnsupportedCompany, edgar.EdgarError, NormalizeError, DCFError, ValueError) as e:
        st.error(str(e))
        return

    # Sliders default to the model's own (config / historical) assumptions; keys are per-ticker so
    # switching companies resets them.
    a0, w0 = base_pack["assumptions"]["values"], base_pack["wacc"]
    k = f"{ticker}:{','.join(peers)}"
    sb = st.sidebar
    clip = lambda v, lo, hi: min(max(v, lo), hi)
    sb.header("Assumptions")
    sb.caption("Defaults come from config/historicals. Percent values.")
    growth = sb.slider("Year-1 revenue growth (fades to terminal)", -10.0, 50.0,
                       clip(round(a0["revenue_growth"][0] * 100, 1), -10.0, 50.0), 0.5, key=f"g0:{k}")
    margin = sb.slider("EBIT margin (held flat)", 0.0, 70.0, clip(round(a0["ebit_margin"][0] * 100, 1), 0.0, 70.0), 0.5, key=f"m:{k}")
    tg = sb.slider("Terminal growth", 0.0, 5.0, clip(round(a0["terminal_growth"] * 100, 2), 0.0, 5.0), 0.25, key=f"tg:{k}")
    use_wacc = sb.checkbox(f"Override WACC (calculated {w0['wacc_calculated']:.2%})",
                           value=w0.get("wacc_override") is not None, key=f"wo:{k}")
    wacc = sb.slider("WACC", 4.0, 16.0, clip(round(w0["wacc"] * 100, 2), 4.0, 16.0), 0.1, key=f"w:{k}", disabled=not use_wacc)
    if sb.button("Reset to defaults"):
        for key in list(st.session_state):
            if key.endswith(f":{k}"):
                del st.session_state[key]
        st.rerun()

    overrides = {"dcf": {"revenue_growth": None, "revenue_growth_start": growth / 100,
                         "ebit_margin": margin / 100, "terminal_growth": tg / 100},
                 "market": {"wacc_override": wacc / 100 if use_wacc else None}}
    try:
        pack = pack_mod.assemble(inputs, _deep_merge(base_cfg, overrides))
    except (DCFError, ValueError) as e:
        st.error(f"Invalid assumptions: {e}")
        return

    s = scenarios(pack)
    usd = lambda x: "n/a" if x is None else f"${x:,.2f}"
    pct = lambda x: None if x is None else f"{x:+.1%}"

    st.subheader(f"{pack['meta']['name']} ({ticker})  ·  peers: {', '.join(pack['meta']['peers']) or 'none'}")
    c = st.columns(5)
    c[0].metric("Rating", s["rating"])
    c[1].metric("Price target", usd(s["price_target"]), pct(s["upside"]))
    c[2].metric("Current price", usd(s["current_price"]))
    c[3].metric("DCF: perpetual growth", usd(pack["dcf"]["gordon"]["implied_price"]), pct(pack["dcf"]["gordon"]["upside"]))
    c[4].metric("DCF: exit multiple", usd(pack["dcf"]["exit_multiple"]["implied_price"]),
                pct(pack["dcf"]["exit_multiple"]["upside"]))
    st.caption(f"WACC {pack['wacc']['wacc']:.2%} · terminal growth {pack['assumptions']['values']['terminal_growth']:.2%} · "
               f"exit {pack['assumptions']['values']['exit_ev_ebitda']:.1f}x EV/EBITDA · "
               f"bear {usd(s['bear'])} / bull {usd(s['bull'])}")

    # Football field
    vs = pack["valuation_summary"]
    ranges = [("DCF: perpetual growth (WACC × g)", vs["dcf_range_gordon_sensitivity"]),
              ("DCF: exit multiple (WACC × multiple)", vs["dcf_range_exit_sensitivity"]),
              ("Trading comps (peer medians)", vs["comps_range"]),
              ("52-week range", vs["week52_range"])]
    ff = pd.DataFrame([{"method": n, "low": r["low"], "high": r["high"]} for n, r in ranges
                       if r and r.get("low") is not None and r.get("high") is not None])
    st.markdown("#### Football field ($/share)")
    if ff.empty:
        st.write("No ranges available.")
    else:
        y = alt.Y("method:N", sort=None, title=None, axis=alt.Axis(labelLimit=320))
        bars = alt.Chart(ff).mark_bar(height=22, cornerRadius=4, color="#1F4E79").encode(
            x=alt.X("low:Q", title="$ per share", scale=alt.Scale(zero=False)), x2="high:Q", y=y,
            tooltip=["method", alt.Tooltip("low:Q", format="$,.2f"), alt.Tooltip("high:Q", format="$,.2f")])
        refs = pd.DataFrame([{"label": "Current", "x": s["current_price"]},
                             {"label": "Price target", "x": s["price_target"]}]).dropna()
        rules = alt.Chart(refs).mark_rule(strokeWidth=2).encode(
            x="x:Q", strokeDash=alt.StrokeDash("label:N", legend=alt.Legend(title=None, orient="top")),
            tooltip=["label", alt.Tooltip("x:Q", format="$,.2f")])
        st.altair_chart((bars + rules).properties(height=200), width="stretch")

    # Sensitivity grids
    st.markdown("#### Sensitivity (implied price)")
    sens = pack["sensitivity"]

    def grid(g: dict, col_key: str, col_fmt) -> pd.DataFrame:
        return pd.DataFrame(g["implied_price"], index=[f"{w:.2%}" for w in g["rows_wacc"]],
                            columns=[col_fmt(x) for x in g[col_key]]).rename_axis("WACC")

    def show(df: pd.DataFrame) -> None:
        mid = len(df) // 2
        sty = df.style.format(lambda v: usd(v)).apply(
            lambda r: ["font-weight: bold; background-color: #DDEBF7" if (r.name == df.index[mid] and i == mid) else ""
                       for i in range(len(r))], axis=1)
        st.dataframe(sty, width="stretch")

    g1, g2 = st.columns(2)
    with g1:
        st.caption("WACC × terminal growth (Gordon)")
        show(grid(sens["wacc_x_terminal_growth"], "cols_terminal_growth", lambda x: f"{x:.2%}"))
    with g2:
        st.caption("WACC × exit EV/EBITDA")
        show(grid(sens["wacc_x_exit_multiple"], "cols_exit_multiple", lambda x: f"{x:.1f}x"))

    # Reverse DCF (re-solved at the current slider settings)
    rv = pack["reverse_dcf"]
    st.markdown(f"#### Reverse DCF: what {usd(s['current_price'])} implies")
    if rv.get("note"):
        st.write(rv["note"])
    else:
        rp = lambda x: "no solution" if x is None else f"{x:.2%}"
        gap = lambda x: None if x is None else f"{x * 100:+.1f} pts vs model"
        r1, r2 = st.columns(2)
        r1.metric("Constant revenue growth (all years)", rp(rv["revenue_growth"]["price_target"]),
                  gap(rv["revenue_growth"]["gap_vs_model"]), delta_color="off")
        r2.metric(f"Terminal EBIT margin (ramp from {rv['model']['year1_ebit_margin']:.1%})",
                  rp(rv["ebit_margin"]["price_target"]), gap(rv["ebit_margin"]["gap_vs_model"]), delta_color="off")
        st.dataframe(pd.DataFrame(
            {"Price target basis": [rp(rv[k]["price_target"]) for k in ("revenue_growth", "ebit_margin")],
             "Perpetual growth only": [rp(rv[k]["gordon"]) for k in ("revenue_growth", "ebit_margin")],
             "Exit multiple only": [rp(rv[k]["exit_multiple"]) for k in ("revenue_growth", "ebit_margin")],
             "Model": [f"{rv['model']['revenue_growth_cagr']:.2%} (CAGR)", f"{rv['model']['terminal_ebit_margin']:.2%}"]},
            index=["Constant revenue growth", "Terminal EBIT margin"]), width="stretch")
        st.caption("Each driver is solved alone so the DCF price equals the current price; all other assumptions "
                   "(including the sliders above) are held fixed.")

    with st.expander("Projection drivers & data quality"):
        a = pack["assumptions"]["values"]
        st.dataframe(pd.DataFrame({key: a[key] for key in ("revenue_growth", "ebit_margin", "tax_rate",
                                   "da_pct_revenue", "capex_pct_revenue", "nwc_pct_revenue")},
                                  index=[f"Year {i + 1}" for i in range(len(a["revenue_growth"]))]).T
                     .style.format("{:.2%}"), width="stretch")
        dq = [d for d in pack["data_quality"] if d["issue"] != "info"]
        st.write(pd.DataFrame(dq) if dq else "No data-quality flags.")

    # Downloads (built from the current slider state)
    buf = io.BytesIO()
    build_workbook(pack)[0].save(buf)
    d1, d2 = st.columns(2)
    d1.download_button("Download model.xlsx", buf.getvalue(), file_name=f"{ticker}_model.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       width="stretch")
    d2.download_button("Download memo.md", render(pack, TEMPLATE.read_text()), file_name=f"{ticker}_memo.md",
                       mime="text/markdown", width="stretch")


if __name__ == "__main__":
    _app()
