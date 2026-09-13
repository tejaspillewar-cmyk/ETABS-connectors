"""Recompute pier labels from wall geometry.

The rule, stated as a structural engineer would:

    A pier is a wall footprint on one storey. Walls stacked at the same
    plan position on different storeys are the same pier and share a
    label. Two piers on the same storey never share a label.

Openings fall out of that for free. A wall pierced by a door or window is
several area objects at different heights, but they all sit on the same
plan line, so they land in the same cluster and take the same label. No
special case is needed, which is why the labelling does not break on
cutouts.

Why cluster per storey and then match between storeys, rather than
grouping everything in one pass: one continuous wall on storey 2 can span
two separate walls on storey 1. A single global grouping would join all
three through that overlap and hand the two storey-1 piers the same
label, which is exactly the defect this module exists to remove. Doing it
per storey keeps same-storey uniqueness structurally guaranteed.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict

# Two wall planes closer than this, and parallel, are treated as the same
# plane. Generous enough to absorb storey-to-storey drafting jitter,
# tight enough to keep walls either side of a corridor apart.
PLANE_TOL_MM = 50.0

# Cosine of the angle within which two segments count as parallel (~1 deg).
PARALLEL_COS = math.cos(math.radians(1.0))

# Collinear walls must SHARE at least this much plan length to be one pier.
# Strictly positive on purpose: abutting is not the same as overlapping.
# A long wall split end-to-end into design piers has segments that touch at
# a point and must stay separate, whereas the pieces of a wall pierced by an
# opening sit at different heights over the same plan run and must merge.
# Overlap is what separates those two cases; proximity is not.
OVERLAP_MIN_MM = 1.0

# A cluster must share at least this much plan length with a cluster on the
# storey below to be considered the same stack.
STACK_MIN_OVERLAP_MM = 1.0

WALL_ORIENTATION = 1        # eAreaDesignOrientation: 1 = Wall, 2 = Floor


class ModelLockedError(RuntimeError):
    """Raised when the model cannot be edited because results exist.

    ETABS locks a model once it has been analysed, and a locked model
    rejects every SetPier silently-ish: each call returns 1 and nothing
    changes, so without this check a relabelling run looks like it worked
    and quietly did nothing.
    """


class Wall:
    __slots__ = ("name", "story", "x1", "y1", "x2", "y2", "pier")

    def __init__(self, name, story, x1, y1, x2, y2, pier):
        self.name = name
        self.story = story
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2
        self.pier = pier

    @property
    def length(self):
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)


class Cluster:
    """One pier on one storey: the walls sharing a plan footprint."""

    def __init__(self, story, walls):
        self.story = story
        self.walls = walls
        self.label = None
        xs = [c for w in walls for c in (w.x1, w.x2)]
        ys = [c for w in walls for c in (w.y1, w.y2)]
        # Fit the merged extent along the dominant direction.
        dx, dy = max(xs) - min(xs), max(ys) - min(ys)
        if math.hypot(dx, dy) < 1e-9:
            self.ux, self.uy = 1.0, 0.0
        else:
            longest = max(walls, key=lambda w: w.length)
            L = longest.length or 1.0
            self.ux = (longest.x2 - longest.x1) / L
            self.uy = (longest.y2 - longest.y1) / L
        self.x0, self.y0 = xs[0], ys[0]
        ts = [self._t(x, y) for w in walls for x, y in
              ((w.x1, w.y1), (w.x2, w.y2))]
        self.t_min, self.t_max = min(ts), max(ts)

    def _t(self, x, y):
        return (x - self.x0) * self.ux + (y - self.y0) * self.uy

    def _offset(self, x, y):
        return (x - self.x0) * (-self.uy) + (y - self.y0) * self.ux

    @property
    def extent(self):
        return self.t_max - self.t_min

    def midpoint(self):
        t = 0.5 * (self.t_min + self.t_max)
        return (self.x0 + self.ux * t, self.y0 + self.uy * t)

    def overlap_with(self, other) -> float:
        """Shared plan length with another cluster, 0 if not the same plane."""
        if abs(self.ux * other.ux + self.uy * other.uy) < PARALLEL_COS:
            return 0.0
        if abs(self._offset(other.x0, other.y0)) > PLANE_TOL_MM:
            return 0.0
        a = self._t(other.x0 + other.ux * other.t_min,
                    other.y0 + other.uy * other.t_min)
        b = self._t(other.x0 + other.ux * other.t_max,
                    other.y0 + other.uy * other.t_max)
        lo, hi = min(a, b), max(a, b)
        return max(0.0, min(self.t_max, hi) - max(self.t_min, lo))


# ── Reading the model ────────────────────────────────────────────────────────

def _parse_namelist(ret):
    """GetNameList tuple order varies by build; normalise it."""
    if ret is None or len(ret) < 2:
        return []
    if len(ret) >= 3 and isinstance(ret[0], int) and ret[0] == 0 \
            and isinstance(ret[1], int):
        return list(ret[2]) if ret[2] else []
    if isinstance(ret[0], int) and ret[0] > 0:
        return list(ret[1]) if ret[1] else []
    return []


def load_walls(SapModel, log=lambda m: None) -> list:
    """Every wall area with its storey, plan footprint and current label.

    Uses the bulk GetAllAreas call: one round trip for all geometry instead
    of five per area, which is the difference between under a second and
    several minutes on a large model.
    """
    r = SapModel.AreaObj.GetAllAreas()
    count, names, orient = int(r[0]), list(r[1]), list(r[2])
    delim, px, py = list(r[4]), list(r[6]), list(r[7])
    log(f"  read {count} area objects")

    story_of = {}
    ret = SapModel.Story.GetStories()
    stories = [s for s in (ret[1] if isinstance(ret[1], (list, tuple)) else [])]
    for story in stories:
        for area in _parse_namelist(SapModel.AreaObj.GetNameListOnStory(story)):
            story_of[area] = story
    log(f"  mapped {len(story_of)} areas onto {len(stories)} storeys")

    walls, skipped = [], 0
    for i in range(count):
        if orient[i] != WALL_ORIENTATION:
            continue
        lo = 0 if i == 0 else delim[i - 1] + 1   # delimiter is inclusive
        hi = delim[i] + 1
        xs, ys = px[lo:hi], py[lo:hi]
        if not xs:
            skipped += 1
            continue
        seg = _footprint(xs, ys)
        if seg is None:
            skipped += 1
            continue
        name = names[i]
        try:
            pier = SapModel.AreaObj.GetPier(name)
            pier = pier[0] if isinstance(pier, (list, tuple)) else pier
        except Exception:
            pier = ""
        # An unassigned area comes back as the literal string "None", which
        # is truthy and would otherwise be treated as a real pier label.
        if pier in (None, "None"):
            pier = ""
        walls.append(Wall(name, story_of.get(name, ""), *seg, pier))

    log(f"  {len(walls)} walls usable"
        + (f", {skipped} skipped (degenerate footprint)" if skipped else ""))
    return walls


def _footprint(xs, ys):
    """Collapse a vertical wall's corners to its plan line segment."""
    dx, dy = max(xs) - min(xs), max(ys) - min(ys)
    if math.hypot(dx, dy) < 1e-6:
        return None                      # no plan extent: not a usable wall
    if dx >= dy:
        i_lo, i_hi = xs.index(min(xs)), xs.index(max(xs))
    else:
        i_lo, i_hi = ys.index(min(ys)), ys.index(max(ys))
    return xs[i_lo], ys[i_lo], xs[i_hi], ys[i_hi]


