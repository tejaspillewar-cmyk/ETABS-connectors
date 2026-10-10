"""
Design review report: reading the report inputs from an ETABS model (ETABS 23 OAPI).

This module only READS. It never changes the model (the present units are
switched to kN, mm while reading and restored, and the Results.Setup output
selection is changed, as fdr_tool.py also does). Changing stiffness modifiers and
re-running the analysis is done in report_modifiers.py and orchestrated by
report_pipeline.py.

Every number placed in the report is traceable: build_report() returns the
ReportData dict (etabs-report/src/model/types.ts, schemaVersion 1) plus an
"extraction" block with the mapping, the source of every value, the checks that
were run, notes and warnings. docs/REPORT_EXTRACTION_EXPLAINED.md explains each one.
"""
import collections
import contextlib
import datetime
import json
import math
import os
import re
import sys
from dataclasses import asdict, dataclass, field

G_MM_S2 = 9806.65            # kN, mm units: mass is kN*s^2/mm
UNITS_KN_MM_C = 5
SLAB_RAFT_TYPES = (5, 6)     # eSlabType Mat, Footing

ROLE_KEYS = ("eq_x", "eq_y", "spec_x", "spec_y", "gust_x", "gust_y", "static_x", "static_y")
ROLE_TITLES = {
    "eq_x": "EQ (static seismic) X", "eq_y": "EQ (static seismic) Y",
    "spec_x": "SPEC (response spectrum) X", "spec_y": "SPEC (response spectrum) Y",
    "gust_x": "Gust wind X", "gust_y": "Gust wind Y",
    "static_x": "Static wind X", "static_y": "Static wind Y",
}
# Which stiffness-modifier set each role is reported from (questionnaire Q22).
ROLE_PASS = {"eq_x": "ULS", "eq_y": "ULS", "spec_x": "ULS", "spec_y": "ULS",
             "gust_x": "SLS", "gust_y": "SLS", "static_x": "SLS", "static_y": "SLS"}
DRIFT_ROLES = ("spec_x", "spec_y", "gust_x", "gust_y")
TORSION_ROLES = ("gust_x", "gust_y", "spec_x", "spec_y")

DEFAULT_MODIFIERS = {
    # [SLS (service), ULS (design)]
    "beams": [0.70, 0.35],
    "columns": [0.90, 0.70],
    "walls": [0.90, 0.70],
    "floors": [0.35, 0.25],
}


@dataclass
class ReportMapping:
    """Per-model settings. Saved next to the model; edited in the GUI."""
    eq_x: list = field(default_factory=list)
    eq_y: list = field(default_factory=list)
    spec_x: list = field(default_factory=list)
    spec_y: list = field(default_factory=list)
    gust_x: list = field(default_factory=list)
    gust_y: list = field(default_factory=list)
    static_x: list = field(default_factory=list)
    static_y: list = field(default_factory=list)
    modal_case: str = ""
    dead_case: str = ""
    live_case: str = ""
    ground_story: str = ""          # "" = story named G / Ground, else the base (elevation 0)
    typical_story: str = ""         # "" = automatic
    wind_drift_limit: float = 0.002
    seismic_drift_limit: float = 0.004
    live_load_factor_for_weight: float = 0.25
    area_variation_flag: float = 0.10
    translation_min: float = 0.6    # suggestion thresholds only; the engineer decides
    torsion_max: float = 0.3
    modifiers: dict = field(default_factory=lambda: json.loads(json.dumps(DEFAULT_MODIFIERS)))
    scale_frame_J: bool = False     # also scale the torsion constant of frames (the model keeps J = 1)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        m = cls()
        for k, v in (d or {}).items():
            if hasattr(m, k):
                setattr(m, k, v)
        mods = json.loads(json.dumps(DEFAULT_MODIFIERS))
        mods.update(m.modifiers or {})
        m.modifiers = mods
        return m

    def all_cases(self, roles=None):
        out = []
        for r in (roles or ROLE_KEYS):
            out += [c for c in getattr(self, r) if c not in out]
        return out

    def problems(self, all_cases):
        """Mapping errors that must stop the run."""
        errs = []
        known = set(all_cases)
        for r in ROLE_KEYS:
            for c in getattr(self, r):
                if c not in known:
                    errs.append(f"{ROLE_TITLES[r]}: case '{c}' does not exist in the model.")
        for label, c in (("Modal", self.modal_case), ("Dead load", self.dead_case), ("Live load", self.live_case)):
            if not c:
                errs.append(f"{label} case is not set.")
            elif c not in known:
                errs.append(f"{label} case '{c}' does not exist in the model.")
        for r in ("eq_x", "eq_y"):
            if not getattr(self, r):
                errs.append(f"No case is mapped for {ROLE_TITLES[r]}.")
        return errs


def mapping_path(model_path):
    base, _ = os.path.splitext(model_path)
    return base + ".report_mapping.json"


def load_mapping(model_path):
    try:
        with open(mapping_path(model_path), encoding="utf-8") as fh:
            return ReportMapping.from_dict(json.load(fh)), True
    except (OSError, ValueError):
        return None, False


