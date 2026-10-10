"""
Design review report: the whole job, in two phases so the GUI can ask before anything slow or
irreversible happens.

  prepare()  read-only: checks the mapping and the model, reads the plan geometry, snapshots every
             object's stiffness modifiers, works out which stiffness state the model is in and
             which analysis runs (if any) are really needed. Returns a Prepared with a plain-language plan.
  execute()  reads the results that are already valid, then (only if needed) saves a working copy,
             writes the missing modifier set, runs the analysis, reads that pass, and assembles the report.

The user's own model file is never modified. See docs/REPORT_EXTRACTION_EXPLAINED.md.
"""
import datetime
import json
import os
import time
from dataclasses import dataclass, field

import report_extractor as rx
import report_modifiers as rm
import report_plan_image as rpi


@dataclass
class Prepared:
    mode: str = "full"
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    plan: list = field(default_factory=list)       # plain-language lines for the confirmation dialog
    state: str = ""
    counts: dict = field(default_factory=dict)
    read_now: list = field(default_factory=list)   # stiffness sets readable from the existing results
    to_run: list = field(default_factory=list)     # sets that need modifiers written and a run
    geom: dict = None
    snap: dict = None
    ledger: object = None
    model_path: str = ""
    have_results: bool = False
    unfinished: list = field(default_factory=list)


def prepare(SM, mapping, mode="full", log=print):
    P = Prepared(mode=mode, ledger=rx.Ledger())
    P.model_path = rm.model_file(SM)
    all_cases = rx.case_names(SM)
    P.errors += mapping.problems(all_cases)
    if not P.model_path:
        P.errors.append("The model has never been saved. Save it in ETABS first.")
    needed = mapping.all_cases() + [c for c in (mapping.modal_case, mapping.dead_case, mapping.live_case) if c]
    status = rx.case_status(SM)
    unfinished = [c for c in needed if c in status and status[c] != 4]
    have_results = bool(SM.GetModelIsLocked()) and not unfinished
    P.have_results, P.unfinished = have_results, unfinished
    log(f"Model locked: {bool(SM.GetModelIsLocked())}; mapped cases not finished: {unfinished or 'none'}")
    if mode == "quick" and not have_results:
        if not SM.GetModelIsLocked():
            P.errors.append("The model has no analysis results (it is unlocked). Run the analysis in ETABS first.")
        else:
            P.errors.append("These mapped cases have no finished results: " + ", ".join(unfinished) +
                            ". Run them in ETABS, or remove them from the case mapping.")
    if P.errors:
        return P

    with rx.kn_mm_units(SM):
        P.geom = rx.read_geometry(SM, mapping, log)

    if mode == "quick":
        P.plan = ["Read-only. Reads the results as they are now. Nothing is changed or run.",
                  "Values are NOT separated into SLS and ULS."]
        return P

    P.snap = rm.snapshot(SM, log)
    P.state, P.counts = rm.detect_state(P.snap, mapping)
    bad_props = rm.property_modifier_problems(SM)
    if bad_props:
        P.warnings.append("Property-level modifiers are not 1.0 on: " + ", ".join(bad_props[:8]) +
                          ". They multiply with the object modifiers; check the SLS / ULS results.")
    if have_results and P.state in ("ULS", "SLS"):
        P.read_now = [P.state]
        P.to_run = [s for s in ("ULS", "SLS") if s != P.state]
    elif have_results and P.state == "both":
        P.read_now = ["ULS", "SLS"]
    else:
        P.to_run = ["SLS", "ULS"]

    n_obj = sum(P.counts["objects"].values())
    P.plan.append(f"Your model: {n_obj} frames and areas; its stiffness modifiers match the "
                  f"{'ULS table' if P.state == 'ULS' else 'SLS table' if P.state == 'SLS' else 'ULS and SLS tables (they are identical)' if P.state == 'both' else 'neither table'}"
                  f"{'' if have_results else ' and it has no finished analysis'}.")
    for s in P.read_now:
        P.plan.append(f"{s} results: read from the analysis you already have (no run needed).")
    if P.to_run:
        P.plan.append("A working copy of the model is saved in the 'report_work' folder next to it, and ETABS switches to the copy. "
                      "Your original file is not modified.")
        for s in P.to_run:
            P.plan.append(f"{s}: the {s} modifiers are written on every object, then the full analysis is run "
                          f"(typically 5 to 15 minutes), then the {s} results are read.")
    else:
        P.plan.append("No analysis run is needed.")
    return P


