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


def test_trl_shape_library_loaded_and_validated(core):
    from refractory_core import ShapeLibraryValidationError
    from app.services.part3_lining.lining.bom import unit_volume
    from app.services.part3_lining.lining.bricks import wall_pairs

    lib = core.shapes.get_library()
    assert lib["total_marks"] >= 250
    chk = core.shapes.validate_import_payload(lib)
    assert chk["ok"], f"TRL library validation errors: {chk['errors']}"

    # Verify Key Brick 65/120 from TRLK/SS/BOF/06/08.10.2016
    b65 = core.shapes.get_mark("65/120")
    assert b65["dimensions_mm"]["length"] == 650
    assert b65["dimensions_mm"]["width_top"] == 210
    assert b65["dimensions_mm"]["width_bottom"] == 90
    assert b65["source_ref"]["document_no"] == "TRLK/SS/BOF/06/08.10.2016"
    assert unit_volume(b65)[0] == pytest.approx(9.75)

    # Verify Barrel Safety Bricks TYP1 (T=150) and TYP30 (T=114)
    typ1 = core.shapes.get_mark("TYP1")
    assert typ1["dimensions_mm"]["T"] == 150 and typ1["dimensions_mm"]["C"] == 300
    assert unit_volume(typ1)[0] == pytest.approx(3.375)
    typ30 = core.shapes.get_mark("TYP30")
    assert typ30["dimensions_mm"]["T"] == 114 and typ30["dimensions_mm"]["B"] == 55
    assert unit_volume(typ30)[0] == pytest.approx(1.8525)

    # Verify Triple Tapered 80 PSR-1 and Beveled Key HT 60/20
    ht60 = core.shapes.get_mark("HT 60/20")
    assert unit_volume(ht60)[0] == pytest.approx(8.39, abs=0.02)
    psr = core.shapes.get_mark("80 PSR-1")
    assert psr["catalog_volume_dm3"] == pytest.approx(8.25)

    # Verify wall_pairs generates valid A/B pairs across all TRL series (200mm..1200mm)
    pairs = wall_pairs(lib)
    lengths = sorted({p["L"] for p in pairs})
    for expected_len in (200, 250, 300, 350, 400, 450, 500, 550, 600, 650, 700, 750, 800, 850, 900, 950, 1000, 1100, 1200):
        assert expected_len in lengths

    # Test importing a custom shape and rejecting an invalid shape
    with pytest.raises(ShapeLibraryValidationError):
        core.shapes.import_shapes({"bricks": [{"mark": "BAD-1", "shape_family": "standard_trapezoidal", "dimensions_mm": {"length": -10}}]}, actor="darshan")

    res = core.shapes.import_shapes({
        "bricks": [{
            "mark": "TRL-CUST-1",
            "shape_family": "standard_trapezoidal",
            "zone": "Barrel",
            "dimensions_mm": {"length": 700, "width_top": 165, "width_bottom": 135, "thickness": 100},
            "source_ref": {"document_no": "TRLK/CUSTOM/01", "revision": "1.0"}
        }]
    }, actor="darshan", reason="Add custom barrel mark")
    assert res["imported_count"] == 1
    assert core.shapes.get_mark("TRL-CUST-1")["dimensions_mm"]["width_top"] == 165


def test_fr04_2_all_formulas_properties_and_shapes_have_source_ref(core):
    # Every formula must have structured source_ref with document_no and revision
    for f in core.formulas.list(include_advanced=True):
        sr = f.get("source_ref")
        assert isinstance(sr, dict) and sr.get("document_no"), f"Formula {f['id']} missing source_ref.document_no"
        assert sr.get("revision"), f"Formula {f['id']} missing source_ref.revision"

    # Every property must have structured source_ref
    for p in core.properties.list():
        sr = p.get("source_ref")
        assert isinstance(sr, dict) and sr.get("document_no"), f"Property {p['id']} missing source_ref.document_no"

    # Every brick shape must have structured source_ref
    for b in core.shapes.get_library()["bricks"]:
        sr = b.get("source_ref")
        assert isinstance(sr, dict) and sr.get("document_no"), f"Brick {b['mark']} missing source_ref.document_no"


