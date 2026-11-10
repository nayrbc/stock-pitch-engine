"""Write outputs/<TICKER>/model.xlsx: a live-formula model that reproduces the Phase 1 math.

Tabs: Inputs · Historicals · DCF · Comps · Sensitivity · Reverse DCF · Summary.
Blue font = hard-coded inputs; black = formulas. Formulas reference input cells only; the only
literals inside formulas are structural constants (1 in 1+r, 0.5 mid-year offset, 0 in guards).
Money is stored in absolute USD and displayed in $M via number format, so no unit constants in formulas.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as col

from pitch.memo import scenarios

BLUE = Font(name="Calibri", size=11, color="0000FF")
BLACK = Font(name="Calibri", size=11, color="000000")
BOLD = Font(name="Calibri", size=11, bold=True)
TITLE = Font(name="Calibri", size=14, bold=True)
HEADER_FILL = PatternFill("solid", fgColor="DDE4EE")
KEY_FILL = PatternFill("solid", fgColor="FFF2CC")
TOP = Border(top=Side(style="thin"))

USD_M = '#,##0,,;(#,##0,,);"-"'          # absolute USD shown in $M
SHARES_M = '#,##0,,'
PRICE = '$#,##0.00;($#,##0.00)'
PCT = '0.0%;(0.0%)'
MULT = '0.0"x"'
NUM = '0.00'
INT = '0'


def _isnum(*refs: str) -> str:
    return "AND(" + ",".join(f"ISNUMBER({r})" for r in refs) + ")"


def _guard(expr: str, *refs: str, extra: str | None = None) -> str:
    cond = _isnum(*refs) if not extra else f"AND({_isnum(*refs)},{extra})"
    return f'=IF({cond},{expr},"n/a")'


class _Sheet:
    def __init__(self, ws):
        self.ws = ws

    def inp(self, ref, value, fmt=None, note=None):
        c = self.ws[ref]
        c.value = value
        c.font = BLUE
        if fmt:
            c.number_format = fmt
        if value is None:
            c.comment = Comment(note or "Missing in source data (see data_quality). Left blank, not 0.", "pitch")
        return c

    def f(self, ref, formula, fmt=None, bold=False):
        c = self.ws[ref]
        c.value = formula
        c.font = Font(name="Calibri", size=11, color="000000", bold=bold)
        if fmt:
            c.number_format = fmt
        return c

    def label(self, ref, text, bold=False, fill=None):
        c = self.ws[ref]
        c.value = text
        c.font = BOLD if bold else BLACK
        if fill:
            c.fill = fill
        return c

    def title(self, text, sub=None):
        self.ws["A1"].value = text
        self.ws["A1"].font = TITLE
        if sub:
            self.ws["A2"].value = sub
            self.ws["A2"].font = Font(italic=True, color="666666")


def _abs(sheet: str, ref: str) -> str:
    c = "".join(ch for ch in ref if ch.isalpha())
    r = "".join(ch for ch in ref if ch.isdigit())
    return f"{sheet}!${c}${r}"


# ---------------------------------------------------------------- Inputs

def _inputs(wb, pack, R, D):
    ws = wb.create_sheet("Inputs")
    s = _Sheet(ws)
    s.title(f"{pack['meta']['name']} ({pack['meta']['ticker']}): Inputs",
            "Blue = input (change me). Black = formula. Every assumption in the model lives on this tab.")
    w, a, b = pack["wacc"], pack["assumptions"], pack["dcf"]["bridge_inputs"]
    cfg, srcs, ws_src = a["config"], a["sources"], w.get("sources", {})
    mkt = pack["market"]
    row = 4

    def section(name):
        nonlocal row
        row += 1
        s.label(f"A{row}", name, bold=True, fill=HEADER_FILL)
        for c in "BC":
            ws[f"{c}{row}"].fill = HEADER_FILL
        row += 1

    def put(key, label, value=None, fmt=None, source="", formula=None, bold=False):
        nonlocal row
        s.label(f"A{row}", label, bold=bold)
        if formula is not None:
            s.f(f"B{row}", formula, fmt, bold=bold)
        else:
            s.inp(f"B{row}", value, fmt)
        ws[f"C{row}"].value = source
        ws[f"C{row}"].font = Font(italic=True, color="666666")
        R[key] = _abs("Inputs", f"B{row}")
        row += 1

    section("Market data & capital structure")
    put("ticker", "Ticker", pack["meta"]["ticker"], source=", ".join(pack["meta"]["peers"]) and f"Peers: {', '.join(pack['meta']['peers'])}")
    put("price", "Current share price", mkt.get("price"), PRICE, "yfinance")
    put("shares", "Diluted shares", b["diluted_shares"], SHARES_M, b.get("diluted_shares_source", ""))
    put("market_cap", "Market cap ($M)", mkt.get("market_cap"), USD_M, "yfinance")
    put("total_debt", "Total debt ($M)", b["total_debt"], USD_M, f"EDGAR {pack['historicals']['fiscal_years'][-1]}")
    put("cash", "Cash & equivalents ($M)", b["cash"], USD_M, f"EDGAR {pack['historicals']['fiscal_years'][-1]}")
    put("sti", "Short-term investments ($M)", b["short_term_investments"], USD_M,
        "EDGAR" if b["short_term_investments"] is not None else "not reported (blank, excluded)")
    put("net_debt", "Net debt ($M)", fmt=USD_M, source="debt - cash - ST investments",
        formula=f"={R['total_debt']}-{R['cash']}-N({R['sti']})")

    section("WACC")
    put("rf", "Risk-free rate", w["risk_free_rate"], PCT, ws_src.get("risk_free_rate", "config"))
    put("beta", "Beta", w["beta"], NUM, ws_src.get("beta", ""))
    put("erp", "Equity risk premium", w["equity_risk_premium"], PCT, ws_src.get("equity_risk_premium", "config"))
    put("kd", "Pre-tax cost of debt", w["pre_tax_cost_of_debt"], PCT, ws_src.get("pre_tax_cost_of_debt", ""))
    tax_y1_row = None  # filled after driver table exists; placeholder formula set below
    put("tax_wacc", "Tax rate (Year 1 driver)", fmt=PCT, source="links to driver table", formula="=0")
    tax_wacc_cell = ws[R["tax_wacc"].split("!")[1].replace("$", "")]
    put("ke", "Cost of equity", fmt=PCT, source="rf + beta x ERP", formula=f"={R['rf']}+{R['beta']}*{R['erp']}")
    put("kd_at", "After-tax cost of debt", fmt=PCT, formula=f"={R['kd']}*(1-{R['tax_wacc']})")
    put("we", "Equity weight", fmt=PCT, source="market cap / (market cap + debt)",
        formula=f"={R['market_cap']}/({R['market_cap']}+{R['total_debt']})")
    put("wd", "Debt weight", fmt=PCT, formula=f"={R['total_debt']}/({R['market_cap']}+{R['total_debt']})")
    put("wacc_calc", "WACC (calculated)", fmt=PCT, formula=f"={R['we']}*{R['ke']}+{R['wd']}*{R['kd_at']}")
    put("wacc_override", "WACC override (leave blank to use calculated)", w.get("wacc_override"), PCT, "optional input")
    ws[R["wacc_override"].split("!")[1].replace("$", "")].comment = None
    put("wacc", "WACC used", fmt=PCT, bold=True,
        formula=f"=IF(ISNUMBER({R['wacc_override']}),{R['wacc_override']},{R['wacc_calc']})")
    ws[R["wacc"].split("!")[1].replace("$", "")].fill = KEY_FILL

    section("DCF")
    put("g", "Terminal growth", a["values"]["terminal_growth"], PCT, srcs.get("terminal_growth", "config"))
    put("mid", "Mid-year convention (1 = on, 0 = off)", 1 if a["values"]["mid_year_convention"] else 0, INT, "config")
    if str(srcs.get("exit_ev_ebitda", "")).startswith("peer median"):
        put("exit_mult", "Exit EV/EBITDA multiple", fmt=MULT, source="links to peer median EV/EBITDA (Comps tab)",
            formula="=0")  # patched once Comps exists
    else:
        put("exit_mult", "Exit EV/EBITDA multiple", a["values"]["exit_ev_ebitda"], MULT, srcs.get("exit_ev_ebitda", ""))

    section("Sensitivity steps")
    sc = cfg["sensitivity"]
    put("wacc_step", "WACC step", sc["wacc_step"], PCT, "config")
    put("g_step", "Terminal growth step", sc["terminal_growth_step"], PCT, "config")
    put("mult_step", "Exit multiple step", sc["exit_multiple_step"], MULT, "config")

    section("Rating thresholds")
    mc = {"buy_threshold": 0.15, "sell_threshold": -0.15, **cfg.get("memo", {})}
    put("buy", "BUY if upside >=", mc["buy_threshold"], PCT, "config")
    put("sell", "SELL if upside <=", mc["sell_threshold"], PCT, "config")

    # Driver table
    row += 1
    n = len(a["values"]["revenue_growth"])
    s.label(f"A{row}", "Projection drivers", bold=True, fill=HEADER_FILL)
    for t in range(n):
        c = s.label(f"{col(2 + t)}{row}", f"Year {t + 1}", bold=True, fill=HEADER_FILL)
        c.alignment = Alignment(horizontal="right")
    s.label(f"{col(2 + n)}{row}", "Source", bold=True, fill=HEADER_FILL)
    row += 1
    drivers = [("revenue_growth", "Revenue growth"), ("ebit_margin", "EBIT margin"), ("tax_rate", "Tax rate"),
               ("da_pct_revenue", "D&A % revenue"), ("capex_pct_revenue", "Capex % revenue"),
               ("nwc_pct_revenue", "Operating NWC % revenue")]
    for key, label in drivers:
        s.label(f"A{row}", label)
        D[key] = []
        for t, v in enumerate(a["values"][key]):
            ref = f"{col(2 + t)}{row}"
            s.inp(ref, v, PCT)
            D[key].append(_abs("Inputs", ref))
        ws[f"{col(2 + n)}{row}"].value = srcs.get(key, "")
        ws[f"{col(2 + n)}{row}"].font = Font(italic=True, color="666666")
        row += 1
    tax_wacc_cell.value = f"={D['tax_rate'][0]}"

    ws.column_dimensions["A"].width = 44
    ws.column_dimensions["B"].width = 14
    for t in range(n):
        ws.column_dimensions[col(3 + t)].width = 14
    ws.column_dimensions[col(2 + n)].width = 60
    ws.column_dimensions["C"].width = max(ws.column_dimensions["C"].width or 0, 14)
    ws.freeze_panes = "B4"
    return ws


# ---------------------------------------------------------------- Historicals

HIST_ROWS = [
    ("revenue", "Revenue", USD_M), ("gross_profit", "Gross profit", USD_M), ("ebit", "EBIT", USD_M),
    ("da", "D&A", USD_M), ("interest", "Interest expense", USD_M), ("pretax_income", "Pre-tax income", USD_M),
    ("tax", "Income tax", USD_M), ("net_income", "Net income", USD_M), ("cfo", "Cash from operations", USD_M),
    ("capex", "Capex", USD_M), ("sbc", "Stock-based comp", USD_M), ("cash", "Cash & equivalents", USD_M),
    ("short_term_investments", "Short-term investments", USD_M), ("total_debt", "Total debt", USD_M),
    ("current_debt", "Current debt (incl. ST borrowings)", USD_M), ("current_assets", "Current assets", USD_M),
    ("current_liabilities", "Current liabilities", USD_M), ("shares_diluted", "Diluted shares (M)", SHARES_M),
]


def _historicals(wb, pack, H):
    ws = wb.create_sheet("Historicals")
    s = _Sheet(ws)
    h = pack["historicals"]
    s.title(f"{pack['meta']['name']}: Historicals ($M)", "Source: SEC EDGAR 10-K XBRL. Blank = not reported (flagged).")
    n = len(h["periods"])
    cols = [col(2 + i) for i in range(n)]
    avg_col = col(2 + n)
    s.label("A4", "Fiscal year", bold=True, fill=HEADER_FILL)
    s.label("A5", "Period end", fill=HEADER_FILL)
    for c, fy, p in zip(cols, h["fiscal_years"], h["periods"]):
        s.label(f"{c}4", fy, bold=True, fill=HEADER_FILL).alignment = Alignment(horizontal="right")
        s.label(f"{c}5", p, fill=HEADER_FILL).alignment = Alignment(horizontal="right")
    s.label(f"{avg_col}4", "Average (EBIT margin: median)", bold=True, fill=HEADER_FILL)
    row = 6
    for key, label, fmt in HIST_ROWS:
        s.label(f"A{row}", label)
        H[key] = []
        for i, c in enumerate(cols):
            v = h["items"][key][i]
            src = h["sources"][key][i]
            cell = s.inp(f"{c}{row}", v, fmt)
            if v is not None and src and src.startswith("derived"):
                cell.comment = Comment(src, "pitch")
            H[key].append(f"Historicals!{c}{row}")
        row += 1

    row += 1
    s.label(f"A{row}", "Ratios & derived", bold=True, fill=HEADER_FILL)
    row += 1

    def ratio(key, label, fmt, build, start=0, average=True, stat="AVERAGE"):
        nonlocal row
        s.label(f"A{row}", label)
        H[key] = [None] * n
        for i in range(start, n):
            s.f(f"{cols[i]}{row}", build(i), fmt)
            H[key][i] = f"Historicals!{cols[i]}{row}"
        if average:
            s.f(f"{avg_col}{row}", f'=IFERROR({stat}({cols[start]}{row}:{cols[-1]}{row}),"n/a")', fmt)
        row += 1

    I = H
    ratio("rev_growth", "Revenue growth", PCT,
          lambda i: _guard(f"{I['revenue'][i]}/{I['revenue'][i - 1]}-1", I["revenue"][i], I["revenue"][i - 1]), start=1)
    ratio("gross_margin", "Gross margin", PCT,
          lambda i: _guard(f"{I['gross_profit'][i]}/{I['revenue'][i]}", I["gross_profit"][i], I["revenue"][i]))
    ratio("ebit_margin", "EBIT margin", PCT,
          lambda i: _guard(f"{I['ebit'][i]}/{I['revenue'][i]}", I["ebit"][i], I["revenue"][i]), stat="MEDIAN")
    ratio("ebitda", "EBITDA", USD_M, lambda i: _guard(f"{I['ebit'][i]}+{I['da'][i]}", I["ebit"][i], I["da"][i]),
          average=False)
    ratio("tax_rate", "Effective tax rate", PCT,
          lambda i: _guard(f"{I['tax'][i]}/{I['pretax_income'][i]}", I["tax"][i], I["pretax_income"][i],
                           extra=f"{I['pretax_income'][i]}>0"))
    ratio("da_pct", "D&A % revenue", PCT, lambda i: _guard(f"{I['da'][i]}/{I['revenue'][i]}", I["da"][i], I["revenue"][i]))
    ratio("capex_pct", "Capex % revenue", PCT,
          lambda i: _guard(f"{I['capex'][i]}/{I['revenue'][i]}", I["capex"][i], I["revenue"][i]))
    ratio("nwc", "Operating NWC", USD_M, lambda i: _guard(
        f"({I['current_assets'][i]}-{I['cash'][i]}-N({I['short_term_investments'][i]}))"
        f"-({I['current_liabilities'][i]}-N({I['current_debt'][i]}))",
        I["current_assets"][i], I["cash"][i], I["current_liabilities"][i]), average=False)
    ratio("nwc_pct", "Operating NWC % revenue", PCT,
          lambda i: _guard(f"{I['nwc'][i]}/{I['revenue'][i]}", I["nwc"][i], I["revenue"][i]))
    ratio("fcf", "FCF (CFO - capex)", USD_M, lambda i: _guard(f"{I['cfo'][i]}-{I['capex'][i]}", I["cfo"][i], I["capex"][i]),
          average=False)
    ratio("sbc_pct", "SBC % revenue", PCT, lambda i: _guard(f"{I['sbc'][i]}/{I['revenue'][i]}", I["sbc"][i], I["revenue"][i]))

    ws.column_dimensions["A"].width = 36
    for c in cols + [avg_col]:
        ws.column_dimensions[c].width = 13
    ws.freeze_panes = "B6"
    return ws


# ---------------------------------------------------------------- DCF

def _dcf(wb, pack, R, D, H, X):
    ws = wb.create_sheet("DCF")
    s = _Sheet(ws)
    s.title(f"{pack['meta']['name']}: Unlevered DCF ($M)",
            "FCFF = EBIT x (1 - t) + D&A - capex - change in NWC. Terminal value discounted N full years.")
    n = len(D["revenue_growth"])
    base_fy = int(pack["historicals"]["fiscal_years"][-1][2:])
    cols = [col(3 + t) for t in range(n)]  # C..
    last = cols[-1]
    s.label("A4", "", fill=HEADER_FILL)
    s.label("B4", f"FY{base_fy}A", bold=True, fill=HEADER_FILL).alignment = Alignment(horizontal="right")
    for t, c in enumerate(cols):
        s.label(f"{c}4", f"FY{base_fy + t + 1}E", bold=True, fill=HEADER_FILL).alignment = Alignment(horizontal="right")

    rows = {}
    labels = [("year", "Projection year"), ("revenue", "Revenue"), ("growth", "  growth"), ("margin", "  EBIT margin"),
              ("ebit", "EBIT"), ("tax_rate", "  tax rate"), ("taxes", "Taxes on EBIT"), ("nopat", "NOPAT"),
              ("da_pct", "  D&A % revenue"), ("da", "D&A"), ("capex_pct", "  capex % revenue"), ("capex", "Capex"),
              ("nwc_pct", "  NWC % revenue"), ("dnwc", "Change in NWC"), ("fcff", "Unlevered FCF"),
              ("ebitda", "EBITDA"), ("period", "Discount period"), ("df", "Discount factor"), ("pv", "PV of FCF")]
    for i, (k, lab) in enumerate(labels):
        rows[k] = 5 + i
        s.label(f"A{rows[k]}", lab, bold=k in ("revenue", "fcff", "pv"))

    r = rows
    s.inp(f"B{r['year']}", 0, INT)
    s.f(f"B{r['revenue']}", f"={H['revenue'][-1]}", USD_M)
    s.f(f"B{r['ebit']}", f"={H['ebit'][-1]}", USD_M)
    s.f(f"B{r['margin']}", f"={H['ebit_margin'][-1]}", PCT)
    for t, c in enumerate(cols):
        p = col(2 + t)  # previous column
        s.inp(f"{c}{r['year']}", t + 1, INT)
        s.f(f"{c}{r['growth']}", f"={D['revenue_growth'][t]}", PCT)
        s.f(f"{c}{r['revenue']}", f"={p}{r['revenue']}*(1+{c}{r['growth']})", USD_M)
        s.f(f"{c}{r['margin']}", f"={D['ebit_margin'][t]}", PCT)
        s.f(f"{c}{r['ebit']}", f"={c}{r['revenue']}*{c}{r['margin']}", USD_M)
        s.f(f"{c}{r['tax_rate']}", f"={D['tax_rate'][t]}", PCT)
        s.f(f"{c}{r['taxes']}", f"={c}{r['ebit']}*{c}{r['tax_rate']}", USD_M)
        s.f(f"{c}{r['nopat']}", f"={c}{r['ebit']}-{c}{r['taxes']}", USD_M)
        s.f(f"{c}{r['da_pct']}", f"={D['da_pct_revenue'][t]}", PCT)
        s.f(f"{c}{r['da']}", f"={c}{r['revenue']}*{c}{r['da_pct']}", USD_M)
        s.f(f"{c}{r['capex_pct']}", f"={D['capex_pct_revenue'][t]}", PCT)
        s.f(f"{c}{r['capex']}", f"={c}{r['revenue']}*{c}{r['capex_pct']}", USD_M)
        s.f(f"{c}{r['nwc_pct']}", f"={D['nwc_pct_revenue'][t]}", PCT)
        s.f(f"{c}{r['dnwc']}", f"={c}{r['nwc_pct']}*({c}{r['revenue']}-{p}{r['revenue']})", USD_M)
        s.f(f"{c}{r['fcff']}", f"={c}{r['nopat']}+{c}{r['da']}-{c}{r['capex']}-{c}{r['dnwc']}", USD_M, bold=True)
        s.f(f"{c}{r['ebitda']}", f"={c}{r['ebit']}+{c}{r['da']}", USD_M)
        s.f(f"{c}{r['period']}", f"={c}{r['year']}-IF({R['mid']}=1,0.5,0)", NUM)
        s.f(f"{c}{r['df']}", f"=1/(1+{R['wacc']})^{c}{r['period']}", "0.0000")
        s.f(f"{c}{r['pv']}", f"={c}{r['fcff']}*{c}{r['df']}", USD_M, bold=True)
    for k in ("fcff", "pv"):
        for c in ["A", "B"] + cols:
            ws[f"{c}{r[k]}"].border = TOP

    # Valuation block
    v = r["pv"] + 2
    s.label(f"A{v}", "Valuation", bold=True, fill=HEADER_FILL)
    s.label(f"B{v}", "Perpetual growth", bold=True, fill=HEADER_FILL)
    s.label(f"C{v}", "Exit multiple", bold=True, fill=HEADER_FILL)
    lines = ["wacc", "g", "mult", "n", "sum_pv", "tv_df", "tv", "pv_tv", "ev", "net_debt", "equity", "shares",
             "price", "current", "upside", "tv_pct", "cross"]
    V = {k: v + 1 + i for i, k in enumerate(lines)}
    labels = {"wacc": "WACC", "g": "Terminal growth", "mult": "Exit EV/EBITDA", "n": "Years to terminal (N)",
              "sum_pv": "Sum of PV of FCF", "tv_df": "Terminal discount factor", "tv": "Terminal value",
              "pv_tv": "PV of terminal value", "ev": "Enterprise value", "net_debt": "Less: net debt",
              "equity": "Equity value", "shares": "Diluted shares", "price": "Implied share price",
              "current": "Current share price", "upside": "Upside / (downside)", "tv_pct": "PV of TV % of EV",
              "cross": "Implied exit multiple / implied perpetual growth"}
    for k, rr in V.items():
        s.label(f"A{rr}", labels[k], bold=k in ("ev", "price"))
    fcf_n, ebitda_n = f"${last}${r['fcff']}", f"${last}${r['ebitda']}"
    for c in "BC":
        s.f(f"{c}{V['wacc']}", f"={R['wacc']}", PCT)
        s.f(f"{c}{V['n']}", f"=${last}${r['year']}", INT)
        s.f(f"{c}{V['sum_pv']}", f"=SUM($C${r['pv']}:${last}${r['pv']})", USD_M)
        s.f(f"{c}{V['tv_df']}", f"=1/(1+{c}{V['wacc']})^{c}{V['n']}", "0.0000")
        s.f(f"{c}{V['pv_tv']}", _guard(f"{c}{V['tv']}*{c}{V['tv_df']}", f"{c}{V['tv']}"), USD_M)
        s.f(f"{c}{V['ev']}", _guard(f"{c}{V['sum_pv']}+{c}{V['pv_tv']}", f"{c}{V['pv_tv']}"), USD_M, bold=True)
        s.f(f"{c}{V['net_debt']}", f"={R['net_debt']}", USD_M)
        s.f(f"{c}{V['equity']}", _guard(f"{c}{V['ev']}-{c}{V['net_debt']}", f"{c}{V['ev']}", f"{c}{V['net_debt']}"), USD_M)
        s.f(f"{c}{V['shares']}", f"={R['shares']}", SHARES_M)
        s.f(f"{c}{V['price']}", _guard(f"{c}{V['equity']}/{c}{V['shares']}", f"{c}{V['equity']}", f"{c}{V['shares']}",
                                       extra=f"{c}{V['shares']}>0"), PRICE, bold=True)
        ws[f"{c}{V['price']}"].fill = KEY_FILL
        s.f(f"{c}{V['current']}", f"={R['price']}", PRICE)
        s.f(f"{c}{V['upside']}", _guard(f"{c}{V['price']}/{c}{V['current']}-1", f"{c}{V['price']}", f"{c}{V['current']}",
                                        extra=f"{c}{V['current']}>0"), PCT)
        s.f(f"{c}{V['tv_pct']}", _guard(f"{c}{V['pv_tv']}/{c}{V['ev']}", f"{c}{V['pv_tv']}", f"{c}{V['ev']}"), PCT)
    s.f(f"B{V['g']}", f"={R['g']}", PCT)
    s.f(f"C{V['mult']}", f"={R['exit_mult']}", MULT)
    s.f(f"B{V['tv']}", f'=IF(B{V["wacc"]}>B{V["g"]},{fcf_n}*(1+B{V["g"]})/(B{V["wacc"]}-B{V["g"]}),"n/a")', USD_M)
    s.f(f"C{V['tv']}", f"={ebitda_n}*C{V['mult']}", USD_M)
    s.f(f"B{V['cross']}", _guard(f"B{V['tv']}/{ebitda_n}", f"B{V['tv']}"), MULT)
    s.f(f"C{V['cross']}", f"=(C{V['tv']}*C{V['wacc']}-{fcf_n})/(C{V['tv']}+{fcf_n})", PCT)

    X.update(rows=r, V=V, cols=cols, last=last)
    X["price_gordon"], X["price_exit"] = f"DCF!$B${V['price']}", f"DCF!$C${V['price']}"
    X["rev_base"], X["rev_last"] = f"DCF!$B${r['revenue']}", f"DCF!${last}${r['revenue']}"
    X["upside_gordon"], X["upside_exit"] = f"DCF!$B${V['upside']}", f"DCF!$C${V['upside']}"

    ws.column_dimensions["A"].width = 44
    for c in ["B"] + cols:
        ws.column_dimensions[c].width = 16
    ws.freeze_panes = "B5"
    return ws


# ---------------------------------------------------------------- Comps

def _comps(wb, pack, R, H, C):
    ws = wb.create_sheet("Comps")
    s = _Sheet(ws)
    comps = pack["comps"]
    s.title(f"{pack['meta']['name']}: Trading comps", comps.get("basis", ""))
    heads = ["Ticker", "Name", "Price", "Market cap", "Net debt", "EV", "Revenue", "EBITDA", "Net income",
             "EV/Revenue", "EV/EBITDA", "P/E", "Revenue growth", "EBIT margin"]
    K = {h: col(1 + i) for i, h in enumerate(heads)}
    for h, c in K.items():
        s.label(f"{c}4", h, bold=True, fill=HEADER_FILL)

    def multiples(r):
        e, m = f"{K['EV']}{r}", f"{K['Market cap']}{r}"
        rev, ebitda, ni = f"{K['Revenue']}{r}", f"{K['EBITDA']}{r}", f"{K['Net income']}{r}"
        s.f(e, _guard(f"{m}+{K['Net debt']}{r}", m, f"{K['Net debt']}{r}"), USD_M)
        s.f(f"{K['EV/Revenue']}{r}", _guard(f"{e}/{rev}", e, rev, extra=f"{rev}<>0"), MULT)
        s.f(f"{K['EV/EBITDA']}{r}", _guard(f"{e}/{ebitda}", e, ebitda, extra=f"{ebitda}>0"), MULT)
        s.f(f"{K['P/E']}{r}", _guard(f"{m}/{ni}", m, ni, extra=f"{ni}>0"), MULT)

    # Target row, linked to Inputs / Historicals
    t = 5
    s.label(f"A{t}", pack["meta"]["ticker"], bold=True)
    s.label(f"B{t}", comps["target"].get("name") or "", bold=True)
    s.f(f"{K['Price']}{t}", f"={R['price']}", PRICE)
    s.f(f"{K['Market cap']}{t}", f"={R['market_cap']}", USD_M)
    s.f(f"{K['Net debt']}{t}", f"={R['net_debt']}", USD_M)
    s.f(f"{K['Revenue']}{t}", f"={H['revenue'][-1]}", USD_M)
    s.f(f"{K['EBITDA']}{t}", f"={H['ebitda'][-1]}", USD_M)
    s.f(f"{K['Net income']}{t}", f"={H['net_income'][-1]}", USD_M)
    s.f(f"{K['Revenue growth']}{t}", f"={H['rev_growth'][-1]}", PCT)
    s.f(f"{K['EBIT margin']}{t}", f"={H['ebit_margin'][-1]}", PCT)
    multiples(t)

    s.label(f"A{t + 1}", "Peers", bold=True, fill=HEADER_FILL)
    first = t + 2
    peers = comps["peers"]
    for i, p in enumerate(peers):
        r = first + i
        s.label(f"A{r}", p["ticker"])
        s.label(f"B{r}", p.get("name") or "")
        for h, key, fmt in (("Price", "price", PRICE), ("Market cap", "market_cap", USD_M), ("Net debt", "net_debt", USD_M),
                            ("Revenue", "revenue", USD_M), ("EBITDA", "ebitda", USD_M), ("Net income", "net_income", USD_M),
                            ("Revenue growth", "revenue_growth", PCT), ("EBIT margin", "ebit_margin", PCT)):
            s.inp(f"{K[h]}{r}", p.get(key), fmt)
        multiples(r)
    lastp = first + max(len(peers), 1) - 1

    stats = lastp + 2
    s.label(f"A{stats}", "Peer median", bold=True)
    s.label(f"A{stats + 1}", "Peer mean", bold=True)
    for h in ("EV/Revenue", "EV/EBITDA", "P/E", "Revenue growth", "EBIT margin"):
        c, fmt = K[h], PCT if h in ("Revenue growth", "EBIT margin") else MULT
        rng = f"{c}{first}:{c}{lastp}"
        s.f(f"{c}{stats}", f'=IF(COUNT({rng})=0,"n/a",MEDIAN({rng}))', fmt, bold=True)
        s.f(f"{c}{stats + 1}", f'=IF(COUNT({rng})=0,"n/a",AVERAGE({rng}))', fmt)
        ws[f"{c}{stats}"].border = TOP
    C["median"] = {h: f"Comps!${K[h]}${stats}" for h in ("EV/Revenue", "EV/EBITDA", "P/E")}

    # Implied valuation at peer median
    b = stats + 3
    for i, h in enumerate(["Implied value at peer median", "Peer median", "Target metric", "Implied equity value",
                           "Implied share price", "Upside / (downside)"]):
        s.label(f"{col(1 + i)}{b}", h, bold=True, fill=HEADER_FILL)
    C["implied"] = []
    for j, (h, metric) in enumerate((("EV/Revenue", "Revenue"), ("EV/EBITDA", "EBITDA"), ("P/E", "Net income"))):
        r = b + 1 + j
        med, met = f"B{r}", f"C{r}"
        s.label(f"A{r}", h)
        s.f(med, f"={C['median'][h]}", MULT)
        s.f(met, f"={K[metric]}{t}", USD_M)
        eq = f"{med}*{met}" if h == "P/E" else f"{med}*{met}-{R['net_debt']}"
        refs = (med, met) if h == "P/E" else (med, met, R["net_debt"])
        s.f(f"D{r}", _guard(eq, *refs), USD_M)
        s.f(f"E{r}", _guard(f"D{r}/{R['shares']}", f"D{r}", R["shares"]), PRICE, bold=True)
        s.f(f"F{r}", _guard(f"E{r}/{R['price']}-1", f"E{r}", R["price"]), PCT)
        C["implied"].append(f"Comps!$E${r}")
    lo = b + 4
    s.label(f"A{lo}", "Range low", bold=True)
    s.label(f"A{lo + 1}", "Range high", bold=True)
    rng = f"E{b + 1}:E{b + 3}"
    s.f(f"E{lo}", f'=IF(COUNT({rng})=0,"n/a",MIN({rng}))', PRICE, bold=True)
    s.f(f"E{lo + 1}", f'=IF(COUNT({rng})=0,"n/a",MAX({rng}))', PRICE, bold=True)
    C["low"], C["high"] = f"Comps!$E${lo}", f"Comps!$E${lo + 1}"

    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 28
    for h in heads[2:]:
        ws.column_dimensions[K[h]].width = 14
    ws.freeze_panes = "C5"
    return ws


# ---------------------------------------------------------------- Sensitivity

def _sensitivity(wb, pack, R, X, S):
    ws = wb.create_sheet("Sensitivity")
    s = _Sheet(ws)
    s.title(f"{pack['meta']['name']}: Implied share price sensitivity",
            "Each cell re-runs the DCF from the DCF tab's FCF row at that WACC and terminal assumption.")
    k = pack["assumptions"]["config"]["sensitivity"]["steps_each_side"]
    size = 2 * k + 1
    r, cols, last = X["rows"], X["cols"], X["last"]
    n_ref = f"DCF!${last}${r['year']}"

    def pv_sum(w):
        return "+".join(f"DCF!${c}${r['fcff']}/(1+{w})^DCF!${c}${r['period']}" for c in cols)

    def grid(top, title, axis_label, center, step, fmt, tv_expr, key):
        s.label(f"A{top}", title, bold=True)
        hdr = top + 2
        s.label(f"A{hdr - 1}", "step", fill=HEADER_FILL)
        s.label(f"B{hdr - 1}", "", fill=HEADER_FILL)
        s.label(f"B{hdr}", f"WACC \\ {axis_label}", bold=True, fill=HEADER_FILL)
        gcols = [col(3 + j) for j in range(size)]
        for j, c in enumerate(gcols):
            s.inp(f"{c}{hdr - 1}", j - k, INT)
            s.f(f"{c}{hdr}", f"={center}+{c}${hdr - 1}*{step}", fmt, bold=True)
            ws[f"{c}{hdr}"].fill = HEADER_FILL
        cells = []
        for i in range(size):
            rr = hdr + 1 + i
            s.inp(f"A{rr}", i - k, INT)
            s.f(f"B{rr}", f"={R['wacc']}+$A{rr}*{R['wacc_step']}", PCT, bold=True)
            ws[f"B{rr}"].fill = HEADER_FILL
            row_cells = []
            for c in gcols:
                w, x = f"$B{rr}", f"{c}${hdr}"
                tv, cond = tv_expr(w, x)
                body = f"(({pv_sum(w)})+{tv}/(1+{w})^{n_ref}-{R['net_debt']})/{R['shares']}"
                s.f(f"{c}{rr}", f'=IF({cond},{body},"n/a")', PRICE)
                if i == k and c == gcols[k]:
                    ws[f"{c}{rr}"].fill = KEY_FILL
                row_cells.append(f"Sensitivity!${c}${rr}")
            cells.append(row_cells)
        S[key] = {"cells": cells, "range": f"Sensitivity!$C${hdr + 1}:${gcols[-1]}${hdr + size}"}
        return hdr + size + 2

    fcf_n, ebitda_n = f"DCF!${last}${r['fcff']}", f"DCF!${last}${r['ebitda']}"
    nxt = grid(4, "WACC x terminal growth (perpetual growth method)", "g", R["g"], R["g_step"], PCT,
               lambda w, g: (f"{fcf_n}*(1+{g})/({w}-{g})", f"AND({w}>0,{w}>{g})"), "gordon")
    grid(nxt + 1, "WACC x exit EV/EBITDA (exit multiple method)", "exit multiple", R["exit_mult"], R["mult_step"], MULT,
         lambda w, m: (f"{ebitda_n}*{m}", f"AND({w}>0,{m}>0)"), "exit")

    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 18
    for j in range(size):
        ws.column_dimensions[col(3 + j)].width = 13
    return ws


# ---------------------------------------------------------------- Reverse DCF

def _reverse(wb, pack, R, D, H, X, RV):
    """Solved drivers (blue, from Python bisection) + a live check DCF per driver that re-prices the stock
    at the solved value. Each check's 'gap vs current price' should be ~0; Goal Seek it on the blue driver."""
    ws = wb.create_sheet("Reverse DCF")
    s = _Sheet(ws)
    rv = pack["reverse_dcf"]
    s.title(f"{pack['meta']['name']}: Reverse DCF (what the current price implies)",
            "Solved one driver at a time, all other drivers as on Inputs. Blue = solved value; "
            "re-solve in Excel with Goal Seek: set 'Gap vs current price' to 0 by changing the blue driver.")
    r, cols, last = X["rows"], X["cols"], X["last"]
    n_ref, base_col = f"DCF!${last}${r['year']}", "B"

    # Solved table
    s.label("A4", "Solved driver", bold=True, fill=HEADER_FILL)
    heads = ["Price target basis", "Perpetual growth only", "Exit multiple only", "Model", "Gap (PT basis - model)"]
    for j, h in enumerate(heads):
        s.label(f"{col(2 + j)}4", h, bold=True, fill=HEADER_FILL).alignment = Alignment(horizontal="right")
    s.label("A5", "Constant revenue growth (all years)")
    s.label("A6", f"Terminal (year-{len(cols)}) EBIT margin")
    s.f("E5", f"=({X['rev_last']}/{X['rev_base']})^(1/{n_ref})-1", PCT)
    s.f("E6", f"={D['ebit_margin'][-1]}", PCT)
    for rr, key in ((5, "revenue_growth"), (6, "ebit_margin")):
        for c, basis in (("C", "gordon"), ("D", "exit_multiple")):
            s.inp(f"{c}{rr}", rv[key][basis], PCT, note="No solution within search bounds.")
        s.f(f"F{rr}", _guard(f"B{rr}-E{rr}", f"B{rr}", f"E{rr}"), PCT)
    s.label("A7", "Model column: implied CAGR of the projected revenue path; year-N margin from Inputs.")
    ws["A7"].font = Font(italic=True, color="666666")

    def check(top, title, key, driver_label):
        s.label(f"A{top}", title, bold=True, fill=HEADER_FILL)
        s.label(f"B{top}", "Base", bold=True, fill=HEADER_FILL).alignment = Alignment(horizontal="right")
        for t, c in enumerate(cols):
            s.label(f"{c}{top}", f"Year {t + 1}", bold=True, fill=HEADER_FILL).alignment = Alignment(horizontal="right")
        drv = f"$B${top + 1}"
        s.label(f"A{top + 1}", driver_label, bold=True)
        s.inp(drv, rv[key]["price_target"], PCT, note="No solution within search bounds.")
        ws[drv].fill = KEY_FILL
        lab = ["growth", "revenue", "margin", "ebit", "nopat", "da", "capex", "dnwc", "fcff", "ebitda", "pv"]
        names = ["Revenue growth", "Revenue", "EBIT margin", "EBIT", "NOPAT", "D&A", "Capex", "Change in NWC",
                 "Unlevered FCF", "EBITDA", "PV of FCF"]
        L = {k: top + 2 + i for i, k in enumerate(lab)}
        for k, nm in zip(lab, names):
            s.label(f"A{L[k]}", nm, bold=k in ("fcff", "pv"))
        s.f(f"{base_col}{L['revenue']}", f"={H['revenue'][-1]}", USD_M)
        for t, c in enumerate(cols):
            p = col(2 + t)
            yr = f"DCF!{c}${r['year']}"
            s.f(f"{c}{L['growth']}", f"={drv}" if key == "revenue_growth" else f"={D['revenue_growth'][t]}", PCT)
            s.f(f"{c}{L['revenue']}", f"={p}{L['revenue']}*(1+{c}{L['growth']})", USD_M)
            m0 = D["ebit_margin"][0]
            s.f(f"{c}{L['margin']}", f"=IF({n_ref}=1,{drv},{m0}+({drv}-{m0})*({yr}-1)/({n_ref}-1))"
                if key == "ebit_margin" else f"={D['ebit_margin'][t]}", PCT)
            s.f(f"{c}{L['ebit']}", f"={c}{L['revenue']}*{c}{L['margin']}", USD_M)
            s.f(f"{c}{L['nopat']}", f"={c}{L['ebit']}*(1-{D['tax_rate'][t]})", USD_M)
            s.f(f"{c}{L['da']}", f"={c}{L['revenue']}*{D['da_pct_revenue'][t]}", USD_M)
            s.f(f"{c}{L['capex']}", f"={c}{L['revenue']}*{D['capex_pct_revenue'][t]}", USD_M)
            s.f(f"{c}{L['dnwc']}", f"={D['nwc_pct_revenue'][t]}*({c}{L['revenue']}-{p}{L['revenue']})", USD_M)
            s.f(f"{c}{L['fcff']}", f"={c}{L['nopat']}+{c}{L['da']}-{c}{L['capex']}-{c}{L['dnwc']}", USD_M, bold=True)
            s.f(f"{c}{L['ebitda']}", f"={c}{L['ebit']}+{c}{L['da']}", USD_M)
            s.f(f"{c}{L['pv']}", f"={c}{L['fcff']}/(1+{R['wacc']})^DCF!{c}${r['period']}", USD_M, bold=True)
        w, g, tvdf = R["wacc"], R["g"], f"(1+{R['wacc']})^{n_ref}"
        fcf_n, ebitda_n = f"{last}{L['fcff']}", f"{last}{L['ebitda']}"
        v = L["pv"] + 1
        out = [("sum", "Sum of PV of FCF", f"=SUM(C{L['pv']}:{last}{L['pv']})", USD_M),
               ("pg", "Implied price: perpetual growth",
                f'=IF({w}>{g},(B{v}+{fcf_n}*(1+{g})/({w}-{g})/{tvdf}-{R["net_debt"]})/{R["shares"]},"n/a")', PRICE),
               ("pe", "Implied price: exit multiple",
                f"=(B{v}+{ebitda_n}*{R['exit_mult']}/{tvdf}-{R['net_debt']})/{R['shares']}", PRICE),
               ("pt", "Implied price target (average)", f'=IFERROR(AVERAGE(B{v + 1},B{v + 2}),"n/a")', PRICE),
               ("cur", "Current share price", f"={R['price']}", PRICE),
               ("gap", "Gap vs current price (should be ~0)", _guard(f"B{v + 3}-B{v + 4}", f"B{v + 3}", f"B{v + 4}"), PRICE)]
        for i, (k, lbl, fml, fmt) in enumerate(out):
            s.label(f"A{v + i}", lbl, bold=k in ("pt", "gap"))
            s.f(f"B{v + i}", fml, fmt, bold=k in ("pt", "gap"))
        ws[f"B{v + 3}"].fill = KEY_FILL
        RV[key] = {"driver": f"'Reverse DCF'!{drv}", "price_target": f"'Reverse DCF'!$B${v + 3}",
                   "gap": f"'Reverse DCF'!$B${v + 5}"}
        return v + len(out) + 1

    nxt = check(9, "Check: constant revenue growth", "revenue_growth", "Solved constant revenue growth")
    check(nxt, "Check: terminal EBIT margin", "ebit_margin", "Solved terminal EBIT margin")
    for rr, key in ((5, "revenue_growth"), (6, "ebit_margin")):
        s.f(f"B{rr}", _guard(RV[key]["driver"], RV[key]["driver"]), PCT, bold=True)

    ws.column_dimensions["A"].width = 40
    for c in ["B"] + cols + ["F"]:
        ws.column_dimensions[c].width = 18
    return ws