def save_mapping(model_path, mapping):
    with open(mapping_path(model_path), "w", encoding="utf-8") as fh:
        json.dump(mapping.to_dict(), fh, indent=2)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
@contextlib.contextmanager
def kn_mm_units(SM):
    original = SM.GetPresentUnits()
    SM.SetPresentUnits(UNITS_KN_MM_C)
    try:
        yield
    finally:
        SM.SetPresentUnits(original)


def read_table(SM, name):
    """A database table as a list of {field: str} rows."""
    r = SM.DatabaseTables.GetTableForDisplayArray(name, [], "All", 0, [], 0, [])
    if r[-1] != 0:
        raise RuntimeError(f"Could not read table '{name}' (ret={r[-1]}).")
    fields, n, data = list(r[2]), r[3], list(r[4])
    k = len(fields)
    return [dict(zip(fields, data[i * k:(i + 1) * k])) for i in range(n)]


def select_cases(SM, cases):
    S = SM.Results.Setup
    S.DeselectAllCasesAndCombosForOutput()
    for c in cases:
        S.SetCaseSelectedForOutput(c, True)


def case_names(SM):
    return [c for c in SM.LoadCases.GetNameList(0, [])[1]]


def case_status(SM):
    r = SM.Analyze.GetCaseStatus(0, [], [])
    return dict(zip(r[1], r[2]))                  # 4 = finished


def guess_mapping(SM):
    """First guess of the case roles from case types and names. The engineer confirms it."""
    m = ReportMapping()
    spectrum = []
    for n in case_names(SM):
        if n.startswith("~"):
            continue
        try:
            t = SM.LoadCases.GetTypeOAPI_1(n, 0, 0, 0, 0)
            ctype, design = t[0], t[2]
        except Exception:
            continue
        d = re.search(r"([XY])\d*$", n.upper())
        direction = d.group(1).lower() if d else None
        if ctype == 3 and not m.modal_case:
            m.modal_case = n
        elif ctype == 4 and direction:
            spectrum.append((n, direction))
        elif ctype == 1 and design == 1 and not m.dead_case:
            m.dead_case = n
        elif ctype == 1 and design == 3 and not m.live_case:
            m.live_case = n
        elif ctype == 1 and design == 5 and direction:
            getattr(m, f"eq_{direction}").append(n)
        elif ctype == 1 and design == 6 and direction:
            gust = re.search(r"GW|GUST", n.upper()) is not None
            getattr(m, f"{'gust' if gust else 'static'}_{direction}").append(n)
    pick = [x for x in spectrum if re.search(r"SPEC|^RS|^S[XY]", x[0].upper())] or spectrum
    for n, direction in pick:
        getattr(m, f"spec_{direction}").append(n)
    return m


def describe_cases(SM):
    """[(name, type text, flagged to run)] for the mapping dialog; auto-created '~' cases are left out."""
    labels = {1: "Linear static", 2: "Nonlinear static", 3: "Modal", 4: "Response spectrum", 5: "Linear history",
              6: "Nonlinear history", 7: "Buckling", 8: "Hyperstatic", 9: "Other"}
    flags = SM.Analyze.GetRunCaseFlag(0, [], [])
    flagged = dict(zip(flags[1], flags[2]))
    out = []
    for n in case_names(SM):
        if n.startswith("~"):
            continue
        try:
            ctype = SM.LoadCases.GetTypeOAPI_1(n, 0, 0, 0, 0)[0]
        except Exception:
            ctype = 0
        out.append((n, labels.get(ctype, f"type {ctype}"), bool(flagged.get(n, True))))
    return out


def base_reactions(SM, cases):
    """{case: (Fx, Fy, Fz, Mx, My, Mz)} in kN, kN-mm."""
    if not cases:
        return {}
    select_cases(SM, cases)
    r = SM.Results.BaseReact(0, [], [], [], [], [], [], [], [], [], 0.0, 0.0, 0.0)
    if r[-1] != 0:
        raise RuntimeError(f"Results.BaseReact failed (ret={r[-1]}).")
    out = {}
    for i in range(r[0]):
        out[r[1][i]] = tuple(r[k][i] for k in range(4, 10))
    return out


def story_drifts(SM, case):
    """[(story, direction, drift)] for one case."""
    select_cases(SM, [case])
    r = SM.Results.StoryDrifts(0, [], [], [], [], [], [], [], [], [], [])
    if r[-1] != 0 or r[0] == 0:
        return []
    return [(r[1][i], r[5][i], r[6][i]) for i in range(r[0])]


def joint_disp(SM, joint, cases, select=True):
    """{case: (U1, U2)} in mm (the larger of the steps for envelope cases)."""
    if select:
        select_cases(SM, cases)
    r = SM.Results.JointDispl(joint, 0, 0, [], [], [], [], [], [], [], [], [], [], [])
    out = {}
    for i in range(r[0]):
        c = r[3][i]
        u = (r[6][i], r[7][i])
        if c not in out or abs(u[0]) + abs(u[1]) > abs(out[c][0]) + abs(out[c][1]):
            out[c] = u
    return out


