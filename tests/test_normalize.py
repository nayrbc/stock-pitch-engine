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
