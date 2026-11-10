# Stock Pitch Engine — Spec

## Goal
Type `pitch NVDA --peers AMD,AVGO,QCOM` and get, in under a minute:
1. An Excel valuation model (live formulas, not pasted values): historicals, DCF, comps, sensitivity tables.
2. A `pitch_pack.json` with every number and assumption the memo needs.
3. A one-page memo draft (thesis, bull/bear, catalysts, risks, valuation, rating).

Built for club stock pitches and competitions. It should look like analyst work, not a toy.

## Stack
- Python 3.11+, `uv` for env management
- Data: SEC EDGAR (free, no key) + `yfinance` (prices, market cap, beta)
- Excel: `openpyxl` (formulas written as formulas)
- CLI: `typer`
- Tests: `pytest`
- Optional later: Streamlit front end

## Data sources
- Ticker → CIK: `https://www.sec.gov/files/company_tickers.json`
- Financials: `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json` (10-digit, zero-padded CIK)
- EDGAR requires a `User-Agent` header with a name + email. Read it from `.env`, never hardcode.
- Cache every response to `cache/` (JSON, keyed by ticker + date) so re-runs cost nothing.
- Market data from `yfinance`: price, shares outstanding, market cap, beta, net debt cross-check.

## Repo layout
```
pitch/
  data/edgar.py        # fetch + cache companyfacts
  data/market.py       # yfinance wrapper
  normalize.py         # XBRL tags -> standard 5-yr IS / BS / CF
  dcf.py               # FCFF DCF, WACC, terminal value, sensitivities
  comps.py             # peer multiples table
  pack.py              # assemble pitch_pack.json
  excel.py             # write model.xlsx with formulas
  memo.py              # fill memo template from pitch_pack
  cli.py
templates/memo.md
tests/
outputs/<TICKER>/      # model.xlsx, pitch_pack.json, memo.md
```

## Core logic (the hard part)
### normalize.py
- Map XBRL tags to standard line items. Companies use different tags for the same thing, e.g. revenue may be `Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax`, or `SalesRevenueNet`. Use an ordered fallback list per line item.
- Annual (10-K, FY) figures only; last 5 fiscal years.
- Line items: revenue, gross profit, EBIT, D&A, interest, tax, net income, CFO, capex, SBC, cash, total debt, shares diluted.
- Flag any missing or derived item in a `data_quality` list. Never silently fill with 0.

### dcf.py
- Unlevered FCF = EBIT·(1−t) + D&A − capex − ΔNWC
- 5-year projection driven by assumptions (revenue growth path, EBIT margin path, capex % rev, D&A % rev, NWC % rev, tax rate), defaulting to historical averages with sane clamps.
- WACC: CAPM cost of equity (rf, beta, ERP from config), after-tax cost of debt, market-value weights.
- Terminal value both ways, Gordon growth and exit EV/EBITDA multiple. Report both.
- Mid-year convention toggle.
- EV → equity bridge: −net debt, ÷ diluted shares → implied price, upside %.
- Sensitivity grids: WACC × terminal growth, WACC × exit multiple.

### comps.py
- For each peer: EV/Revenue, EV/EBITDA, P/E (LTM), revenue growth, EBIT margin.
- Median and mean; implied price for the target at peer median.

### Assumptions
- All assumptions live in `config/assumptions.yaml` and can be overridden per ticker (`config/NVDA.yaml`).
- Every assumption shows up in the Excel inputs tab, so changing a cell reflows the whole model.

## Excel model (model.xlsx)
Tabs: `Inputs` · `Historicals` · `DCF` · `Comps` · `Sensitivity` · `Summary`
- Blue font = hard-coded inputs, black = formulas (banker convention).
- No hard-coded numbers inside formulas.
- The Summary tab has a football field (DCF range, comps range, 52-week range) as a chart.

## Memo (memo.md)
Sections: Rating + price target · Thesis in 3 bullets · Variant view (what the market is missing) · Bull / Base / Bear with prices · Catalysts with dates · Key risks + what would change our mind · Valuation summary.
The code fills in the numbers. The prose is written afterwards by pasting `pitch_pack.json` into a cheap model (Sonnet), so no API cost is built into the tool.

## Acceptance criteria
- `pitch AAPL --peers MSFT,GOOGL` runs end to end with no manual edits.
- Historical revenue and net income match the 10-K within 0.5%.
- Changing WACC in `Inputs` updates the implied price in `Summary`.
- Works for at least 3 companies with different tagging (e.g. AAPL, KO, NVDA).
- `pytest` passes, including a hand-checked DCF test case.

Banks and insurers are out of scope; FCF DCF doesn't apply. Detect them by SIC code and exit with a clear message.

## Out of scope (v1)
Quarterly data, non-US filers (20-F), banks/insurers, live news or sentiment, any paid data.

---

## Build plan: which model does what

| Phase | Model | Work |
|---|---|---|
| 0 | Sonnet | Scaffold repo, `edgar.py`, `market.py`, caching, CLI skeleton |
| 1 | **Fable session 1** | `normalize.py`, `dcf.py`, `comps.py`, `pack.py` + tests |
| 2 | Sonnet | `excel.py` (formulas, formatting, chart), memo template fill |
| 3 | Sonnet | Run on 3 tickers, fix bugs |
| 4 | **Fable session 2** (optional) | Review one full pitch the way an analyst would: flawed assumptions, weak thesis logic |

Stop and ask for help before opening a third Fable session.

## Fable session 1: kickoff message (paste as the first message in a fresh session)

> Read SPEC.md. Implement Phase 1 only: `pitch/normalize.py`, `pitch/dcf.py`, `pitch/comps.py`, `pitch/pack.py`, and tests in `tests/`. Phase 0 is done: use the existing `pitch/data/` fetchers and don't modify them.
> Constraints: pure functions where possible; assumptions come from `config/assumptions.yaml`; flag missing data, never fill with 0.
> Done = `pytest` passes, including a DCF test with hand-checkable numbers, and `python -m pitch.pack AAPL --peers MSFT,GOOGL` writes a valid `outputs/AAPL/pitch_pack.json`.
> Output only code and a ≤5-line summary. Don't explain unless something in the spec is ambiguous. If it is, ask before guessing.