def test_monolithic_14_shapes_area_volume_anchors_match_client_pdf(core):
    f = core.formulas

    # 1. Rectangular Plate (Page 3): L=1450, B=155, T=200, Pitch=10x10 -> Area=224750, Vol=44950000, Anchors=2247.5
    a1 = f.evaluate("mono_rect_plate_area", {"L": 1450, "B": 155})
    v1 = f.evaluate("mono_rect_plate_vol", {"L": 1450, "B": 155, "T": 200})
    q1 = f.evaluate("mono_anchors_qty", {"area_mm2": a1, "pitch_x": 10, "pitch_y": 10})
    assert a1 == pytest.approx(224750.0)
    assert v1 == pytest.approx(44950000.0)
    assert q1 == pytest.approx(2247.5)

    # 2. Isosceles Triangle Plate (Page 5): B=1674, H=725, T=75, Pitch=100x100 -> Area=606825, Vol=45511875, Anchors=60.6825
    a2 = f.evaluate("mono_isosceles_tri_area", {"B": 1674, "H": 725})
    v2 = f.evaluate("mono_isosceles_tri_vol", {"B": 1674, "H": 725, "T": 75})
    q2 = f.evaluate("mono_anchors_qty", {"area_mm2": a2, "pitch_x": 100, "pitch_y": 100})
    assert a2 == pytest.approx(606825.0)
    assert v2 == pytest.approx(45511875.0)
    assert q2 == pytest.approx(60.6825)

    # 3. Equilateral Triangle Plate (Pages 7-8): H=5000, T=200, Pitch=150x150 -> S=5773.5027, Area=14433757, Vol=2.8867513e9, Anchors=641.5003
    s3 = f.evaluate("mono_equilateral_tri_side_from_h", {"H": 5000})
    a3 = f.evaluate("mono_equilateral_tri_area_from_h", {"H": 5000})
    v3 = f.evaluate("mono_equilateral_tri_vol_from_h", {"H": 5000, "T": 200})
    q3 = f.evaluate("mono_anchors_qty", {"area_mm2": a3, "pitch_x": 150, "pitch_y": 150})
    assert s3 == pytest.approx(5773.5027, rel=1e-6)
    assert a3 == pytest.approx(14433756.73, rel=1e-6)
    assert v3 == pytest.approx(2886751346.0, rel=1e-6)
    assert q3 == pytest.approx(641.5003, rel=1e-5)

    # 4. Parallelogram Plate (Page 11): B=2000, H=2000, T=50, Pitch=100x100 -> Area=4000000, Vol=200000000, Anchors=400
    a4 = f.evaluate("mono_parallelogram_area", {"B": 2000, "H": 2000})
    v4 = f.evaluate("mono_parallelogram_vol", {"B": 2000, "H": 2000, "T": 50})
    q4 = f.evaluate("mono_anchors_qty", {"area_mm2": a4, "pitch_x": 100, "pitch_y": 100})
    assert a4 == pytest.approx(4000000.0)
    assert v4 == pytest.approx(200000000.0)
    assert q4 == pytest.approx(400.0)

    # 5. Trapezoid Plate (Page 15): A=1881, B=1450, H=2475, T=75, Pitch=100x100 -> Area=4122112.5, Vol=309158437.5, Anchors=412.21125
    a5 = f.evaluate("mono_trapezoid_area", {"A": 1881, "B": 1450, "H": 2475})
    v5 = f.evaluate("mono_trapezoid_vol", {"A": 1881, "B": 1450, "H": 2475, "T": 75})
    q5 = f.evaluate("mono_anchors_qty", {"area_mm2": a5, "pitch_x": 100, "pitch_y": 100})
    assert a5 == pytest.approx(4122112.5)
    assert v5 == pytest.approx(309158437.5)
    assert q5 == pytest.approx(412.21125)

    # 6. Circle Plate (Page 16): D=2000, T=50, Pitch=100x100 -> Area=3141592.654, Vol=157079632.7, Anchors=314.1592654
    a6 = f.evaluate("mono_circle_area", {"D": 2000})
    v6 = f.evaluate("mono_circle_vol", {"D": 2000, "T": 50})
    q6 = f.evaluate("mono_anchors_qty", {"area_mm2": a6, "pitch_x": 100, "pitch_y": 100})
    assert a6 == pytest.approx(3141592.6536, rel=1e-6)
    assert v6 == pytest.approx(157079632.68, rel=1e-6)
    assert q6 == pytest.approx(314.1592654, rel=1e-6)

    # 7. Sector of Circle Plate (Page 22): D=2000, theta=50 deg, T=50, Pitch=100x100 -> Area=436332.313, Vol=21816615.65, Anchors=43.6332313
    a7 = f.evaluate("mono_sector_circle_area", {"D": 2000, "theta_deg": 50})
    v7 = f.evaluate("mono_sector_circle_vol", {"D": 2000, "theta_deg": 50, "T": 50})
    q7 = f.evaluate("mono_anchors_qty", {"area_mm2": a7, "pitch_x": 100, "pitch_y": 100})
    assert a7 == pytest.approx(436332.313, rel=1e-6)
    assert v7 == pytest.approx(21816615.65, rel=1e-6)
    assert q7 == pytest.approx(43.6332313, rel=1e-6)

    # 8. Elliptical Plate (Page 27): A=1500, B=50, T=15, Pitch=100x100 -> Area=235619.449, Vol=3534291.735, Anchors=23.5619449
    a8 = f.evaluate("mono_ellipse_area", {"A": 1500, "B": 50})
    v8 = f.evaluate("mono_ellipse_vol", {"A": 1500, "B": 50, "T": 15})
    q8 = f.evaluate("mono_anchors_qty", {"area_mm2": a8, "pitch_x": 100, "pitch_y": 100})
    assert a8 == pytest.approx(235619.449, rel=1e-6)
    assert v8 == pytest.approx(3534291.735, rel=1e-6)
    assert q8 == pytest.approx(23.5619449, rel=1e-6)

    # 9. Circular Duct (Page 29): D=2000, T=100, L=25, Pitch=100x100 -> d=1800, Area=596902.6042, Vol=14922565.1, Anchors=59.69026042
    d9 = f.evaluate("mono_circular_duct_inner_d", {"D": 2000, "T": 100})
    a9 = f.evaluate("mono_circular_duct_area", {"D": 2000, "T": 100})
    v9 = f.evaluate("mono_circular_duct_vol", {"D": 2000, "T": 100, "L": 25})
    q9 = f.evaluate("mono_anchors_qty", {"area_mm2": a9, "pitch_x": 100, "pitch_y": 100})
    assert d9 == pytest.approx(1800.0)
    assert a9 == pytest.approx(596902.6042, rel=1e-6)
    assert v9 == pytest.approx(14922565.105, rel=1e-6)
    assert q9 == pytest.approx(59.69026042, rel=1e-6)

    # 10. Rectangular Duct (Page 33): W=2000, H=2500, T=50, L=25, Pitch=200x200 -> Area=440000, Vol=11000000, Anchors=11
    a10 = f.evaluate("mono_rect_duct_area", {"W": 2000, "H": 2500, "T": 50})
    v10 = f.evaluate("mono_rect_duct_vol", {"W": 2000, "H": 2500, "T": 50, "L": 25})
    q10 = f.evaluate("mono_anchors_qty", {"area_mm2": a10, "pitch_x": 200, "pitch_y": 200})
    assert a10 == pytest.approx(440000.0)
    assert v10 == pytest.approx(11000000.0)
    assert q10 == pytest.approx(11.0)

    # 11. Developed Cylinder Shell (Page 36): D=2000, T=50, L=25, Pitch=200x200 -> Area=157079.6327, Vol=7853981.634, Anchors=3.926990817
    a11 = f.evaluate("mono_cylinder_shell_area", {"D": 2000, "L": 25})
    v11 = f.evaluate("mono_cylinder_shell_vol", {"D": 2000, "L": 25, "T": 50})
    q11 = f.evaluate("mono_anchors_qty", {"area_mm2": a11, "pitch_x": 200, "pitch_y": 200})
    assert a11 == pytest.approx(157079.6327, rel=1e-6)
    assert v11 == pytest.approx(7853981.634, rel=1e-6)
    assert q11 == pytest.approx(3.926990817, rel=1e-6)

    # 12. Developed Cone Shell (Page 39): R=2000, H=400, T=400, Pitch=200x150 -> Area=25381604.41, Anchors=846.0534803
    a12 = f.evaluate("mono_cone_shell_area", {"R": 2000, "H": 400})
    q12 = f.evaluate("mono_anchors_qty", {"area_mm2": a12, "pitch_x": 200, "pitch_y": 150})
    assert a12 == pytest.approx(25381604.41, rel=1e-6)
    assert q12 == pytest.approx(846.0534803, rel=1e-6)

    # 13. Developed Frustum of Cone Shell (Page 41): R=1000, r=500, H=250, T=250, Pitch=200x200 -> Area=2634305.524, Vol=658576381, Anchors=65.8576381
    a13 = f.evaluate("mono_frustum_cone_area", {"R": 1000, "r": 500, "H": 250})
    v13 = f.evaluate("mono_frustum_cone_vol", {"R": 1000, "r": 500, "H": 250, "T": 250})
    q13 = f.evaluate("mono_anchors_qty", {"area_mm2": a13, "pitch_x": 200, "pitch_y": 200})
    assert a13 == pytest.approx(2634305.524, rel=1e-6)
    assert v13 == pytest.approx(658576381.0, rel=1e-6)
    assert q13 == pytest.approx(65.8576381, rel=1e-6)

    # 14. Developed Lid of Sphere (Page 44): r1=500, H=250, T=250, Pitch=200x200 -> R=625, Area=981747.7042, Vol=245436926.1, Anchors=24.54369261
    r14 = f.evaluate("mono_sphere_lid_radius", {"r1": 500, "H": 250})
    a14 = f.evaluate("mono_sphere_lid_area", {"r1": 500, "H": 250})
    v14 = f.evaluate("mono_sphere_lid_vol", {"r1": 500, "H": 250, "T": 250})
    q14 = f.evaluate("mono_anchors_qty", {"area_mm2": a14, "pitch_x": 200, "pitch_y": 200})
    assert r14 == pytest.approx(625.0)
    assert a14 == pytest.approx(981747.7042, rel=1e-6)
    assert v14 == pytest.approx(245436926.1, rel=1e-6)
    assert q14 == pytest.approx(24.54369261, rel=1e-6)