class Coords:
    def __init__(self, SM):
        self.SM, self.cache = SM, {}

    def __call__(self, p):
        if p not in self.cache:
            r = self.SM.PointObj.GetCoordCartesian(p, 0.0, 0.0, 0.0)
            self.cache[p] = (r[0], r[1], r[2])
        return self.cache[p]


def polygon_area(pts):
    s = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i][0], pts[i][1]
        x2, y2 = pts[(i + 1) % len(pts)][0], pts[(i + 1) % len(pts)][1]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def corner_joints(joints):
    """Points 1..4 as drawn in the report: top-left, top-right, bottom-left, bottom-right."""
    def pick(key):
        return max(joints, key=lambda j: key(j[1]))
    return [pick(lambda c: c[1] - c[0]), pick(lambda c: c[0] + c[1]),
            pick(lambda c: -(c[0] + c[1])), pick(lambda c: c[0] - c[1])]


def area_property_types(SM):
    """({property: 'wall' | 'slab' | 'raft' | 'other'}, {wall property: thickness mm})."""
    ptype, thickness = {}, {}
    for n in SM.PropArea.GetNameList(0, [])[1]:
        w = SM.PropArea.GetWall(n, 0, 0, "", 0.0)
        s = SM.PropArea.GetSlab(n, 0, 0, "", 0.0)
        if w[-1] == 0:
            ptype[n], thickness[n] = "wall", w[3]
        elif s[-1] == 0:
            ptype[n] = "raft" if s[0] in SLAB_RAFT_TYPES else "slab"
        else:
            ptype[n] = "other"
    return ptype, thickness


# --------------------------------------------------------------------------
# geometry (does not depend on stiffness modifiers)
# --------------------------------------------------------------------------
def read_geometry(SM, mapping, log=print):
    names = list(SM.Story.GetNameList(0, [])[1])               # top first
    elev = {n: SM.Story.GetElevation(n)[0] for n in names}
    height = {n: SM.Story.GetHeight(n)[0] for n in names}
    base = elev[names[-1]] - height[names[-1]]
    if mapping.ground_story:
        ground, ground_name = elev[mapping.ground_story], mapping.ground_story
    else:
        found = [n for n in names if n.upper() in ("G", "GROUND", "GF")]
        ground = elev[found[0]] if found else base
        ground_name = found[0] if found else "Base"

    log("Reading plan geometry, areas and walls...")
    coords = Coords(SM)
    ptype, thickness = area_property_types(SM)

    per_story = {}
    for st in names:
        on_story = SM.AreaObj.GetNameListOnStory(st, 0, [])
        objs = on_story[1] if on_story[0] else ()
        slab_area, xs, ys, wall_area = 0.0, [], [], 0.0
        for o in objs:
            prop = SM.AreaObj.GetProperty(o, "")[0]
            kind = ptype.get(prop, "other")
            pts = [coords(p) for p in SM.AreaObj.GetPoints(o, 0, [])[1]]
            if kind == "slab":
                slab_area += polygon_area(pts)
                xs += [p[0] for p in pts]
                ys += [p[1] for p in pts]
            elif kind == "wall":
                lowest = min(p[2] for p in pts)
                low = [p for p in pts if abs(p[2] - lowest) < 1.0]
                if len(low) >= 2:
                    length = max(math.hypot(a[0] - b[0], a[1] - b[1]) for a in low for b in low)
                    wall_area += length * thickness.get(prop, 0.0)
        per_story[st] = {"slab_area": slab_area, "wall_area": wall_area,
                         "dx": (max(xs) - min(xs)) if xs else 0.0, "dy": (max(ys) - min(ys)) if ys else 0.0}

    zmap = {round(e): n for n, e in elev.items()}
    by_story = collections.defaultdict(list)
    for p in SM.PointObj.GetNameList(0, [])[1]:
        c = coords(p)
        st = zmap.get(round(c[2]))
        if st:
            by_story[st].append((p, c))
    corners = {st: corner_joints(j) for st, j in by_story.items() if j}
    return {"names": names, "elev": elev, "height": height, "base": base, "ground": ground,
            "ground_name": ground_name, "per_story": per_story, "corners": corners, "coords": coords}


# --------------------------------------------------------------------------
# one pass = everything that is read from one analysis state
# --------------------------------------------------------------------------
def modal_rows(SM, case):
    select_cases(SM, [case])
    r = SM.Results.ModalParticipatingMassRatios(0, [], [], [], [], [], [], [], [], [], [], [], [], [], [])
    rows = []
    for i in range(r[0]):
        ux, uy, rz = r[5][i], r[6][i], r[13][i]
        dom = max((ux, "X-trans"), (uy, "Y-trans"), (rz, "Torsion"))[1]
        rows.append({"mode": i + 1, "period_s": r[4][i], "ux": ux, "uy": uy, "rz": rz, "nature": dom})
    return rows


