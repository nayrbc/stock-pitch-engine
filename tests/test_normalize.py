from pitch.normalize import LINE_ITEMS, normalize, operating_nwc
from tests.conftest import PERIODS, base_series, make_facts

SHARES = {"WeightedAverageNumberOfDilutedSharesOutstanding": "shares"}


def test_basic_shape(facts):
    h = normalize(facts)
    assert h["periods"] == PERIODS
    assert h["fiscal_years"] == ["FY2021", "FY2022", "FY2023", "FY2024", "FY2025"]
    assert set(LINE_ITEMS) <= set(h["items"])
    assert all(len(v) == 5 for v in h["items"].values())
    assert h["items"]["revenue"][-1] == 146.41
    assert h["items"]["shares_diluted"][-1] == 10


def test_last_n_years_only():
    s = base_series()
    h = normalize(make_facts(s, SHARES), years=3)
    assert h["periods"] == PERIODS[-3:]


def test_tag_fallback_per_period():
    s = base_series()
    rev = s.pop("Revenues")[0]
    s["SalesRevenueNet"] = (rev[:2] + [None] * 3, True)
    s["RevenueFromContractWithCustomerExcludingAssessedTax"] = ([None] * 2 + rev[2:], True)
    h = normalize(make_facts(s, SHARES))
    assert h["items"]["revenue"] == rev
    assert h["sources"]["revenue"][0] == "SalesRevenueNet"
    assert h["sources"]["revenue"][-1] == "RevenueFromContractWithCustomerExcludingAssessedTax"


def test_missing_is_none_and_flagged_never_zero():
    s = base_series()
    s["ShareBasedCompensation"] = ([1, 1, None, 1, 1], True)
    h = normalize(make_facts(s, SHARES))
    assert h["items"]["sbc"][2] is None
    assert {"item": "sbc", "period": PERIODS[2], "issue": "missing"}.items() <= next(
        d for d in h["data_quality"] if d["item"] == "sbc").items()


def test_derived_gross_profit_and_da_flagged():
    s = base_series()
    s.pop("GrossProfit")
    s["CostOfRevenue"] = ([60, 66, 72.6, 79.86, 87.846], True)
    s.pop("DepreciationDepletionAndAmortization")
    s["Depreciation"] = ([4] * 5, True)
    s["AmortizationOfIntangibleAssets"] = ([1] * 5, True)
    h = normalize(make_facts(s, SHARES))
    assert h["items"]["gross_profit"][0] == 40
    assert h["items"]["da"] == [5] * 5
    issues = {(d["item"], d["issue"]) for d in h["data_quality"]}
    assert ("gross_profit", "derived") in issues and ("da", "derived") in issues


def test_total_debt_composite():
    s = base_series()
    s.pop("LongTermDebt")
    s["LongTermDebtNoncurrent"] = ([18] * 5, False)
    s["LongTermDebtCurrent"] = ([2] * 5, False)
    s["CommercialPaper"] = ([3] * 5, False)
    h = normalize(make_facts(s, SHARES))
    assert h["items"]["total_debt"] == [23] * 5
    assert h["items"]["current_debt"] == [5] * 5


def test_short_term_borrowings_not_double_counted_with_cp():
    s = base_series()
    s["ShortTermBorrowings"] = ([4] * 5, False)
    s["CommercialPaper"] = ([3] * 5, False)
    h = normalize(make_facts(s, SHARES))
    assert h["items"]["total_debt"] == [24] * 5


def test_missing_debt_flagged():
    s = base_series()
    s.pop("LongTermDebt")
    h = normalize(make_facts(s, SHARES))
    assert h["items"]["total_debt"] == [None] * 5
    assert any(d["item"] == "total_debt" and d["issue"] == "missing" for d in h["data_quality"])


def test_operating_nwc(facts):
    # (40 - 10 cash - 0 STI) - (30 - 0 current debt) = 0
    assert operating_nwc(normalize(facts)) == [0] * 5


def test_later_filing_wins():
    f = make_facts(base_series(), SHARES)
    rows = f["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
    rows.append({**rows[-1], "val": 999, "filed": "2027-02-01"})
    assert normalize(f)["items"]["revenue"][-1] == 999


def test_non_10k_ignored():
    f = make_facts(base_series(), SHARES)
    rows = f["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
    rows.append({**rows[-1], "val": 999, "form": "10-Q", "filed": "2027-02-01"})
    assert normalize(f)["items"]["revenue"][-1] == 146.41


# --- Phase 3 regressions (real-filer tagging found running KO / NVDA / peers) ---

def _issues(h, item):
    return [d for d in h["data_quality"] if d["item"] == item]


def test_pre_split_share_comparative_is_restated():
    """NVDA: FY2022 diluted shares last filed pre-10:1 split (2,535M vs 25,070M)."""
    s = base_series()
    s["WeightedAverageNumberOfDilutedSharesOutstanding"] = ([2535, 25070, 24940, 24804, 24514], True)
    h = normalize(make_facts(s, SHARES))
    assert h["items"]["shares_diluted"] == [25350, 25070, 24940, 24804, 24514]
    assert [d["period"] for d in _issues(h, "shares_diluted")] == [PERIODS[0]]


def test_ordinary_share_changes_not_treated_as_splits():
    s = base_series()
    s["WeightedAverageNumberOfDilutedSharesOutstanding"] = ([100, 95, 120, 118, 110], True)
    h = normalize(make_facts(s, SHARES))
    assert h["items"]["shares_diluted"] == [100, 95, 120, 118, 110]
    assert not _issues(h, "shares_diluted")


def test_short_term_investments_other_tag():
    """KO: ST investments tagged OtherShortTermInvestments."""
    s = base_series()
    s["OtherShortTermInvestments"] = ([3] * 5, False)
    h = normalize(make_facts(s, SHARES))
    assert h["items"]["short_term_investments"] == [3] * 5


def test_short_term_investments_afs_total_fallback_flagged():
    """NVDA FY2026: only total AFS debt securities tagged in the 10-K; must not land in NWC."""
    s = base_series()
    s["MarketableSecuritiesCurrent"] = ([5, 5, 5, 5, None], False)
    s["AvailableForSaleSecuritiesDebtSecurities"] = ([None] * 4 + [8], False)
    h = normalize(make_facts(s, SHARES))
    assert h["items"]["short_term_investments"] == [5, 5, 5, 5, 8]
    assert [d["issue"] for d in _issues(h, "short_term_investments")] == ["derived"]
    assert operating_nwc(h)[-1] == (40 - 10 - 8) - 30


def test_net_income_profitloss_fallback_flagged():
    """AVGO: net income only under ProfitLoss."""
    s = base_series()
    ni = s.pop("NetIncomeLoss")[0]
    s["ProfitLoss"] = (ni, True)
    h = normalize(make_facts(s, SHARES))
    assert h["items"]["net_income"] == ni
    assert {d["issue"] for d in _issues(h, "net_income")} == {"derived"}


def test_interest_from_net_nonoperating_flagged():
    """KDP: only InterestIncomeExpenseNonoperatingNet (negative = net expense)."""
    s = base_series()
    s.pop("InterestExpense")
    s["InterestIncomeExpenseNonoperatingNet"] = ([-2, -2, 1, -2, -2], True)
    h = normalize(make_facts(s, SHARES))
    assert h["items"]["interest"] == [2, 2, None, 2, 2]
    assert {(d["period"], d["issue"]) for d in _issues(h, "interest")} == (
        {(p, "derived") for p in PERIODS[:2] + PERIODS[3:]} | {(PERIODS[2], "missing")})
