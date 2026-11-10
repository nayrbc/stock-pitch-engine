# {{name}} ({{ticker}}): {{rating}}, price target {{price_target}}

**Current price:** {{current_price}} · **Upside to PT:** {{upside}} · **Date:** {{date}}
**Peers:** {{peers}} · **WACC:** {{wacc}} · **Terminal growth:** {{terminal_growth}} · **Exit multiple:** {{exit_multiple}}

> Numbers are generated from `pitch_pack.json` and `model.xlsx`. Write the prose in each `[PROSE: ...]` slot and don't change the numbers.

## Rating + price target
**{{rating}}** with a price target of **{{price_target}}** ({{upside}} vs {{current_price}}).
The price target is the average of the two DCF prices: perpetual growth {{dcf_gordon}} and exit multiple {{dcf_exit}}.
[PROSE: one sentence on why this rating]

## Thesis
1. [PROSE: thesis point 1, supported by a number below]
2. [PROSE: thesis point 2]
3. [PROSE: thesis point 3]

## Variant view: what the market is missing
[PROSE: compare the market-implied view with ours. At {{current_price}} the stock trades at {{target_ev_ebitda}} EV/EBITDA vs a peer median of {{peer_ev_ebitda}}.]

## Bull / Base / Bear
| Case | Price | vs current | What you have to believe |
|---|---|---|---|
| Bull | {{bull}} | {{bull_upside}} | WACC {{bull_wacc}}, terminal growth {{bull_g}}, exit {{bull_mult}}. [PROSE] |
| Base | {{base}} | {{base_upside}} | WACC {{wacc}}, terminal growth {{terminal_growth}}, exit {{exit_multiple}}. [PROSE] |
| Bear | {{bear}} | {{bear_upside}} | WACC {{bear_wacc}}, terminal growth {{bear_g}}, exit {{bear_mult}}. [PROSE] |

## Catalysts
| Date | Catalyst | Why it matters |
|---|---|---|
| [PROSE: date] | [PROSE: e.g. next earnings] | [PROSE] |
| [PROSE: date] | [PROSE] | [PROSE] |

## Key risks + what would change our mind
- [PROSE: risk 1, and the metric that would change our mind]
- [PROSE: risk 2]
- [PROSE: risk 3]

## Valuation summary
| Method | Low | High | Point |
|---|---|---|---|
| DCF: perpetual growth (WACC × g) | {{gordon_low}} | {{gordon_high}} | {{dcf_gordon}} |
| DCF: exit multiple (WACC × EV/EBITDA) | {{exit_low}} | {{exit_high}} | {{dcf_exit}} |
| Comps (peer medians) | {{comps_low}} | {{comps_high}} | n/a |
| 52-week range | {{week52_low}} | {{week52_high}} | {{current_price}} |

**Historicals ({{last_fy}}):** revenue {{revenue}} ({{revenue_growth}} YoY), EBIT margin {{ebit_margin}}, net income {{net_income}}.
**Projection drivers:** revenue growth {{growth_path}}, EBIT margin {{margin_path}}, tax rate {{tax_rate}}.
**Cost of capital:** cost of equity {{cost_of_equity}} (rf {{rf}}, beta {{beta}}, ERP {{erp}}), pre-tax cost of debt {{cost_of_debt}}, E/D weights {{weights}}.
**Terminal value share of EV:** {{tv_pct_gordon}} (perpetual growth) / {{tv_pct_exit}} (exit multiple).

**Data quality flags ({{dq_count}}):**
{{data_quality}}