def storey_displacement_table(SM, cases):
    """{case: {story: [max |X| mm, max |Y| mm]}} from 'Story Max Over Avg Displacements'."""
    SM.DatabaseTables.SetLoadCasesSelectedForDisplay(list(cases))
    out = {c: {} for c in cases}
    for row in read_table(SM, "Story Max Over Avg Displacements"):
        c = row.get("OutputCase")
        if c not in out:
            continue
        try:
            v = abs(float(row["Maximum"]))
        except (KeyError, TypeError, ValueError):
            continue
        d = out[c].setdefault(row["Story"], [0.0, 0.0])
        i = 0 if row.get("Direction", "X").upper().startswith("X") else 1
        d[i] = max(d[i], v)
    return out


def read_pass(SM, mapping, geom, roles, name, log=print, with_singles=True):
    """Read everything the given roles need from the model in its present analysed state."""
    cases = mapping.all_cases(roles)
    singles = [c for c in (mapping.dead_case, mapping.live_case) if c] if with_singles else []
    log(f"[{name}] base reactions for {len(cases) + len(singles)} cases...")
    react = base_reactions(SM, cases + [c for c in singles if c not in cases])
    out = {"name": name, "roles": list(roles), "react": react, "drifts": {}, "modal": [], "joint": {}, "story_disp": {}}
    if with_singles and mapping.modal_case:
        log(f"[{name}] modal results...")
        out["modal"] = modal_rows(SM, mapping.modal_case)
    for r in roles:
        if r in DRIFT_ROLES:
            for c in getattr(mapping, r):
                log(f"[{name}] story drifts {c}...")
                out["drifts"][c] = story_drifts(SM, c)
    tcases = mapping.all_cases([r for r in roles if r in TORSION_ROLES])
    if tcases:
        log(f"[{name}] corner-joint displacements for {len(tcases)} cases on {len(geom['corners'])} stories...")
        select_cases(SM, tcases)
        for st, cj in geom["corners"].items():
            if geom["elev"][st] < geom["ground"]:
                continue
            for i, (jn, _) in enumerate(cj):
                for c, u in joint_disp(SM, jn, tcases, select=False).items():
                    out["joint"][(st, i, c)] = u
    gcases = mapping.all_cases([r for r in roles if r in ("gust_x", "gust_y")])
    if gcases:
        log(f"[{name}] story displacements for the chart...")
        try:
            out["story_disp"] = storey_displacement_table(SM, gcases)
        except Exception as exc:
            out["story_disp_error"] = str(exc)
    return out


# --------------------------------------------------------------------------
# assembling the report
# --------------------------------------------------------------------------
class Ledger:
    """Collects the source of each value and the checks that were run."""
    def __init__(self):
        self.sources, self.checks, self.notes, self.warnings = [], [], [], []

    def src(self, field, how, pass_name=""):
        self.sources.append({"field": field, "source": how, "pass": pass_name})

    def check(self, cid, status, detail):
        self.checks.append({"id": cid, "status": status, "detail": detail})   # pass / warn / fail
        if status in ("warn", "fail"):
            self.warnings.append(f"[{cid}] {detail}")


def _f(react, case, i):
    return react[case][i] if case and case in react else 0.0


def build_report(SM, mapping=None, passes=None, geom=None, plan_png=None, ledger=None,
                 run_info=None, log=print):
    """Assemble ReportData. `passes` = {"ULS": pass, "SLS": pass} or {"CURRENT": pass}.

    With passes=None a single read of the present model state is made ("quick read").
    """
    mapping = mapping or guess_mapping(SM)
    L = ledger or Ledger()
    with kn_mm_units(SM):
        if geom is None:
            geom = read_geometry(SM, mapping, log)
        if passes is None:
            passes = {"CURRENT": read_pass(SM, mapping, geom, ROLE_KEYS, "CURRENT", log)}
            L.warnings.append("Quick read: every value comes from the stiffness-modifier state the model is in now; "
                              "wind (SLS) and seismic (ULS) values are NOT separated. Use Generate for a full report.")
        model_path = SM.GetModelFilename()
        project = os.path.splitext(os.path.basename(model_path))[0] if model_path else ""
        return _assemble(SM, mapping, geom, passes, plan_png, L, run_info, model_path, project)