def test_fr04_3_kb_versioning_and_design_stamping(core, tmp_path):
    info0 = core.kb_versions.current_info()
    assert info0["kb_version"].startswith("KB-v1.0-")
    assert info0["components"]["formulas"]["count"] >= 153
    assert info0["components"]["shape_library"]["total_marks"] >= 252

    # Stamp a design and verify snapshot JSON is written on server & reference table is updated
    out_dir = tmp_path / "design_out_1"
    stamp1 = core.kb_versions.stamp_design(
        job_id="job_001",
        job_ref="180MT-BOF-REV1",
        output_dir=out_dir,
        actor="darshan",
        source_documents=[{"filename": "180MT_BOF.dxf", "sha256": "abc12345"}],
    )
    assert stamp1["kb_version"] == info0["kb_version"]
    assert (out_dir / "kb_stamp.json").exists()

    # Load the saved server JSON snapshot by version
    snap_loaded = core.kb_versions.get_version(stamp1["kb_version"])
    assert snap_loaded["kb_version"] == stamp1["kb_version"]
    assert len(snap_loaded["snapshot"]["formulas"]) >= 153

    # Modify a formula -> KB version hash must change deterministically!
    core.formulas.set_expression("d_joint", "1.0", actor="darshan", reason="Test KB version bump")
    info1 = core.kb_versions.current_info()
    assert info1["kb_version"] != info0["kb_version"]

    stamp2 = core.kb_versions.stamp_design(job_id="job_002", job_ref="180MT-BOF-REV2", actor="darshan")
    assert stamp2["kb_version"] == info1["kb_version"]

    stamps = core.kb_versions.list_stamps()
    assert len(stamps) == 2
    assert stamps[0]["job_id"] == "job_002" and stamps[1]["job_id"] == "job_001"


