"""
Stiffness-modifier sets (SLS = "Service", ULS = "Design") applied per object, and the
analysis run, for the design review report.

Why this exists: some report values are reported from the ULS stiffness state and some
from the SLS state (questionnaire Q22). ETABS holds ONE stiffness state at a time, so a
value from the other state needs the modifiers changed and the analysis run again. There
is no shortcut that avoids that; what this module does is make it safe:

  * the user's own model file is never modified: changes are made on a saved working copy;
  * every object's modifiers are snapshotted first and the model's current state is
    compared with the SLS and ULS tables, so a state that is already in place is read from
    the existing results instead of being run again;
  * each set is read back after it is written and must match exactly before the analysis runs;
  * after the run every case that was flagged to run must report "finished".

Modifiers are written on each OBJECT (frame / area), because that is where the models carry them;
the property definitions stay at 1.0 (checked and reported if not).
"""
import datetime
import os
import time

import report_extractor as rx

I22, I33, J = 4, 5, 3
TOL = 1e-6
CLASS_TABLE = {"beam": "beams", "column": "columns", "wall": "walls", "floor": "floors"}
SET_INDEX = {"SLS": 0, "ULS": 1}


# --------------------------------------------------------------------------
# classification and snapshot
# --------------------------------------------------------------------------
def snapshot(SM, log=print):
    """{'frames': {name: (class, modifiers)}, 'areas': {name: (class | None, modifiers)}}.

    Frame class: 'column' if vertical, else 'beam'.  Area class: 'wall' / 'floor' by property type;
    raft (Mat / Footing) and other shells get None and are never touched.
    """
    coords = rx.Coords(SM)
    ptype, _ = rx.area_property_types(SM)
    frames, areas = {}, {}
    for n in SM.FrameObj.GetNameList(0, [])[1]:
        pts = SM.FrameObj.GetPoints(n, "", "")
        a, b = coords(pts[0]), coords(pts[1])
        vertical = abs(a[0] - b[0]) < 1.0 and abs(a[1] - b[1]) < 1.0
        frames[n] = ("column" if vertical else "beam", tuple(SM.FrameObj.GetModifiers(n, [])[0]))
    for n in SM.AreaObj.GetNameList(0, [])[1]:
        kind = ptype.get(SM.AreaObj.GetProperty(n, "")[0], "other")
        cls = {"wall": "wall", "slab": "floor"}.get(kind)
        areas[n] = (cls, tuple(SM.AreaObj.GetModifiers(n, [])[0]))
    log(f"Modifier snapshot: {len(frames)} frames, {len(areas)} areas.")
    return {"frames": frames, "areas": areas}


def property_modifier_problems(SM):
    """Property-level modifiers that are not 1.0 (they would multiply with the object values)."""
    bad = []
    for n in SM.PropFrame.GetNameList(0, [])[1]:
        if any(abs(x - 1.0) > TOL for x in SM.PropFrame.GetModifiers(n, [])[0]):
            bad.append(f"frame property {n}")
    for n in SM.PropArea.GetNameList(0, [])[1]:
        if any(abs(x - 1.0) > TOL for x in SM.PropArea.GetModifiers(n, [])[0]):
            bad.append(f"area property {n}")
    return bad


def target_array(kind, cls, current, mapping, set_name):
    """The modifier array an object should have in the given set (other components untouched)."""
    if cls is None:
        return tuple(current)
    factor = mapping.modifiers[CLASS_TABLE[cls]][SET_INDEX[set_name]]
    v = list(current)
    if kind == "frame":
        v[I22], v[I33] = factor, factor
        if mapping.scale_frame_J:
            v[J] = factor
    elif cls == "wall":
        v[0:6] = [factor] * 6             # f11 f22 f12 m11 m22 m12
    elif cls == "floor":
        v[3:6] = [factor] * 3             # m11 m22 m12 (membrane stays 1.0, as in the models)
    return tuple(v)


def _same(a, b):
    return len(a) == len(b) and all(abs(x - y) <= TOL for x, y in zip(a, b))


def count_mismatches(snap, mapping, set_name, base=None):
    """Objects whose current modifiers differ from the target of the set. `base` = original values."""
    bad = 0
    for kind, key in (("frame", "frames"), ("area", "areas")):
        for name, (cls, cur) in snap[key].items():
            if cls is None:
                continue
            ref = (base or snap)[key][name][1]
            if not _same(cur, target_array(kind, cls, ref, mapping, set_name)):
                bad += 1
    return bad