def _assemble(SM, mapping, geom, passes, plan_png, L, run_info, model_path, project):
    names, elev, height = geom["names"], geom["elev"], geom["height"]
    ground, base, per_story = geom["ground"], geom["base"], geom["per_story"]

    def P(role):                               # the pass a role is reported from
        return passes.get(ROLE_PASS[role]) or passes.get("CURRENT")

    def pname(role):
        p = P(role)
        return p["name"] if p else ""

    top = elev[names[0]]
    h_above, h_below = (top - ground) / 1000.0, (ground - base) / 1000.0
    typical_h = collections.Counter(round(height[n] / 1000.0, 3) for n in (names[:-1] or names)).most_common(1)[0][0]
    L.src("building.heightAboveGround_m", "top story elevation minus ground elevation (Story.GetElevation)")
    L.src("building.heightBelowGround_m", "ground elevation minus base elevation (Story.GetHeight of the lowest story)")
    L.src("building.typicalFloorHeight_m", "most common story height (excluding the lowest story)")

    # ---- forces -----------------------------------------------------------
    def governing(role, idx):
        cs = [c for c in getattr(mapping, role) if P(role) and c in P(role)["react"]]
        return max(cs, key=lambda c: abs(_f(P(role)["react"], c, idx)), default=None)

    ex, ey = governing("eq_x", 0), governing("eq_y", 1)
    eq = {"Vx_kN": _f(P("eq_x")["react"], ex, 0) if ex else 0.0, "My_kNm": _f(P("eq_x")["react"], ex, 4) / 1000.0 if ex else 0.0,
          "Vy_kN": _f(P("eq_y")["react"], ey, 1) if ey else 0.0, "Mx_kNm": _f(P("eq_y")["react"], ey, 3) / 1000.0 if ey else 0.0}
    L.src("seismicForces.Vx_kN / My_kNm", f"base reaction Fx and My of EQ-X case '{ex}' (largest |Fx| if several)", pname("eq_x"))
    L.src("seismicForces.Vy_kN / Mx_kNm", f"base reaction Fy and Mx of EQ-Y case '{ey}' (largest |Fy| if several)", pname("eq_y"))

    def wind_row(rx, ry, label, row_id):
        gx, gy = governing(rx, 0), governing(ry, 1)
        if not gx and not gy:
            return None, None, None
        px, py = P(rx), P(ry)
        fx = abs(_f(px["react"], gx, 0)) if gx else 0.0
        cy = abs(_f(px["react"], gx, 1)) if gx else 0.0
        fy = abs(_f(py["react"], gy, 1)) if gy else 0.0
        cx = abs(_f(py["react"], gy, 0)) if gy else 0.0
        # positional in the workbook: [Fx along X-wind, Fy along Y-wind, Cx across (Y-wind), Cy across (X-wind)]
        row = {"id": row_id, "sourceLabel": label, "Vx_kN": fx, "My_kNm": fy, "Vy_kN": cx, "Mx_kNm": cy}
        L.src(f"windLoadCases[{label}]",
              f"max-along-wind case per direction: X = '{gx}' (Fx = |Fx|, Cy = |Fy| of that case), "
              f"Y = '{gy}' (Fy = |Fy|, Cx = |Fx| of that case); stored in the workbook positions Fx, Fy, Cx, Cy",
              pname(rx))
        return row, gx, gy

    wind_rows = []
    gust_row, gx, gy = wind_row("gust_x", "gust_y", "Gust (ETABS)", "wind-gust")
    if gust_row:
        wind_rows.append(gust_row)
    else:
        L.check("wind_gust", "warn", "No gust wind cases are mapped; the wind row is empty.")
    static_row, _, _ = wind_row("static_x", "static_y", "Static wind (ETABS)", "wind-static")
    if static_row:
        wind_rows.append(static_row)

    # ---- gravity and mass --------------------------------------------------
    pu = P("eq_x") or next(iter(passes.values()))
    total_dead = _f(pu["react"], mapping.dead_case, 2)
    live = _f(pu["react"], mapping.live_case, 2)
    by_type = collections.Counter()
    for row in read_table(SM, "Material List by Object Type"):
        by_type[row["ObjectType"]] += float(row["Weight"])
    self_weight = sum(by_type.values())
    sdl = total_dead - self_weight
    L.src("gravityLoads.deadLoad_kN", "sum of the table 'Material List by Object Type' (self weight of all concrete), as in the sample reports where DL = mass-index total")
    L.src("gravityLoads.superDeadLoad_kN", f"base reaction Fz of '{mapping.dead_case}' minus the self weight (that case holds both the self-weight and super-dead patterns)", pu["name"])
    L.src("gravityLoads.liveLoad_kN", f"base reaction Fz of '{mapping.live_case}'", pu["name"])
    L.src("massComponents", "table 'Material List by Object Type' summed per Column / Beam / Wall / Floor; Raft stays 0 (not identified)")
    mass_components = [{"id": f"mass-{k}", "component": k, "weight_kN": by_type.get(k, 0.0)}
                       for k in ("Column", "Beam", "Wall", "Floor", "Raft")]
    extra = {k: v for k, v in by_type.items() if k not in ("Column", "Beam", "Wall", "Floor", "Raft")}
    if extra:
        L.check("mass_types", "warn", f"Material list has other object types not in the mass index: {extra}")
    if sdl < 0:
        L.check("sdl_sign", "warn", f"Super dead load came out negative ({sdl:,.0f} kN): the dead case total is below the self weight.")
    else:
        L.check("sdl_sign", "pass", f"Super dead load {sdl:,.0f} kN (dead case {total_dead:,.0f} - self weight {self_weight:,.0f}).")

    # ---- modal -------------------------------------------------------------
    modes = (P("eq_x") or pu)["modal"]
    first3 = modes[:3]
    L.src("modalModes", f"first 3 modes of '{mapping.modal_case}'; nature = the largest of UX (X-trans), UY (Y-trans), RZ (Torsion)", pu["name"])
    sug = {}
    if len(first3) >= 2:
        ok = all(m["nature"] != "Torsion" and m["ux"] + m["uy"] >= mapping.translation_min and m["rz"] <= mapping.torsion_max
                 for m in first3[:2])
        sug["Clear Modes"] = "SAFE" if ok else "UNSAFE"
    else:
        L.check("modal", "fail", "Fewer than 2 modes were found.")

    # ---- seismic weight, T -------------------------------------------------
    mass_rows = read_table(SM, "Mass Summary by Story")
    w_above = sum(float(r["UX"]) for r in mass_rows if elev.get(r["Story"], -1e9) > ground) * G_MM_S2
    quarter = total_dead + mapping.live_load_factor_for_weight * live
    L.src("seismic.seismicWeightEtabs_kN", "table 'Mass Summary by Story' (UX mass of stories above ground) x 9806.65 mm/s^2")
    rel = abs(w_above - quarter) / quarter if quarter else 0
    L.check("seismic_weight", "pass" if rel < 0.02 else "warn",
            f"Seismic weight from the mass table {w_above:,.0f} kN vs dead + {mapping.live_load_factor_for_weight:g} live "
            f"{quarter:,.0f} kN ({rel:.1%} apart).")
    T = 0.0672 * h_above ** 0.75
    L.src("seismic.fundamentalPeriod_s", "T = 0.0672 x (height above ground in m)^0.75 (questionnaire Q9)")

    # ---- areas, B, L -------------------------------------------------------
    above = [n for n in names if elev[n] > ground]
    groups = collections.defaultdict(list)
    for n in above:
        groups[round(per_story[n]["slab_area"])].append(n)
    if mapping.typical_story and mapping.typical_story in per_story:
        typical = mapping.typical_story
    else:
        rep = sorted(max(groups.values(), key=len), key=lambda n: elev[n])
        typical = rep[len(rep) // 2]
    ta = per_story[typical]["slab_area"] / 1e6
    total_area = sum(per_story[n]["slab_area"] for n in names) / 1e6
    wall_area = per_story[typical]["wall_area"] / 1e6
    dims = sorted([per_story[typical]["dx"], per_story[typical]["dy"]])
    B, Lm = dims[0] / 1000.0, dims[1] / 1000.0
    L.src("building.typicalFloorArea_m2", f"sum of slab polygon areas on the typical story '{typical}' (the story whose slab area repeats most)")
    L.src("building.etabsTotalArea_m2", "sum of slab polygon areas on all stories (gross, openings not deducted; Mat/Footing slabs excluded)")
    L.src("building.shearWallEtabsArea_m2", f"sum over wall objects on '{typical}' of (plan length of the wall base edge x thickness)")
    L.src("building.widthB_m / lengthL_m", f"smaller / larger bounding-box side of the slabs on '{typical}'")
    varying = [n for n in above[2:] if abs(per_story[n]["slab_area"] / 1e6 - ta) > mapping.area_variation_flag * ta]
    if varying:
        L.check("area_variation", "warn", f"Slab area differs by more than {mapping.area_variation_flag:.0%} from '{typical}' on "
                f"{len(varying)} stories (top 2 excluded): {varying[:10]}{'...' if len(varying) > 10 else ''}. "
                f"B, L and areas are taken from '{typical}'; edit them if another story governs.")
    else:
        L.check("area_variation", "pass", f"Slab area is within {mapping.area_variation_flag:.0%} of '{typical}' on all stories except the top 2.")
    wall_weight_area = by_type.get("Wall", 0.0) / 25.0 / h_above if h_above else 0.0
    rel = abs(wall_area - wall_weight_area) / wall_weight_area if wall_weight_area else 0
    L.check("wall_area", "pass" if rel < 0.05 else "warn",
            f"Wall plan area on '{typical}' {wall_area:.2f} m2 vs wall weight / 25 / total height {wall_weight_area:.2f} m2 ({rel:.1%} apart).")

    # ---- drifts and the torsion story --------------------------------------
    drift_rows, worst = [], (0.0, None)
    for label, role, direction, limit in (("GWX", "gust_x", "X", mapping.wind_drift_limit),
                                          ("GWY", "gust_y", "Y", mapping.wind_drift_limit),
                                          ("SpecX", "spec_x", "X", mapping.seismic_drift_limit),
                                          ("SpecY", "spec_y", "Y", mapping.seismic_drift_limit)):
        pr = P(role)
        best = (0.0, "", "")
        for c in getattr(mapping, role):
            for st, d, v in (pr["drifts"].get(c, []) if pr else []):
                if d.upper().startswith(direction) and abs(v) > best[0]:
                    best = (abs(v), st, c)
        if not getattr(mapping, role):
            continue
        drift_rows.append({"id": f"drift-{label}", "label": f"{label} ({best[1]})" if best[1] else label,
                           "drift": best[0], "limit": limit, "manualCheck": "SAFE" if best[0] <= limit else "UNSAFE"})
        L.src(f"driftChecks[{label}]", f"largest |drift| in direction {direction} over all stories for case(s) {getattr(mapping, role)} "
              f"(here '{best[2]}' at {best[1]}); limit {limit}", pr["name"] if pr else "")
        if best[0] > worst[0]:
            worst = (best[0], best[1])
    sug["Drift check"] = "SAFE" if drift_rows and all(r["manualCheck"] == "SAFE" for r in drift_rows) else "UNSAFE"
    t_story = worst[1] or names[0]
    L.notes.append(f"Torsion points are on {t_story}, the story with the largest drift ({worst[0]:.5f}).")

    cj = geom["corners"].get(t_story)
    torsion = []
    if cj:
        sx = max(getattr(mapping, "spec_x"), key=lambda c: max([abs(v) for s, d, v in (P("spec_x")["drifts"].get(c, []))] or [0]), default=None) if mapping.spec_x else None
        sy = max(getattr(mapping, "spec_y"), key=lambda c: max([abs(v) for s, d, v in (P("spec_y")["drifts"].get(c, []))] or [0]), default=None) if mapping.spec_y else None
        for i, (jn, _) in enumerate(cj):
            def g(role, case, k):
                pr = P(role)
                return pr["joint"].get((t_story, i, case), (0.0, 0.0))[k] if pr and case else 0.0
            torsion.append({"id": f"tp-{i + 1}", "pointLabel": f"{i + 1} ({jn})", "loads": {
                "gwxX": g("gust_x", gx, 0), "gwxY": g("gust_x", gx, 1), "gwyX": g("gust_y", gy, 0), "gwyY": g("gust_y", gy, 1),
                "specXX": g("spec_x", sx, 0), "specXY": g("spec_x", sx, 1), "specYX": g("spec_y", sy, 0), "specYY": g("spec_y", sy, 1)}})
        L.src("torsionCheck", f"4 corner joints of '{t_story}' (points 1-4 = top-left, top-right, bottom-left, bottom-right by the extremes of y-x, x+y, -(x+y), x-y); "
              f"joint displacements U1, U2 under gust cases '{gx}', '{gy}' (SLS) and spectrum cases '{sx}', '{sy}' (ULS)", "SLS + ULS")
    else:
        L.check("torsion", "fail", f"No joints were found at the elevation of story '{t_story}'.")
        torsion = [{"id": f"tp-{i + 1}", "pointLabel": f"Point {i + 1}", "loads": {k: 0.0 for k in
                    ("gwxX", "gwxY", "gwyX", "gwyY", "specXX", "specXY", "specYX", "specYY")}} for i in range(4)]

    # ---- chart -------------------------------------------------------------
    prx = P("gust_x")
    sd = prx["story_disp"] if prx else {}
    if prx and prx.get("story_disp_error"):
        L.check("chart", "warn", f"Story displacement table could not be read: {prx['story_disp_error']}")
    stories_out = []
    for order, n in enumerate(names):
        if elev[n] < ground:
            continue
        ux = sd.get(gx, {}).get(n, [0.0, 0.0])[0] if gx else 0.0
        uy = sd.get(gy, {}).get(n, [0.0, 0.0])[1] if gy else 0.0
        stories_out.append({"id": f"story-{n}", "label": n, "elevation_m": (elev[n] - ground) / 1000.0,
                            "ux_mm": ux, "uy_mm": uy, "order": order})
    if ground == base:
        stories_out.append({"id": "story-base", "label": "Base", "elevation_m": 0.0, "ux_mm": 0.0, "uy_mm": 0.0, "order": len(names)})
    L.src("stories[].ux_mm / uy_mm", f"table 'Story Max Over Avg Displacements', column Maximum, Ux under '{gx}' direction X and Uy under '{gy}' direction Y; "
          "elevation relative to ground; stories below ground are not charted", pname("gust_x"))
    if len(stories_out) > 56:
        L.check("chart_capacity", "warn", f"{len(stories_out)} story rows but the workbook chart holds 56; the lowest {len(stories_out) - 56} are not exported to Excel.")
    if stories_out:
        lim = h_above * 1000.0 / 500.0
        top = stories_out[0]
        sug["Deflection"] = "SAFE" if max(top["ux_mm"], top["uy_mm"]) <= lim else "UNSAFE"
        L.src("checklist suggestions", "Clear Modes: modes 1 and 2 are not torsion, UX+UY >= 0.6 and RZ <= 0.3; Drift check: all four drifts <= limits; "
              "Deflection: top-story Ux and Uy <= H/500. These are suggestions: the engineer decides.")

    # ---- cross-pass checks -------------------------------------------------
    u, s = passes.get("ULS"), passes.get("SLS")
    if u and s:
        a, b = _f(u["react"], mapping.dead_case, 2), _f(s["react"], mapping.dead_case, 2) if mapping.dead_case in s["react"] else None
        if b is not None:
            d = abs(a - b) / max(abs(a), 1.0)
            L.check("mass_independent", "pass" if d < 1e-4 else "fail",
                    f"Dead-load base reaction is the same in both stiffness states ({a:,.1f} vs {b:,.1f} kN): a modifier change must not change weight.")
        if s["modal"] and u["modal"]:
            ts, tu = s["modal"][0]["period_s"], u["modal"][0]["period_s"]
            L.check("period_sanity", "pass" if ts <= tu * 1.0001 else "warn",
                    f"Mode 1 period SLS {ts:.3f} s vs ULS {tu:.3f} s (stiffer SLS should not be longer).")

    eq_ok = bool(ex) and bool(ey)
    L.check("eq_cases", "pass" if eq_ok else "fail", f"EQ cases used: X = {ex}, Y = {ey}.")
    if eq_ok:
        zero = [n for n, v in (("Vx", eq["Vx_kN"]), ("Vy", eq["Vy_kN"])) if abs(v) < 1e-6]
        L.check("eq_forces", "fail" if zero else "pass",
                f"EQ base shear is zero for {', '.join(zero)}: the mapped EQ case has no results or no load." if zero
                else f"EQ base shear Vx {eq['Vx_kN']:,.0f} kN, Vy {eq['Vy_kN']:,.0f} kN.")
    for r_ in wind_rows:
        if all(abs(r_[k]) < 1e-6 for k in ("Vx_kN", "My_kNm", "Vy_kN", "Mx_kNm")):
            L.check("wind_forces", "fail", f"Wind row '{r_['sourceLabel']}' is all zero: the mapped wind cases have no results or no load.")
    needs = ["Engineer initials", "Revision", "Location", "Storey description", "Zone / category / frame type / site class",
             "Z, I, R (the ETABS API has no IS 1893 getter)", "Ah,min", "Consultant wind row (if used)", "CAD plan image"]

    report = {
        "schemaVersion": 1, "source": "etabs-api",
        "metadata": {"projectName": project, "filePath": model_path, "engineerInitials": "",
                     "reportDate": datetime.date.today().isoformat(), "revision": "R0"},
        "building": {"location": "", "storeyDescription": "", "widthB_m": B, "lengthL_m": Lm,
                     "heightAboveGround_m": h_above, "heightBelowGround_m": h_below, "typicalFloorHeight_m": typical_h,
                     "slendernessLimit": 8, "typicalFloorArea_m2": ta, "etabsTotalArea_m2": total_area,
                     "shearWallEtabsArea_m2": wall_area, "note": "*P = Podium, Pr = Provisional floor"},
        "seismic": {"zone": "", "category": "", "frameType": "", "siteClass": "", "codeYear": 2016,
                    "Z_zoneFactor": 0, "I_importanceFactor": 1, "R_responseReductionFactor": 1, "Ah_min": 0,
                    "returnPeriodYears": 0, "seismicWeightEtabs_kN": w_above, "fundamentalPeriod_s": T},
        "seismicForces": eq, "windLoadCases": wind_rows, "modalModes": first3,
        "gravityLoads": {"deadLoad_kN": self_weight, "superDeadLoad_kN": sdl, "liveLoad_kN": live},
        "driftChecks": drift_rows, "massComponents": mass_components, "torsionCheck": torsion,
        "checklist": [
            {"id": "chk-modes", "label": "Clear Modes", "rule": "manual", "manualStatus": sug.get("Clear Modes", "")},
            {"id": "chk-period", "label": "Time period difference is atleast 10%", "rule": "period-difference"},
            {"id": "chk-drift", "label": "Drift check", "rule": "manual", "manualStatus": sug.get("Drift check", "")},
            {"id": "chk-defl", "label": "Deflection", "rule": "manual", "manualStatus": sug.get("Deflection", "")},
            {"id": "chk-torsion", "label": "Torsional Irregularity", "rule": "torsional-irregularity"}],
        "stories": stories_out,
        "config": {"concreteUnitWeight_kNm3": 25, "massIndexDivisor_sqft": 1, "driftLimitRatio": 500,
                   "torsionalIrregularityLimit": 1.2, "periodDifferenceMinPercent": 10},
        "extraction": {"generatedAt": datetime.datetime.now().isoformat(timespec="seconds"),
                       "mapping": mapping.to_dict(), "ground": geom["ground_name"], "typicalStory": typical,
                       "torsionStory": t_story, "passes": {k: v["name"] for k, v in passes.items()},
                       "run": run_info or {}, "warnings": L.warnings, "notes": L.notes, "checks": L.checks,
                       "suggestions": sug, "sources": L.sources, "needsInput": needs, "modesFound": len(modes)},
    }
    if plan_png:
        report["layoutImages"] = {"cadPlan": None, "etabsPlan": plan_png}
    return report


if __name__ == "__main__":
    import comtypes
    import etabs_worker
    comtypes.CoInitialize()
    sm = etabs_worker.attach_by_pid(int(sys.argv[1]))
    out = build_report(sm)
    path = sys.argv[2] if len(sys.argv) > 2 else "report_data.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    print("written", path)
