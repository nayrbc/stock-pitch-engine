"""Excel model reproduces Phase 1 numbers via live formulas (evaluated with the `formulas` engine)."""

import re
import warnings

import pytest

from pitch.excel import build_workbook, expected_outputs
from pitch.memo import fields, render, scenarios
from pitch.memo import TEMPLATE
from pitch.pack import build_pack
from tests.test_comps_pack import hist, mkt

warnings.filterwarnings("ignore", category=SyntaxWarning)
formulas = pytest.importorskip("formulas")


@pytest.fixture(scope="module")
def pack():
    from pitch.dcf import load_config
    peers = {"P1": (hist(2), mkt(30, 600, name="Peer One")), "P2": (hist(3), mkt(40, 900, name="Peer Two"))}
    return build_pack("TEST", hist(), mkt(20, 200, name="TestCo"), peers, load_config())


@pytest.fixture(scope="module")
def model(pack, tmp_path_factory):
    wb, refs = build_workbook(pack)
    path = tmp_path_factory.mktemp("xl") / "model.xlsx"
    wb.save(path)
    xl = formulas.ExcelModel().loads(str(path)).finish()
    return xl, refs, path


def _key(path, ref):
    sheet, cell = ref.split("!")
    sheet = sheet.strip("'")
    return f"'[{path.name}]{sheet.upper()}'!{cell.replace('$', '')}"


def _get(sol, path, ref):
    v = sol[_key(path, ref)].value[0][0]
    return v if isinstance(v, str) else float(v)


def test_summary_matches_pitch_pack(model, pack):
    xl, refs, path = model
    sol = xl.calculate()
    exp = expected_outputs(pack)
    out = refs["summary"]
    for key in ("gordon", "exit", "pt", "bull", "bear"):
        assert _get(sol, path, out[key]) == pytest.approx(exp[key], abs=0.01), key
    assert _get(sol, path, out["wacc"]) == pytest.approx(exp["wacc"], abs=1e-9)
    assert _get(sol, path, out["rating"]) == exp["rating"]


def test_sensitivity_grids_match_pitch_pack(model, pack):
    xl, refs, path = model
    sol = xl.calculate()
    for key, pk in (("gordon", "wacc_x_terminal_growth"), ("exit", "wacc_x_exit_multiple")):
        for row_refs, row_vals in zip(refs["sensitivity"][key]["cells"], pack["sensitivity"][pk]["implied_price"]):
            for ref, v in zip(row_refs, row_vals):
                assert _get(sol, path, ref) == pytest.approx(v, abs=0.01)


def test_changing_wacc_in_inputs_moves_summary_price(model):
    xl, refs, path = model
    base = xl.calculate()
    override = _key(path, refs["inputs"]["wacc_override"])
    out = refs["summary"]
    p0_g, p0_e = _get(base, path, out["gordon"]), _get(base, path, out["exit"])
    wacc0 = _get(base, path, out["wacc"])
    sol = xl.calculate(inputs={override: wacc0 + 0.01})
    assert _get(sol, path, out["wacc"]) == pytest.approx(wacc0 + 0.01)
    assert _get(sol, path, out["gordon"]) < p0_g - 0.01
    assert _get(sol, path, out["exit"]) < p0_e - 0.01


def test_no_hardcoded_numbers_in_formulas(model):
    _, _, path = model
    import openpyxl
    wb = openpyxl.load_workbook(path)
    allowed = {"0", "1", "0.5"}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("="):
                    body = re.sub(r"'[^']*'|\"[^\"]*\"|\$?[A-Z]{1,3}\$?\d+|[A-Za-z_]+!", "", c.value)
                    nums = set(re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", body))
                    assert nums <= allowed, f"{ws.title}!{c.coordinate}: {c.value}"
                    assert c.font.color.rgb.endswith("000000"), f"formula not black at {ws.title}!{c.coordinate}"
                elif isinstance(c.value, (int, float)) and not isinstance(c.value, bool):
                    assert c.font.color.rgb.endswith("0000FF"), f"input not blue at {ws.title}!{c.coordinate}"


def test_tabs_and_chart(model):
    _, _, path = model
    import openpyxl
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ["Inputs", "Historicals", "DCF", "Comps", "Sensitivity", "Reverse DCF",
                             "Summary"]
    assert len(wb["Summary"]._charts) == 1


def test_memo_renders_all_placeholders(pack):
    text = render(pack, TEMPLATE.read_text())
    assert "{{" not in text
    s = scenarios(pack)
    assert f"${s['price_target']:,.2f}" in text and s["rating"] in text
    assert set(re.findall(r"\{\{(\w+)\}\}", TEMPLATE.read_text())) <= set(fields(pack))


def test_scenarios_ordering_and_rating(pack):
    s = scenarios(pack)
    assert s["bear"] < s["base"] < s["bull"]
    assert s["price_target"] == pytest.approx(
        (pack["dcf"]["gordon"]["implied_price"] + pack["dcf"]["exit_multiple"]["implied_price"]) / 2)
    assert s["rating"] in {"BUY", "HOLD", "SELL"}


def test_reverse_dcf_tab_reprices_to_current_price(model, pack):
    xl, refs, path = model
    sol = xl.calculate()
    price = pack["valuation_summary"]["current_price"]
    for key in ("revenue_growth", "ebit_margin"):
        r = refs["reverse_dcf"][key]
        assert _get(sol, path, r["driver"]) == pytest.approx(pack["reverse_dcf"][key]["price_target"])
        assert _get(sol, path, r["price_target"]) == pytest.approx(price, abs=0.01), key
        assert _get(sol, path, r["gap"]) == pytest.approx(0, abs=0.01), key


def test_reverse_dcf_tab_driver_is_live(model, pack):
    xl, refs, path = model
    r = refs["reverse_dcf"]["revenue_growth"]
    sol = xl.calculate(inputs={_key(path, r["driver"]): pack["reverse_dcf"]["revenue_growth"]["price_target"] + 0.02})
    assert _get(sol, path, r["gap"]) > 0.01


def test_memo_shows_reverse_dcf(pack):
    text = render(pack, TEMPLATE.read_text())
    assert "Reverse DCF" in text
    assert f"{pack['reverse_dcf']['revenue_growth']['price_target']:.1%}" in text
    assert f"{pack['reverse_dcf']['ebit_margin']['price_target']:.1%}" in text
