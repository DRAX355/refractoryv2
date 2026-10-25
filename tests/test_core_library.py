import pytest

from refractory_core import FormulaError, ImmutablePropertyError, RefractoryCore

TRAP = {"length": 1000, "thickness": 100, "width_top": 160, "width_bottom": 140}


@pytest.fixture
def core(tmp_path):
    return RefractoryCore(tmp_path)


def test_default_formula_matches_legacy_math(core):
    assert core.formulas.evaluate("vol_standard_trapezoidal", TRAP) == pytest.approx(15.0)


def test_edit_changes_dependents_and_is_audited(core):
    v = {"qty": 100, "allow_pct": 5, "unit_vol_dm3": 10, "density": 3.0}
    assert core.formulas.evaluate("total_tonnes", v) == pytest.approx(105 * 30 / 1000)
    assert "total_tonnes" in core.formulas.dependents("order_qty")
    core.formulas.set_expression("order_qty", "ceil(qty * (1 + allow_pct / 100)) + 10", actor="a", reason="spares")
    assert core.formulas.evaluate("total_tonnes", v) == pytest.approx(115 * 30 / 1000)
    last = core.audit.entries(action="formula.update")[-1]
    assert last["before"].startswith("ceil") and last["actor"] == "a" and "total_tonnes" in last["meta"]["affected"]
    core.formulas.reset("order_qty", actor="a", reason="")
    assert core.formulas.evaluate("total_tonnes", v) == pytest.approx(105 * 30 / 1000)


def test_override_survives_reload(tmp_path):
    RefractoryCore(tmp_path).formulas.set_expression("unit_weight", "unit_vol_dm3 * density * 1.01", actor="a", reason="r")
    assert RefractoryCore(tmp_path).formulas.evaluate("unit_weight", {"unit_vol_dm3": 10, "density": 3}) == pytest.approx(30.3)


@pytest.mark.parametrize("bad", ["__import__('os')", "open('x')", "a.b", "unit_weight(1)", "[1]", "nope * 2", "1/0", "total_tonnes"])
def test_bad_formulas_rejected(core, bad):
    with pytest.raises(FormulaError):
        core.formulas.set_expression("unit_weight", bad, actor="a", reason="x")
    assert core.audit.entries()[-1]["outcome"] == "DENIED"


def test_circular_reference_rejected(core):
    with pytest.raises(FormulaError):
        core.formulas.set_expression("order_qty", "total_tonnes + 1", actor="a", reason="x")


def test_reason_required(core):
    with pytest.raises(FormulaError):
        core.formulas.set_expression("unit_weight", "unit_vol_dm3 * density", actor="a", reason=" ")


def test_locked_property_cannot_change_and_denial_is_audited(core):
    with pytest.raises(ImmutablePropertyError):
        core.properties.set("compound.MgO.density", 9.9, actor="mallory", reason="x")
    assert core.properties.value("compound.MgO.density") == 3.58
    e = core.audit.entries()[-1]
    assert e["outcome"] == "DENIED" and e["actor"] == "mallory"
    assert core.properties.set("default.cut_allowance", 4, actor="a", reason="policy")["value"] == 4


def test_grade_chemistry_frozen(core):
    core.properties.register_grade("G1", generic_class="MgO-C", chemistry={"MgO": 80, "C": 12}, bulk_density=3.0, actor="a")
    with pytest.raises(ImmutablePropertyError):
        core.properties.update_grade("G1", actor="a", reason="x", chemistry={"MgO": 99})
    with pytest.raises(ImmutablePropertyError):
        core.properties.register_grade("G1", generic_class="MgO-C", chemistry={}, bulk_density=3, actor="a")
    assert core.properties.update_grade("G1", actor="a", reason="measured", bulk_density=3.05)["bulk_density"] == 3.05


def test_audit_chain_detects_tampering(core):
    core.formulas.set_expression("unit_weight", "unit_vol_dm3 * density", actor="a", reason="r")
    core.audit.record("x", "y")
    assert core.audit.verify()["ok"]
    txt = core.audit.path.read_text().replace('"actor": "a"', '"actor": "b"', 1)
    core.audit.path.write_text(txt)
    r = core.audit.verify()
    assert not r["ok"] and r["broken_at"] == 1