# ── Clustering ───────────────────────────────────────────────────────────────

def _same_pier(a: Wall, b: Wall) -> bool:
    """Do two walls on one storey belong to the same pier footprint?"""
    ax, ay = a.x2 - a.x1, a.y2 - a.y1
    bx, by = b.x2 - b.x1, b.y2 - b.y1
    la, lb = math.hypot(ax, ay), math.hypot(bx, by)
    if la < 1e-9 or lb < 1e-9:
        return False
    ax, ay, bx, by = ax / la, ay / la, bx / lb, by / lb
    if abs(ax * bx + ay * by) < PARALLEL_COS:
        return False                      # not parallel: different plane
    if abs((b.x1 - a.x1) * (-ay) + (b.y1 - a.y1) * ax) > PLANE_TOL_MM:
        return False                      # parallel but offset: e.g. corridor
    ta = sorted([0.0, la])
    tb = sorted([(b.x1 - a.x1) * ax + (b.y1 - a.y1) * ay,
                 (b.x2 - a.x1) * ax + (b.y2 - a.y1) * ay])
    return min(ta[1], tb[1]) - max(ta[0], tb[0]) > OVERLAP_MIN_MM


def cluster_story(walls: list) -> list:
    """Group one storey's walls into piers by shared plan footprint."""
    parent = list(range(len(walls)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    # Bucket by plane so this stays near-linear instead of O(n^2) per storey.
    buckets = defaultdict(list)
    for i, w in enumerate(walls):
        dx, dy = w.x2 - w.x1, w.y2 - w.y1
        L = math.hypot(dx, dy) or 1.0
        ux, uy = dx / L, dy / L
        if (ux, uy) < (0.0, 0.0):
            ux, uy = -ux, -uy
        offset = (-uy) * w.x1 + ux * w.y1
        key = (round(ux, 2), round(uy, 2), round(offset / PLANE_TOL_MM))
        buckets[key].append(i)

    for key, members in buckets.items():
        # Neighbouring offset buckets can hold the same plane.
        ux, uy, band = key
        neigh = [i for d in (-1, 0, 1)
                 for i in buckets.get((ux, uy, band + d), [])]
        for i in members:
            for j in neigh:
                if i < j and _same_pier(walls[i], walls[j]):
                    union(i, j)

    groups = defaultdict(list)
    for i, w in enumerate(walls):
        groups[find(i)].append(w)
    return [Cluster(w[0].story, w) for w in groups.values()]


def build_stacks(walls: list, story_order: list, log=lambda m: None):
    """Cluster each storey, then chain clusters vertically into stacks.

    Returns (clusters_by_story, stacks) where a stack is a list of clusters
    from different storeys sharing a plan position.
    """
    by_story = defaultdict(list)
    for w in walls:
        by_story[w.story].append(w)

    clusters_by_story = {}
    for story in story_order:
        if by_story.get(story):
            clusters_by_story[story] = cluster_story(by_story[story])

    stacks = []
    previous = []
    for story in story_order:
        current = clusters_by_story.get(story)
        if not current:
            continue
        claimed = set()
        for cl in sorted(current, key=lambda c: -c.extent):
            best, best_ov = None, STACK_MIN_OVERLAP_MM
            for k, prev in enumerate(previous):
                if k in claimed:
                    continue           # one stack takes at most one cluster
                ov = cl.overlap_with(prev)
                if ov > best_ov:
                    best, best_ov = k, ov
            if best is None:
                stacks.append([cl])
                cl.stack = len(stacks) - 1
            else:
                claimed.add(best)
                stacks[previous[best].stack].append(cl)
                cl.stack = previous[best].stack
        previous = current
    log(f"  {sum(len(v) for v in clusters_by_story.values())} piers "
        f"across {len(clusters_by_story)} storeys -> {len(stacks)} stacks")
    return clusters_by_story, stacks


# ── Naming ───────────────────────────────────────────────────────────────────

def _sort_key(stack):
    """Order stacks by plan position so numbering is stable between runs."""
    x, y = stack[0].midpoint()
    return (round(y, 1), round(x, 1))


def assign_labels(clusters_by_story, stacks, mode="all", prefix="P"):
    """Map area name -> new pier label.

    mode="all"   renumber every stack P1..Pn by plan position.
    mode="fix"   keep a stack's existing label when that label is already
                 used by this stack and nothing else; rename only the
                 stacks that clash.
    """
    order = sorted(range(len(stacks)), key=lambda i: _sort_key(stacks[i]))

    names = {}
    if mode == "all":
        for n, i in enumerate(order, start=1):
            names[i] = f"{prefix}{n}"
    else:
        # A stack may keep its label only if every cluster in it already
        # carries that one label and no other stack does.
        wanted = {}
        for i, stack in enumerate(stacks):
            labels = {w.pier for cl in stack for w in cl.walls if w.pier}
            wanted[i] = next(iter(labels)) if len(labels) == 1 else None
        taken = defaultdict(list)
        for i, lab in wanted.items():
            if lab:
                taken[lab].append(i)
        used = set()
        for lab, owners in taken.items():
            if len(owners) == 1:
                names[owners[0]] = lab
                used.add(lab)
        nxt = 1
        for i in order:
            if i in names:
                continue
            while f"{prefix}{nxt}" in used:
                nxt += 1
            names[i] = f"{prefix}{nxt}"
            used.add(f"{prefix}{nxt}")

    mapping = {}
    for i, stack in enumerate(stacks):
        for cl in stack:
            cl.label = names[i]
            for w in cl.walls:
                mapping[w.name] = names[i]
    return mapping


# ── Reporting and writing ────────────────────────────────────────────────────

def diagnose(clusters_by_story):
    """Existing labels that cover more than one pier on a single storey.

    This is the defect the relabelling fixes: one label spanning walls that
    are not the same pier, which makes ETABS report the pair as a single
    very long, very thin equivalent section.
    """
    problems = []
    for story, clusters in clusters_by_story.items():
        owners = defaultdict(list)
        for cl in clusters:
            for lab in {w.pier for w in cl.walls if w.pier}:
                owners[lab].append(cl)
        for lab, cls in owners.items():
            if len(cls) > 1:
                problems.append((story, lab, cls))
    return problems


def preview(walls, mapping, clusters_by_story, stacks, log=print):
    changed = [w for w in walls if w.pier != mapping.get(w.name)]
    before = {w.pier for w in walls if w.pier}
    after = set(mapping.values())
    problems = diagnose(clusters_by_story)

    log("")
    log("  DRY RUN -- nothing written to the model")
    log(f"  wall areas              : {len(walls)}")
    log(f"  piers (storey clusters) : "
        f"{sum(len(v) for v in clusters_by_story.values())}")
    log(f"  vertical stacks         : {len(stacks)}")
    log(f"  labels before / after   : {len(before)} / {len(after)}")
    log(f"  areas whose label changes: {len(changed)}")
    log("")
    if problems:
        log(f"  labels covering >1 pier on a storey: {len(problems)}")
        for story, lab, cls in problems[:10]:
            spans = ", ".join(f"{c.extent:,.0f}mm @ ({c.midpoint()[0]:,.0f},"
                              f"{c.midpoint()[1]:,.0f})" for c in cls[:3])
            log(f"    {lab:<6} @ {story[:28]:<30} {len(cls)} piers: {spans}")
        if len(problems) > 10:
            log(f"    ... and {len(problems)-10} more")
    else:
        log("  no label covers more than one pier on any storey")
    return {"changed": changed, "problems": problems,
            "before": before, "after": after}


def frames_with_piers(SapModel) -> list:
    """Frames carrying a pier label.

    Columns can be assigned to a pier as well as walls. This module only
    relabels area objects, so any such frame would keep its old label and
    could end up sharing a name with an unrelated wall. Report them rather
    than quietly leaving that behind.
    """
    found = []
    for name in _parse_namelist(SapModel.FrameObj.GetNameList(0, [])):
        try:
            pier = SapModel.FrameObj.GetPier(name)
            pier = pier[0] if isinstance(pier, (list, tuple)) else pier
        except Exception:
            continue
        if pier and pier != "None":
            found.append((name, pier))
    return found


def cleanup_labels(SapModel, keep, log=lambda m: None):
    """Delete pier labels nothing references any more.

    Renumbering can strand the model's previous labels. Only labels absent
    from the new mapping and unused by any frame are removed.
    """
    frame_labels = {p for _, p in frames_with_piers(SapModel)}
    existing = set(_parse_namelist(SapModel.PierLabel.GetNameList(0, [])))
    removed = 0
    for label in sorted(existing - set(keep) - frame_labels):
        try:
            if SapModel.PierLabel.Delete(label) in (0, None):
                removed += 1
        except Exception:
            pass
    if removed:
        log(f"  removed {removed} pier labels left unused")
    return removed


def is_locked(SapModel) -> bool:
    try:
        return bool(SapModel.GetModelIsLocked())
    except Exception:
        return False


def apply(SapModel, mapping, log=lambda m: None):
    """Write the new labels. Does NOT save the model.

    Refuses outright on a locked model. ETABS returns 1 from every SetPier
    in that state rather than raising, so a run would otherwise report
    thousands of successes and change nothing.
    """
    if is_locked(SapModel):
        raise ModelLockedError(
            "The model is locked because it has analysis results, so pier "
            "labels cannot be changed.\n\n"
            "Unlocking discards the analysis results and the model has to be "
            "re-run, which on a large model can take hours.\n\n"
            "The usual order is: relabel the piers first, then run the "
            "analysis, then the FDR.")

    # A pier label must exist before an area can be assigned to it.
    # Assigning to an undefined label returns 1 and silently does nothing,
    # which is what made the first run look half-successful.
    existing = set(_parse_namelist(SapModel.PierLabel.GetNameList(0, [])))
    wanted = set(mapping.values())
    created = 0
    for label in sorted(wanted - existing):
        try:
            if SapModel.PierLabel.SetPier(label) in (0, None):
                created += 1
        except Exception:
            pass
    if created:
        log(f"  defined {created} new pier labels")

    ok, failed = 0, []
    for area, label in mapping.items():
        try:
            ret = SapModel.AreaObj.SetPier(area, label)
            if ret in (0, None):
                ok += 1
            else:
                failed.append((area, label, ret))
        except Exception as exc:
            failed.append((area, label, str(exc)))
    log(f"  set {ok} labels, {len(failed)} failed")
    if not failed:
        cleanup_labels(SapModel, wanted, log)
    return ok, failed


def story_order(SapModel):
    """Storey names from the bottom up."""
    ret = SapModel.Story.GetStories()
    names = list(ret[1]) if isinstance(ret[1], (list, tuple)) else []
    elevs = None
    for field in ret:
        if isinstance(field, (list, tuple)) and len(field) == len(names) \
                and field is not names and all(
                    isinstance(v, float) for v in field):
            elevs = list(field)
            break
    if elevs:
        return [n for _, n in sorted(zip(elevs, names))]
    return list(reversed(names))


def relabel(SapModel, mode="all", write=False, log=print):
    """Full pass. Returns the preview summary plus the mapping."""
    log("  reading model geometry...")
    walls = load_walls(SapModel, log)
    order = story_order(SapModel)
    clusters_by_story, stacks = build_stacks(walls, order, log)
    mapping = assign_labels(clusters_by_story, stacks, mode=mode)
    summary = preview(walls, mapping, clusters_by_story, stacks, log)
    summary["mapping"] = mapping

    summary["locked"] = is_locked(SapModel)
    if summary["locked"]:
        log("")
        log("  NOTE: the model is locked (it has analysis results), so labels "
            "cannot be written until it is unlocked -- which discards those "
            "results.", )
    frames = frames_with_piers(SapModel)
    summary["frames"] = frames
    if frames:
        log(f"  WARNING: {len(frames)} frame(s) also carry pier labels and are "
            f"not relabelled, e.g. {frames[:3]}")
    if write:
        log("")
        log("  writing labels...")
        summary["written"] = apply(SapModel, mapping, log)
    return summary
