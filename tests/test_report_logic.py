"""Offline tests for the design review report logic (no ETABS needed)."""
import json

import pytest

import report_extractor as rx
import report_modifiers as rm


# ---------------------------------------------------------------- mapping
def test_mapping_round_trip_and_defaults(tmp_path):
    m = rx.ReportMapping(eq_x=["EX"], eq_y=["EY"], spec_x=["SX"], modal_case="Modal", dead_case="DL", live_case="LL")
    m.modifiers["floors"][0] = 0.4
    back = rx.ReportMapping.from_dict(json.loads(json.dumps(m.to_dict())))
    assert back.eq_x == ["EX"] and back.modifiers["floors"] == [0.4, 0.25]
    assert back.modifiers["beams"] == [0.70, 0.35]            # defaults filled in
    model = str(tmp_path / "m.EDB")
    rx.save_mapping(model, m)
    loaded, found = rx.load_mapping(model)
    assert found and loaded.spec_x == ["SX"]
    assert rx.load_mapping(str(tmp_path / "other.EDB")) == (None, False)


def test_mapping_problems_name_the_missing_pieces():
    m = rx.ReportMapping(eq_x=["EX", "GONE"], eq_y=[], modal_case="Modal", dead_case="DL", live_case="")
    errs = m.problems(["EX", "Modal", "DL"])
    assert any("GONE" in e for e in errs)
    assert any("EQ (static seismic) Y" in e for e in errs)
    assert any("Live load case is not set" in e for e in errs)


# ---------------------------------------------------------------- modifier sets
M = rx.ReportMapping()


def test_target_arrays_follow_the_questionnaire():
    frame = (1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)
    assert rm.target_array("frame", "beam", frame, M, "ULS") == (1, 1, 1, 1, 0.35, 0.35, 1, 1)
    assert rm.target_array("frame", "beam", frame, M, "SLS")[4] == 0.70
    assert rm.target_array("frame", "column", frame, M, "SLS")[4] == 0.90
    area = (1.0,) * 10
    assert rm.target_array("area", "wall", area, M, "ULS") == (0.7,) * 6 + (1.0,) * 4
    assert rm.target_array("area", "floor", area, M, "ULS") == (1, 1, 1, 0.25, 0.25, 0.25, 1, 1, 1, 1)
    assert rm.target_array("area", "floor", area, M, "SLS")[3:6] == (0.35,) * 3
    assert rm.target_array("area", None, area, M, "ULS") == area          # raft / other shells untouched


def test_frame_J_only_scaled_when_asked():
    frame = (1.0,) * 8
    assert rm.target_array("frame", "beam", frame, M, "ULS")[3] == 1.0
    m2 = rx.ReportMapping(scale_frame_J=True)
    assert rm.target_array("frame", "beam", frame, m2, "ULS")[3] == 0.35


def _snap(frame_vals, wall_vals, floor_vals, raft_vals=(1.0,) * 10):
    return {"frames": {"1": ("beam", tuple(frame_vals)), "2": ("column", tuple(frame_vals))},
            "areas": {"w": ("wall", tuple(wall_vals)), "f": ("floor", tuple(floor_vals)), "r": (None, tuple(raft_vals))}}


def _state_snap(set_name):
    b = rm.target_array("frame", "beam", (1.0,) * 8, M, set_name)
    c = rm.target_array("frame", "column", (1.0,) * 8, M, set_name)
    w = rm.target_array("area", "wall", (1.0,) * 10, M, set_name)
    f = rm.target_array("area", "floor", (1.0,) * 10, M, set_name)
    return {"frames": {"1": ("beam", b), "2": ("column", c)}, "areas": {"w": ("wall", w), "f": ("floor", f), "r": (None, (1.0,) * 10)}}


def test_detect_state():
    assert rm.detect_state(_state_snap("ULS"), M)[0] == "ULS"
    assert rm.detect_state(_state_snap("SLS"), M)[0] == "SLS"
    untouched = _snap((1.0,) * 8, (1.0,) * 10, (1.0,) * 10)
    state, counts = rm.detect_state(untouched, M)
    assert state == "other" and counts["ULS"] == 4 and counts["objects"]["untouched"] == 1
    mixed = _state_snap("ULS")
    mixed["areas"]["w"] = ("wall", rm.target_array("area", "wall", (1.0,) * 10, M, "SLS"))
    assert rm.detect_state(mixed, M)[0] == "other"                        # one wrong object = neither set


def test_identical_tables_are_reported_as_both():
    same = rx.ReportMapping()
    same.modifiers = {k: [0.5, 0.5] for k in same.modifiers}
    assert rm.detect_state(_state_snap_for(same, "ULS"), same)[0] == "both"


def _state_snap_for(m, s):
    return {"frames": {"1": ("beam", rm.target_array("frame", "beam", (1.0,) * 8, m, s))},
            "areas": {"w": ("wall", rm.target_array("area", "wall", (1.0,) * 10, m, s))}}


