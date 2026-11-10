import pytest

from pitch.dcf import load_config

PERIODS = ["2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"]


def _fact(end, val, filed="2026-02-01", start=True):
    row = {"end": end, "val": val, "form": "10-K", "filed": filed, "fy": int(end[:4]), "fp": "FY"}
    if start:
        row["start"] = f"{int(end[:4])}-01-01"
    return row


def make_facts(series: dict[str, tuple[list, bool]], units: dict[str, str] | None = None) -> dict:
    """series: {tag: (values aligned to PERIODS (None = not reported), is_duration)}."""
    units = units or {}
    gaap = {}
    for tag, (vals, duration) in series.items():
        rows = [_fact(p, v, start=duration) for p, v in zip(PERIODS, vals) if v is not None]
        gaap[tag] = {"units": {units.get(tag, "USD"): rows}}
    return {"entityName": "TestCo", "cik": 1, "facts": {"us-gaap": gaap}}


def base_series(scale: float = 1.0) -> dict:
    s = scale
    return {
        "Revenues": ([100 * s, 110 * s, 121 * s, 133.1 * s, 146.41 * s], True),
        "GrossProfit": ([40 * s, 44 * s, 48.4 * s, 53.24 * s, 58.564 * s], True),
        "OperatingIncomeLoss": ([20 * s, 22 * s, 24.2 * s, 26.62 * s, 29.282 * s], True),
        "DepreciationDepletionAndAmortization": ([5 * s, 5.5 * s, 6.05 * s, 6.655 * s, 7.3205 * s], True),
        "InterestExpense": ([1 * s] * 5, True),
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest":
            ([19 * s, 21 * s, 23.2 * s, 25.62 * s, 28.282 * s], True),
        "IncomeTaxExpenseBenefit": ([4.75 * s, 5.25 * s, 5.8 * s, 6.405 * s, 7.0705 * s], True),
        "NetIncomeLoss": ([14.25 * s, 15.75 * s, 17.4 * s, 19.215 * s, 21.2115 * s], True),
        "NetCashProvidedByUsedInOperatingActivities": ([18 * s] * 5, True),
        "PaymentsToAcquirePropertyPlantAndEquipment": ([5 * s, 5.5 * s, 6.05 * s, 6.655 * s, 7.3205 * s], True),
        "ShareBasedCompensation": ([1 * s] * 5, True),
        "CashAndCashEquivalentsAtCarryingValue": ([10 * s] * 5, False),
        "LongTermDebt": ([20 * s] * 5, False),
        "AssetsCurrent": ([40 * s] * 5, False),
        "LiabilitiesCurrent": ([30 * s] * 5, False),
        "WeightedAverageNumberOfDilutedSharesOutstanding": ([10 * s] * 5, True),
    }


@pytest.fixture
def facts():
    return make_facts(base_series(), units={"WeightedAverageNumberOfDilutedSharesOutstanding": "shares"})


@pytest.fixture
def cfg():
    return load_config()