def _roles(set_name):
    return [r for r in rx.ROLE_KEYS if rx.ROLE_PASS[r] == set_name]


def execute(SM, P, mapping, log=print):
    started = time.time()
    info = {"mode": P.mode, "originalFile": P.model_path, "stateAtStart": P.state, "modifierObjects": P.counts,
            "runs": [], "startedAt": datetime.datetime.now().isoformat(timespec="seconds")}
    L = P.ledger
    for w in P.warnings:
        L.warnings.append(w)

    if P.mode == "quick":
        report = rx.build_report(SM, mapping, passes=None, geom=P.geom, ledger=L, run_info=info, log=log)
    else:
        passes = {}
        needed = [c for c in (mapping.modal_case, mapping.dead_case, mapping.live_case) if c]
        with rx.kn_mm_units(SM):
            if set(P.read_now) == {"ULS", "SLS"}:       # identical tables: one read serves both
                log("Reading results from the existing analysis (ULS and SLS tables are identical)...")
                one = rx.read_pass(SM, mapping, P.geom, rx.ROLE_KEYS, "ULS=SLS", log)
                passes = {"ULS": one, "SLS": one}
            else:
                for s in P.read_now:
                    log(f"Reading {s} results from the existing analysis...")
                    passes[s] = rx.read_pass(SM, mapping, P.geom, _roles(s), s, log)
        if P.to_run:
            orig, copy = rm.make_working_copy(SM, log)
            info["workingCopy"] = copy
            try:
                for s in P.to_run:
                    rm.apply_set(SM, P.snap, mapping, s, log)
                    secs = rm.run_analysis(SM, mapping.all_cases() + needed, log)
                    info["runs"].append({"set": s, "minutes": round(secs / 60, 2)})
                    with rx.kn_mm_units(SM):
                        passes[s] = rx.read_pass(SM, mapping, P.geom, _roles(s), s, log)
            except Exception as exc:
                raise RuntimeError(f"{exc}\n\nETABS is now on the working copy {copy}, in an unfinished state. "
                                   f"Your original file {orig} was not modified; reopen it in ETABS.") from exc
            L.notes.append(f"ETABS is now on the working copy {copy} (state: {P.to_run[-1]}). The original file was not changed.")
        info["tablesApplied"] = mapping.modifiers
        info["frameJScaled"] = mapping.scale_frame_J
        log("Assembling the report...")
        report = rx.build_report(SM, mapping, passes=passes, geom=P.geom, ledger=L, run_info=info, log=log)

    try:
        log("Drawing the plan with the corner points...")
        img = rpi.render_plan(SM, P.geom, report["extraction"]["torsionStory"])
        if img:
            report["layoutImages"] = {"cadPlan": None, "etabsPlan": img}
    except Exception as exc:                       # the image is a convenience; never lose the report over it
        report["extraction"]["warnings"].append(f"[plan_image] The plan image could not be drawn: {exc}")
    info["finishedAt"] = datetime.datetime.now().isoformat(timespec="seconds")
    info["totalMinutes"] = round((time.time() - started) / 60, 2)
    return report


def output_path(model_path, suffix="report_data.json"):
    folder = rm.work_folder(model_path)
    stem = os.path.splitext(os.path.basename(model_path))[0]
    return os.path.join(folder, f"{stem}_{datetime.datetime.now():%Y%m%d_%H%M%S}_{suffix}")


def save_report(report, path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    return path