def test_fr05_curved_transitions_mhp_assembly_and_belpahar_limits(core):
    from app.services.part3_lining.lining.bricks import design_mhp_and_taphole_assemblies, transition_pairs, wall_pairs
    from app.services.part3_lining.lining.checks import check_belpahar_manufacturing_limits
    from app.services.part3_lining.lining.bom import unit_volume

    lib = core.shapes.get_library()
    assert lib["total_marks"] >= 273
    assert lib.get("_known_gaps") == []

    # 1. FR-05.1: Verify TSC-1A..TSC-8B, TPZ-1..TPZ-4, and BSM-1 exist with exact geometry & volumes
    tsc1a = core.shapes.get_mark("TSC-1A")
    assert unit_volume(tsc1a)[0] == pytest.approx(5.01, abs=0.05)
    tpz1 = core.shapes.get_mark("TPZ-1")
    assert unit_volume(tpz1)[0] == pytest.approx(11.39, abs=0.05)
    bsm1 = core.shapes.get_mark("BSM-1")
    assert unit_volume(bsm1)[0] == pytest.approx(5.95, abs=0.05)

    pairs = wall_pairs(lib) + transition_pairs(lib)
    pair_ids = {p["id"] for p in pairs}
    for tier in range(1, 9):
        assert f"TSC-{tier}A+TSC-{tier}B" in pair_ids
    assert "TPZ-1+TPZ-2" in pair_ids and "TPZ-3+TPZ-4" in pair_ids

    # 2. FR-05.2: Verify MHP (bottom plug) & Taphole assembly design engine
    asm = design_mhp_and_taphole_assemblies(
        lib,
        nozzles=[{"type": "stirring", "label": "MHP", "count": 6, "pcd": 2100}, {"type": "taphole", "label": "Tap Hole"}],
        bottom={"brick_length": 800, "pcd": 2100},
        densities={"GRADE-A": 3.05, "GRADE-F": 2.95, "GRADE-G": 2.95, "GRADE-H": 3.00, "GRADE-I": 3.01},
    )
    assert asm["mhp_assembly"]["plug_count"] == 6
    assert any(i["mark"] == "MHP" and i["total_qty"] == 6 for i in asm["mhp_assembly"]["items"])
    assert any(i["mark"] == "MHP Seating Block" and i["total_qty"] == 6 for i in asm["mhp_assembly"]["items"])
    assert any(i["mark"] == "80 PSR-1" and i["total_qty"] == 12 for i in asm["mhp_assembly"]["items"])
    assert len(asm["taphole_assembly"]["items"]) == 11  # TB-1..TB-5 + TS-1..TS-6

    # 3. FR-05.3: Verify Belpahar manufacturing limits pass for all 273 standard TRL shapes
    by_mark = {b["mark"]: b for b in lib["bricks"]}
    chk_ok = check_belpahar_manufacturing_limits(by_mark, {"lines": []}, core.formulas)
    assert chk_ok[0]["ok"], f"Unexpected Belpahar violation: {chk_ok[0]['detail']}"

    # Verify a non-standard oversized/over-tapered brick triggers a Belpahar violation
    bad_lib = {
        "OVERSIZED-1": {
            "mark": "OVERSIZED-1",
            "shape_family": "standard_trapezoidal",
            "dimensions_mm": {"length": 1450, "width_top": 240, "width_bottom": 25, "thickness": 100},
            "catalog_volume_dm3": 40.0,
        }
    }
    chk_bad = check_belpahar_manufacturing_limits(bad_lib, {"lines": [{"mark": "OVERSIZED-1", "unit_wt_kg": 122.0}]}, core.formulas)
    assert not chk_bad[0]["ok"]
    assert "length 1450 mm > Belpahar max 1200 mm" in chk_bad[0]["detail"]
    assert "min tip width 25 mm < Belpahar min 34 mm" in chk_bad[0]["detail"]
    assert "taper diff (a-b)=215 mm > Belpahar max 120 mm" in chk_bad[0]["detail"]
    assert "unit weight 122.0 kg > Belpahar max pressed brick limit 95 kg" in chk_bad[0]["detail"]