def test_presets_rank_by_context_and_popularity(core):
    p = core.presets
    p.record_use("zone", {"vessel": "BOF 180t", "zone": "barrel"}, {"grade": "GRADE-A", "wear": 0.1}, actor="a")
    for _ in range(3):
        p.record_use("zone", {"vessel": "BOF 180t", "zone": "barrel"}, {"grade": "GRADE-B", "wear": 0.1}, actor="b")
    p.record_use("zone", {"vessel": "ladle", "zone": "slagline"}, {"grade": "GRADE-C", "wear": 0.2}, actor="a")
    s = p.suggest("zone", {"vessel": "BOF 180t", "zone": "barrel"})
    assert s[0]["settings"]["grade"] == "GRADE-B" and s[0]["status"] == "pending"
    assert s[0]["review_fields"] == {} and "wear" in s[0]["usual_fields"]      # the usual set: nothing to review
    assert [x for x in s if x["settings"]["grade"] == "GRADE-A"][0]["review_fields"] == {"grade": "GRADE-A"}
    applied = p.confirm(s[0]["id"], actor="a", edits={"wear": 0.12})
    assert applied == {"grade": "GRADE-B", "wear": 0.12}
    assert core.audit.entries(action="preset.confirm")


def test_bom_uses_edited_formula(tmp_path, monkeypatch):
    import refractory_core
    from app.services.part3_lining.lining.bom import unit_volume
    core = RefractoryCore(tmp_path)
    monkeypatch.setattr(refractory_core, "_default", core)
    brick = {"shape_family": "standard_trapezoidal", "dimensions_mm": {"length": 1000, "width_top": 160, "width_bottom": 140, "thickness": 100}}
    assert unit_volume(brick)[0] == pytest.approx(15.0)
    core.formulas.set_expression("vol_standard_trapezoidal", "length * thickness * width_top / 1e6", actor="a", reason="test")
    assert unit_volume(brick)[0] == pytest.approx(16.0)


def test_all_113_formulas_validate(core):
    flist = core.formulas.list()
    assert len(flist) >= 113
    for f in flist:
        chk = core.formulas.validate(f["id"], f["expression"])
        assert chk["ok"], f"{f['id']} failed: {chk.get('error')}"


def test_bricks_close_ring_uses_edited_formula(tmp_path, monkeypatch):
    import math
    import refractory_core
    from app.services.part3_lining.lining.bricks import close_ring
    core = RefractoryCore(tmp_path)
    monkeypatch.setattr(refractory_core, "_default", core)
    r0 = close_ring(2 * math.pi * 2800, 2 * math.pi * 3700, (125.0, 155.0), (110.0, 175.0), 0.5, 40.0)
    core.formulas.set_expression("ring_n_a", "(inner_len * (b_cold + joint) - (b_hot + joint) * outer_len) / ring_det + 5", actor="a", reason="shift A count")
    r1 = close_ring(2 * math.pi * 2800, 2 * math.pi * 3700, (125.0, 155.0), (110.0, 175.0), 0.5, 40.0)
    assert r1["exact_nA"] == pytest.approx(r0["exact_nA"] + 5.0, abs=0.05)


def test_ui_extraction_formula_cascade(core):
    vars_m = {"vessel_h": 10000, "trans_count": 2, "mouth_count": 2, "course": 200, "joint": 0}
    assert core.formulas.evaluate("ui_mouth_start_elev", vars_m) == pytest.approx(9200.0)
    core.formulas.set_expression("ui_mouth_height", "(trans_count + mouth_count + 1) * (course + joint)", actor="a", reason="extra buffer course")
    assert core.formulas.evaluate("ui_mouth_start_elev", vars_m) == pytest.approx(9000.0)


def test_geometry_assembler_extraction_formulas(tmp_path, monkeypatch):
    import refractory_core
    from app.models.vessel_geometry import SegmentType, VesselSegment
    from app.services.part2_extraction.geometry_assembler import _compute_slopes
    core = RefractoryCore(tmp_path)
    monkeypatch.setattr(refractory_core, "_default", core)
    assert core.formulas.evaluate("ext_outer_radius", {"od": 7000.0}) == pytest.approx(3500.0)
    segs = [
        VesselSegment(
            index=0,
            segment_type=SegmentType.CONE,
            label="Lower cone",
            height_mm=2000.0,
            radius_bottom_mm=2500.0,
            radius_top_mm=3500.0,
        )
    ]
    _compute_slopes(segs)
    assert segs[0].slope_angle_deg == pytest.approx(26.57, abs=0.05)


