"""XBRL companyfacts -> standard annual IS / BS / CF line items (last N fiscal years).

Every line item is a list aligned with `periods` (oldest -> newest). Missing values are None,
never 0. Every missing or derived value gets an entry in `data_quality`.
"""

from __future__ import annotations

from pitch.data.edgar import annual_values

# Ordered fallback lists: first tag that reports a given period wins.
TAGS: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueGoodsNet",
    ],
    "gross_profit": ["GrossProfit"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
    "ebit": ["OperatingIncomeLoss"],
    "da": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
    ],
    "depreciation": ["Depreciation"],
    "amortization": ["AmortizationOfIntangibleAssets"],
    "interest": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"],
    "pretax_income": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ],
    "tax": ["IncomeTaxExpenseBenefit"],
    "net_income": ["NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic", "ProfitLoss"],
    "cfo": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "sbc": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "short_term_investments": [
        "MarketableSecuritiesCurrent",
        "ShortTermInvestments",
        "OtherShortTermInvestments",
        "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
        "DebtSecuritiesCurrent",
    ],
    # Fallbacks used only via derivation (flagged): may include noncurrent / interest income.
    "afs_debt_total": ["AvailableForSaleSecuritiesDebtSecurities"],
    "interest_net_nonop": ["InterestIncomeExpenseNonoperatingNet"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "shares_diluted": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    # Debt components (combined into total_debt below)
    "debt_total_lt": [
        "LongTermDebt",
        "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
        "DebtLongtermAndShorttermCombinedAmount",
    ],
    "debt_lt_noncurrent": ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"],
    "debt_lt_current": ["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent", "DebtCurrent"],
    "debt_st_total": ["ShortTermBorrowings"],
    "debt_commercial_paper": ["CommercialPaper"],
    "debt_st_other": ["OtherShortTermBorrowings"],
}

UNITS = {"shares_diluted": "shares"}

# Items exposed in the output (in display order). Components above are internal.
LINE_ITEMS = [
    "revenue", "gross_profit", "ebit", "da", "interest", "pretax_income", "tax", "net_income",
    "cfo", "capex", "sbc", "cash", "short_term_investments", "total_debt", "shares_diluted",
    "current_assets", "current_liabilities", "current_debt",
]
# Missing values in these are expected for many filers; flagged as "info" rather than "missing".
OPTIONAL_ITEMS = {"short_term_investments", "current_debt"}


class NormalizeError(ValueError):
    pass


def _lookup(facts: dict, item: str, periods: list[str]) -> tuple[list, list]:
    """Values + source tags for one item, aligned to `periods`, using ordered tag fallback."""
    unit = UNITS.get(item, "USD")
    by_tag = [(t, annual_values(facts, t, unit)) for t in TAGS[item]]
    vals, srcs = [], []
    for p in periods:
        hit = next(((t, rows[p]["val"]) for t, rows in by_tag if p in rows), None)
        vals.append(hit[1] if hit else None)
        srcs.append(hit[0] if hit else None)
    return vals, srcs


def _add(*xs):
    return None if any(x is None for x in xs) else sum(xs)