def test_multiuser_auth_and_session_isolation(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.core import auth

    monkeypatch.setattr(auth, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(auth, "SECRET_FILE", tmp_path / ".auth_secret")
    monkeypatch.setattr(auth, "USER_SESSIONS_ROOT", tmp_path / "user_sessions")
    client = TestClient(app)

    # 1. Sign in as designer1
    r1 = client.post("/api/v1/auth/login", json={"username": "designer1", "password": "trl2026"})
    assert r1.status_code == 200
    t1 = r1.json()["token"]
    h1 = {"Authorization": f"Bearer {t1}"}

    # 2. Save a session as designer1
    s1 = client.post(
        "/api/v1/core/sessions",
        headers=h1,
        json={"id": "sess_d1_01", "name": "Designer 1 Private BOF", "state": {"job": "D1-180MT-BOF"}},
    )
    assert s1.status_code == 200
    assert s1.json()["owner"] == "designer1"

    # Verify designer1 sees their session
    list1 = client.get("/api/v1/core/sessions", headers=h1)
    assert list1.status_code == 200
    assert len(list1.json()) == 1
    assert list1.json()[0]["id"] == "sess_d1_01"

    # 3. Sign in as designer2
    r2 = client.post("/api/v1/auth/login", json={"username": "designer2", "password": "trl2026"})
    assert r2.status_code == 200
    t2 = r2.json()["token"]
    h2 = {"Authorization": f"Bearer {t2}"}

    # 4. Verify designer2 CANNOT see or load designer1's session
    list2 = client.get("/api/v1/core/sessions", headers=h2)
    assert list2.status_code == 200
    assert list2.json() == []

    get_other = client.get("/api/v1/core/sessions/sess_d1_01", headers=h2)
    assert get_other.status_code == 404

    del_other = client.delete("/api/v1/core/sessions/sess_d1_01", headers=h2)
    assert del_other.status_code == 404

    # Verify designer1's session is still intact
    get_own = client.get("/api/v1/core/sessions/sess_d1_01", headers=h1)
    assert get_own.status_code == 200
    assert get_own.json()["state"]["job"] == "D1-180MT-BOF"