# ---------------------------------------------------------------- Summary

def _summary(wb, pack, R, X, C, S, OUT):
    ws = wb["Summary"]
    s = _Sheet(ws)
    s.title(f"{pack['meta']['name']} ({pack['meta']['ticker']}): Valuation summary",
            f"Peers: {', '.join(pack['meta']['peers']) or 'none'} · generated {pack['meta']['generated_at']}")
    k = pack["assumptions"]["config"]["sensitivity"]["steps_each_side"]
    bull_rc, bear_rc = ((k - 1, k + 1), (k + 1, k - 1)) if k else ((0, 0), (0, 0))
    g, x = S["gordon"]["cells"], S["exit"]["cells"]
    rows = [
        ("current", "Current share price", f"={R['price']}", PRICE),
        ("gordon", "DCF: perpetual growth", f"={X['price_gordon']}", PRICE),
        ("gordon_up", "  upside", f"={X['upside_gordon']}", PCT),
        ("exit", "DCF: exit multiple", f"={X['price_exit']}", PRICE),
        ("exit_up", "  upside", f"={X['upside_exit']}", PCT),
        ("pt", "Price target (average of DCF methods)", f'=IFERROR(AVERAGE({X["price_gordon"]},{X["price_exit"]}),"n/a")', PRICE),
        ("pt_up", "  upside to price target", None, PCT),
        ("rating", "Rating", None, None),
        ("bull", "Bull case (WACC -1 step, g / multiple +1 step)",
         f'=IFERROR(AVERAGE({g[bull_rc[0]][bull_rc[1]]},{x[bull_rc[0]][bull_rc[1]]}),"n/a")', PRICE),
        ("bear", "Bear case (WACC +1 step, g / multiple -1 step)",
         f'=IFERROR(AVERAGE({g[bear_rc[0]][bear_rc[1]]},{x[bear_rc[0]][bear_rc[1]]}),"n/a")', PRICE),
        ("wacc", "WACC", f"={R['wacc']}", PCT),
        ("g", "Terminal growth", f"={R['g']}", PCT),
        ("mult", "Exit EV/EBITDA", f"={R['exit_mult']}", MULT),
    ]
    s.label("A4", "Key outputs", bold=True, fill=HEADER_FILL)
    ws["B4"].fill = HEADER_FILL
    pos = {}
    for i, (key, label, formula, fmt) in enumerate(rows):
        rr = 5 + i
        pos[key] = f"B{rr}"
        s.label(f"A{rr}", label, bold=key in ("pt", "rating"))
        if formula:
            s.f(f"B{rr}", formula, fmt, bold=key in ("pt", "rating"))
    pt, cur = pos["pt"], pos["current"]
    s.f(pos["pt_up"], _guard(f"{pt}/{cur}-1", pt, cur, extra=f"{cur}>0"), PCT)
    up = pos["pt_up"]
    s.f(pos["rating"], f'=IF(ISNUMBER({up}),IF({up}>={R["buy"]},"BUY",IF({up}<={R["sell"]},"SELL","HOLD")),"NOT RATED")',
        bold=True)
    for key in ("pt", "rating"):
        ws[pos[key]].fill = KEY_FILL

    # Football field
    top = 5 + len(rows) + 2
    for j, h in enumerate(["Football field", "Low", "High", "Spread"]):
        s.label(f"{col(1 + j)}{top}", h, bold=True, fill=HEADER_FILL)
    mkt = pack["market"]
    ff = [
        ("DCF: perpetual growth (WACC x g)", f"=MIN({S['gordon']['range']})", f"=MAX({S['gordon']['range']})"),
        ("DCF: exit multiple (WACC x multiple)", f"=MIN({S['exit']['range']})", f"=MAX({S['exit']['range']})"),
        ("Trading comps (peer medians)", f"={C['low']}", f"={C['high']}"),
        ("52-week trading range", mkt.get("week52_low"), mkt.get("week52_high")),
    ]
    for i, (label, lo, hi) in enumerate(ff):
        rr = top + 1 + i
        s.label(f"A{rr}", label)
        for c, v in (("B", lo), ("C", hi)):
            (s.f(f"{c}{rr}", v, PRICE) if isinstance(v, str) else s.inp(f"{c}{rr}", v, PRICE))
        s.f(f"D{rr}", _guard(f"C{rr}-B{rr}", f"B{rr}", f"C{rr}"), PRICE)
    first, lastr = top + 1, top + len(ff)

    chart = BarChart()
    chart.type = "bar"
    chart.grouping = "stacked"
    chart.overlap = 100
    chart.gapWidth = 60
    chart.title = f"{pack['meta']['ticker']} valuation football field ($/share)"
    chart.y_axis.title = "$ per share"
    chart.y_axis.majorGridlines = None
    chart.x_axis.scaling.orientation = "maxMin"
    chart.legend = None
    chart.add_data(Reference(ws, min_col=2, min_row=top, max_row=lastr), titles_from_data=True)
    chart.add_data(Reference(ws, min_col=4, min_row=top, max_row=lastr), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=first, max_row=lastr))
    chart.series[0].graphicalProperties.noFill = True
    chart.series[0].graphicalProperties.line.noFill = True
    chart.series[1].graphicalProperties.solidFill = "1F4E79"
    chart.height, chart.width = 8, 18
    ws.add_chart(chart, f"F{4}")

    OUT.update({k: f"Summary!{v}" for k, v in pos.items()})
    ws.column_dimensions["A"].width = 46
    for c in "BCD":
        ws.column_dimensions[c].width = 14
    return ws