def normalize(facts: dict, years: int = 5) -> dict:
    """Standardize companyfacts into `years` annual periods. Pure: no I/O."""
    rev_rows: dict[str, dict] = {}
    for tag in TAGS["revenue"]:
        for end, row in annual_values(facts, tag).items():
            rev_rows.setdefault(end, row)
    if not rev_rows:
        raise NormalizeError("No annual 10-K revenue found under any known revenue tag")
    periods = sorted(rev_rows)[-years:]

    raw = {item: _lookup(facts, item, periods) for item in TAGS}
    items: dict[str, list] = {}
    sources: dict[str, list] = {}
    dq: list[dict] = []

    def flag(item, i, issue, detail):
        dq.append({"item": item, "period": periods[i], "issue": issue, "detail": detail})

    for item in LINE_ITEMS:
        if item in raw:
            items[item], sources[item] = list(raw[item][0]), list(raw[item][1])
        else:
            items[item], sources[item] = [None] * len(periods), [None] * len(periods)

    _split_adjust_shares(items["shares_diluted"], sources["shares_diluted"], flag)

    for i in range(len(periods)):
        # Net income: ProfitLoss includes noncontrolling interests
        if sources["net_income"][i] == "ProfitLoss":
            flag("net_income", i, "derived", "ProfitLoss (includes noncontrolling interests); no NetIncomeLoss tag")

        # Interest expense: fall back to net nonoperating interest when it is a net expense
        if items["interest"][i] is None:
            net = raw["interest_net_nonop"][0][i]
            if net is not None and net < 0:
                items["interest"][i], sources["interest"][i] = -net, "derived: -InterestIncomeExpenseNonoperatingNet"
                flag("interest", i, "derived", "net nonoperating interest (expense net of interest income)")

        # ST investments: fall back to total AFS debt securities (may include noncurrent)
        if items["short_term_investments"][i] is None and raw["afs_debt_total"][0][i] is not None:
            items["short_term_investments"][i] = raw["afs_debt_total"][0][i]
            sources["short_term_investments"][i] = "derived: AvailableForSaleSecuritiesDebtSecurities"
            flag("short_term_investments", i, "derived",
                 "total AFS debt securities (no current-only tag; may include noncurrent)")

        # Gross profit: derive from revenue - cost of revenue
        if items["gross_profit"][i] is None:
            cogs, ctag = raw["cost_of_revenue"][0][i], raw["cost_of_revenue"][1][i]
            v = _add(items["revenue"][i], -cogs if cogs is not None else None)
            if v is not None:
                items["gross_profit"][i], sources["gross_profit"][i] = v, f"derived: revenue - {ctag}"
                flag("gross_profit", i, "derived", f"revenue - {ctag}")

        # EBIT: derive from pre-tax income + interest expense
        if items["ebit"][i] is None:
            v = _add(items["pretax_income"][i], items["interest"][i])
            if v is not None:
                items["ebit"][i], sources["ebit"][i] = v, "derived: pretax_income + interest"
                flag("ebit", i, "derived", "pre-tax income + interest expense (includes other income)")

        # D&A: derive from depreciation (+ amortization of intangibles if reported)
        if items["da"][i] is None and raw["depreciation"][0][i] is not None:
            dep, amort = raw["depreciation"][0][i], raw["amortization"][0][i]
            if amort is not None:
                items["da"][i], sources["da"][i] = dep + amort, "derived: Depreciation + AmortizationOfIntangibleAssets"
                flag("da", i, "derived", "Depreciation + AmortizationOfIntangibleAssets")
            else:
                items["da"][i], sources["da"][i] = dep, "derived: Depreciation only"
                flag("da", i, "derived", "Depreciation only; intangible amortization not reported")

        # Cash: flag if restricted cash fallback used
        if sources["cash"][i] == "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents":
            flag("cash", i, "derived", "includes restricted cash (no unrestricted cash tag)")

        # Short-term borrowings (ShortTermBorrowings already includes commercial paper when reported)
        if raw["debt_st_total"][0][i] is not None:
            st, st_src = raw["debt_st_total"][0][i], "ShortTermBorrowings"
        else:
            parts = [(k, raw[k][0][i]) for k in ("debt_commercial_paper", "debt_st_other") if raw[k][0][i] is not None]
            st = sum(v for _, v in parts) if parts else None
            st_src = " + ".join(raw[k][1][i] for k, _ in parts) if parts else None
        cur_lt = raw["debt_lt_current"][0][i]
        if cur_lt is not None or st is not None:
            items["current_debt"][i] = (cur_lt or 0) + (st or 0)
            sources["current_debt"][i] = " + ".join(x for x in (raw["debt_lt_current"][1][i], st_src) if x)

        # Total debt = long-term debt incl. current portion + short-term borrowings
        lt_total, lt_src = raw["debt_total_lt"][0][i], raw["debt_total_lt"][1][i]
        if lt_total is None:
            nc, cur = raw["debt_lt_noncurrent"][0][i], raw["debt_lt_current"][0][i]
            if nc is not None:
                lt_total = nc + (cur or 0)
                lt_src = f"{raw['debt_lt_noncurrent'][1][i]} + {raw['debt_lt_current'][1][i] or '(no current portion tag)'}"
                if cur is None:
                    flag("total_debt", i, "derived", "current portion of LT debt not reported; noncurrent only")
        if lt_total is None:
            flag("total_debt", i, "missing", "no long-term debt tag reported")
            continue
        if st is None:
            flag("total_debt", i, "info", "no short-term borrowings tag; total debt = long-term debt only")
        items["total_debt"][i] = lt_total + (st or 0)
        sources["total_debt"][i] = lt_src + (f" + {st_src}" if st_src else "")

    for item in LINE_ITEMS:
        for i, v in enumerate(items[item]):
            if v is None and not any(d["item"] == item and d["period"] == periods[i] for d in dq):
                issue = "info" if item in OPTIONAL_ITEMS else "missing"
                flag(item, i, issue, f"no value under tags {TAGS.get(item, [])}")

    return {
        "entity_name": facts.get("entityName"),
        "cik": facts.get("cik"),
        "periods": periods,
        "fiscal_years": [f"FY{p[:4]}" for p in periods],
        "items": items,
        "sources": sources,
        "data_quality": dq,
    }


SPLIT_RATIOS = (2, 3, 4, 5, 8, 10, 15, 20, 25, 30, 40, 50)


def _split_adjust_shares(vals: list, srcs: list, flag) -> None:
    """Restate share counts for stock splits in place.

    Comparatives last filed before a split are never restated in companyfacts (e.g. NVDA FY2022 shares
    are pre-10:1-split). Walking newest -> oldest, a jump between consecutive years within 10% of a
    common split ratio is treated as a split and older values are scaled. Reverse splits likewise.
    """
    factor, newer = 1.0, None  # newer = nearest newer (already adjusted) value
    for i in range(len(vals) - 1, -1, -1):
        cur = vals[i]
        if cur is None or cur <= 0:
            continue
        if newer is not None:
            jump = newer / (cur * factor)
            factor *= next((q for r in SPLIT_RATIOS for q in (r, 1 / r) if abs(jump / q - 1) <= 0.10), 1.0)
        if factor != 1.0:
            vals[i] = round(cur * factor)
            srcs[i] = f"{srcs[i]} (split-adjusted x{factor:g})"
            flag("shares_diluted", i, "derived", f"split-adjusted x{factor:g} (pre-split comparative)")
        newer = vals[i]


def latest(hist: dict, item: str):
    """Most recent value of a line item (None if missing)."""
    vals = hist["items"].get(item) or []
    return vals[-1] if vals else None


def operating_nwc(hist: dict) -> list:
    """Operating NWC per period: (current assets - cash - ST investments) - (current liabilities - current debt).

    None if current assets, cash or current liabilities are missing. Missing ST investments / current debt
    mean "none reported" (flagged as info in data_quality) and are excluded rather than guessed.
    """
    it = hist["items"]
    out = []
    for ca, cash, sti, cl, cd in zip(
        it["current_assets"], it["cash"], it["short_term_investments"], it["current_liabilities"], it["current_debt"]
    ):
        out.append(None if None in (ca, cash, cl) else (ca - cash - (sti or 0)) - (cl - (cd or 0)))
    return out