def detect_state(snap, mapping):
    """'ULS', 'SLS', 'both' (tables identical) or 'other', with the mismatch counts."""
    nu = count_mismatches(snap, mapping, "ULS")
    ns = count_mismatches(snap, mapping, "SLS")
    state = "both" if nu == 0 and ns == 0 else "ULS" if nu == 0 else "SLS" if ns == 0 else "other"
    counts = {}
    for key in ("frames", "areas"):
        for cls, _ in snap[key].values():
            counts[cls or "untouched"] = counts.get(cls or "untouched", 0) + 1
    return state, {"ULS": nu, "SLS": ns, "objects": counts}


# --------------------------------------------------------------------------
# writing a set, working copy, running
# --------------------------------------------------------------------------
def model_file(SM):
    """Path of the model on disk. After a Save As, ETABS reports its temporary '.$et' working file instead
    of the saved '.EDB' (verified), so the extension is normalised."""
    path = SM.GetModelFilename() or ""
    root, ext = os.path.splitext(path)
    return root + ".EDB" if ext.lower() == ".$et" else path


def work_folder(model_path):
    """<model folder>/report_work (created); no nesting when the model already lives there."""
    d = os.path.dirname(model_path)
    if os.path.basename(d).lower() != "report_work":
        d = os.path.join(d, "report_work")
    os.makedirs(d, exist_ok=True)
    return d


def same_model(a, b):
    """True if two model paths name the same file, ignoring the extension (.EDB / .$et) and case."""
    norm = lambda p: os.path.normcase(os.path.abspath(os.path.splitext(p)[0]))
    return bool(a) and bool(b) and norm(a) == norm(b)


def apply_set(SM, base_snap, mapping, set_name, log=print):
    """Write the set on every object and verify it by reading everything back."""
    SM.SetModelIsLocked(False)
    changed = 0
    for kind, key, obj in (("frame", "frames", SM.FrameObj), ("area", "areas", SM.AreaObj)):
        for name, (cls, cur) in base_snap[key].items():
            if cls is None:
                continue
            tgt = target_array(kind, cls, cur, mapping, set_name)
            ret = obj.SetModifiers(name, list(tgt))
            code = ret[-1] if isinstance(ret, (tuple, list)) else ret
            if code != 0:
                raise RuntimeError(f"SetModifiers failed on {kind} {name} (ret={code}).")
            changed += 1
    log(f"{set_name}: modifiers written on {changed} objects; verifying...")
    after = snapshot(SM, lambda *_: None)
    bad = count_mismatches(after, mapping, set_name, base=base_snap)
    if bad:
        raise RuntimeError(f"{set_name} modifier read-back failed on {bad} objects; the analysis was not run.")
    log(f"{set_name}: read-back matches the table on all objects.")
    return changed


def make_working_copy(SM, log=print):
    """Save the model under a new name in <model folder>/report_work and switch to it (the original is untouched)."""
    orig = model_file(SM)
    if not orig:
        raise RuntimeError("The model has never been saved; save it in ETABS first.")
    folder = work_folder(orig)
    stem = os.path.splitext(os.path.basename(orig))[0]
    path = os.path.join(folder, f"{stem}_report_{datetime.datetime.now():%Y%m%d_%H%M%S}.EDB")
    ret = SM.File.Save(path)
    code = ret[-1] if isinstance(ret, (tuple, list)) else ret
    if code != 0:
        raise RuntimeError(f"Could not save the working copy (ret={code}).")
    now = SM.GetModelFilename()
    if not same_model(now, path):
        raise RuntimeError(f"ETABS did not switch to the working copy (still {now}); stopping before any change.")
    if not os.path.isfile(path):
        raise RuntimeError(f"The working copy was not written to disk: {path}")
    log(f"Working copy: {path}")
    return orig, path


def run_analysis(SM, needed_cases, log=print):
    """Run all flagged cases, then require every flagged case to be finished and the model locked."""
    flags = SM.Analyze.GetRunCaseFlag(0, [], [])
    flagged = {n for n, f in zip(flags[1], flags[2]) if f}
    for c in needed_cases:
        if c not in flagged:
            SM.Analyze.SetRunCaseFlag(c, True)
            flagged.add(c)
            log(f"Run flag was off for '{c}'; switched on (working copy only).")
    t0 = time.time()
    log("Running the analysis (all flagged cases); this can take 5 to 15 minutes...")
    ret = SM.Analyze.RunAnalysis()
    if ret != 0:
        raise RuntimeError(f"Analyze.RunAnalysis failed (ret={ret}).")
    secs = time.time() - t0
    status = rx.case_status(SM)
    bad = {c: s for c, s in status.items() if c in flagged and s != 4}
    if bad:
        raise RuntimeError(f"These cases did not finish: {bad}")
    if not SM.GetModelIsLocked():
        raise RuntimeError("The analysis ended but the model is not locked; results are not available.")
    log(f"Analysis finished in {secs / 60:.1f} min; all {len(flagged)} flagged cases finished.")
    return secs