# ---------------------------------------------------------------- applying a set against a fake ETABS
class FakeObj:
    def __init__(self, store, sticky=True, fail=None):
        self.store, self.sticky, self.fail = store, sticky, fail

    def GetNameList(self, n, names):
        return [len(self.store), tuple(self.store), 0]

    def GetModifiers(self, name, v):
        return [tuple(self.store[name]), 0]

    def SetModifiers(self, name, values):
        if self.fail == name:
            return [tuple(values), 1]
        if self.sticky:
            self.store[name] = tuple(values)
        return [tuple(values), 0]


class FakeSM:
    def __init__(self, frames, areas, sticky=True, fail=None):
        self.FrameObj, self.AreaObj = FakeObj(frames, sticky, fail), FakeObj(areas, sticky)
        self.locked = True
        self.model = type("O", (), {"GetNameList": lambda s, a, b: [0, (), 0]})()
        self.PropFrame = self.PropArea = self.model

    def SetModelIsLocked(self, v):
        self.locked = v


def _fake_snapshot(monkeypatch, sm):
    """snapshot() needs geometry; for the fake, classify by object name."""
    def snap(SM, log=print):
        return {"frames": {n: ("beam", tuple(v)) for n, v in SM.FrameObj.store.items()},
                "areas": {n: ("wall" if n.startswith("w") else "floor", tuple(v)) for n, v in SM.AreaObj.store.items()}}
    monkeypatch.setattr(rm, "snapshot", snap)


def test_apply_set_writes_and_verifies(monkeypatch):
    sm = FakeSM({"b1": (1.0,) * 8}, {"w1": (1.0,) * 10, "f1": (1.0,) * 10})
    _fake_snapshot(monkeypatch, sm)
    base = rm.snapshot(sm)
    assert rm.apply_set(sm, base, M, "ULS", log=lambda *_: None) == 3
    assert sm.locked is False                                           # editing unlocks, as ETABS does
    assert sm.FrameObj.store["b1"][4] == 0.35 and sm.AreaObj.store["w1"][:6] == (0.7,) * 6
    assert rm.count_mismatches(rm.snapshot(sm), M, "ULS", base=base) == 0


def test_apply_set_refuses_to_continue_if_readback_differs(monkeypatch):
    sm = FakeSM({"b1": (1.0,) * 8}, {"w1": (1.0,) * 10}, sticky=False)   # writes "succeed" but do not stick
    _fake_snapshot(monkeypatch, sm)
    with pytest.raises(RuntimeError, match="read-back failed"):
        rm.apply_set(sm, rm.snapshot(sm), M, "SLS", log=lambda *_: None)


def test_apply_set_stops_on_an_api_error(monkeypatch):
    sm = FakeSM({"b1": (1.0,) * 8}, {"w1": (1.0,) * 10}, fail="b1")
    _fake_snapshot(monkeypatch, sm)
    with pytest.raises(RuntimeError, match="SetModifiers failed"):
        rm.apply_set(sm, rm.snapshot(sm), M, "SLS", log=lambda *_: None)


# ---------------------------------------------------------------- file names after Save As
def test_model_file_and_same_model():
    class S:
        def __init__(self, p): self.p = p
        def GetModelFilename(self): return self.p
    assert rm.model_file(S(r"D:\a\m.$et")) == r"D:\a\m.EDB"
    assert rm.model_file(S(r"D:\a\m.EDB")) == r"D:\a\m.EDB"
    assert rm.same_model(r"D:\a\m.$et", r"d:/a/M.EDB")
    assert not rm.same_model(r"D:\a\m.EDB", r"D:\a\n.EDB")
    assert not rm.same_model("", r"D:\a\m.EDB")


def test_work_folder_does_not_nest(tmp_path):
    first = rm.work_folder(str(tmp_path / "m.EDB"))
    assert first.endswith("report_work")
    assert rm.work_folder(first + "\\m_copy.EDB") == first


# ---------------------------------------------------------------- geometry helpers
def test_polygon_area():
    assert rx.polygon_area([(0, 0, 0), (4, 0, 0), (4, 3, 0), (0, 3, 0)]) == 12.0


def test_corner_points_follow_the_plan_numbering():
    joints = [("a", (0, 0, 0)), ("b", (10, 0, 0)), ("c", (10, 6, 0)), ("d", (0, 6, 0)), ("mid", (5, 3, 0))]
    names = [j[0] for j in rx.corner_joints(joints)]
    assert names == ["d", "c", "a", "b"]          # 1 top-left, 2 top-right, 3 bottom-left, 4 bottom-right


# ---------------------------------------------------------------- the ledger and checks
def test_ledger_collects_warnings_for_warn_and_fail_only():
    L = rx.Ledger()
    L.check("a", "pass", "fine")
    L.check("b", "warn", "look")
    L.check("c", "fail", "bad")
    assert [w[:3] for w in L.warnings] == ["[b]", "[c]"]
    assert [c["status"] for c in L.checks] == ["pass", "warn", "fail"]