# ---------------------------------------------------------------- entry points

def build_workbook(pack: dict) -> tuple[Workbook, dict]:
    """Build the model workbook from a pitch pack. Returns (workbook, refs to key output cells)."""
    wb = Workbook()
    wb.active.title = "Summary"
    R, D, H, X, C, S, OUT, RV = {}, {}, {}, {}, {}, {}, {}, {}
    _inputs(wb, pack, R, D)
    _historicals(wb, pack, H)
    _dcf(wb, pack, R, D, H, X)
    _comps(wb, pack, R, H, C)
    if str(pack["assumptions"]["sources"].get("exit_ev_ebitda", "")).startswith("peer median"):
        wb["Inputs"][R["exit_mult"].split("!")[1].replace("$", "")].value = f"={C['median']['EV/EBITDA']}"
    _sensitivity(wb, pack, R, X, S)
    _reverse(wb, pack, R, D, H, X, RV)
    _summary(wb, pack, R, X, C, S, OUT)
    wb.move_sheet("Summary", offset=len(wb.sheetnames) - 1)  # tab order: Inputs ... Summary
    wb.active = wb.sheetnames.index("Summary")
    refs = {"inputs": R, "summary": OUT, "dcf": {"price_gordon": X["price_gordon"], "price_exit": X["price_exit"]},
            "sensitivity": S, "comps": C, "reverse_dcf": RV}
    return wb, refs


def write_model(pack: dict, path: Path) -> Path:
    wb, _ = build_workbook(pack)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def expected_outputs(pack: dict) -> dict:
    """Values the Summary tab should show, per Phase 1 math (used by tests)."""
    s = scenarios(pack)
    return {"gordon": pack["dcf"]["gordon"]["implied_price"], "exit": pack["dcf"]["exit_multiple"]["implied_price"],
            "pt": s["price_target"], "bull": s["bull"], "bear": s["bear"], "rating": s["rating"],
            "wacc": pack["wacc"]["wacc"]}
