"""
Post-extraction, pre-drawing connectivity pass.

Extraction measures every wall/beam/slab in isolation, so elements that
should share a structural node in ETABS often don't actually touch:
  - Two walls meeting at a bent/L/T corner (whether drawn as two separate
    polygons, or decomposed from one bent polygon by wall_slab_extractor.py)
    have independently-measured centerlines that stop short of, or overlap,
    the true corner.
  - A beam ending at a shear wall stops wherever the DXF geometry happened
    to end, not necessarily exactly on the wall's centerline.
  - A slab is drawn to its raw DXF boundary, usually the clear span between
    beam/wall faces, not the beam/wall centerline -- leaving a gap.

snap_connections() fixes all three by snapping wall/beam endpoints together
at shared corners and T-junctions, and expanding slab edges out to the
centerline of the beams/walls that bound them. It returns a NEW dict (via
dataclasses.replace()) -- extraction stays a pure step, and draw_model()
and the UI preview both consume this pass's output unchanged.

Since BeamEntry and WallEntry now carry explicit p1/p2 endpoints, this pass
reads and writes them directly -- no lossy centroid+angle reconstruction.
"""
import math
from dataclasses import replace

from .geometry import (
    distance, line_angle, line_intersection, closest_point_on_segment,
    polygon_area_sqmm, sqmm_to_sqm, polygon_centroid,
)

NODE_SNAP_MIN_MM = 150
NODE_SNAP_MAX_MM = 750
NODE_SNAP_FACTOR = 1.5  # snap tolerance = clamp(factor * max(thickness_a, thickness_b), MIN, MAX)

SLAB_EXPAND_MAX_GAP_MM = 600
SLAB_EXPAND_ANGLE_TOL_DEG = 5.0


class _ConnResult:
    def __init__(self):
        self.corners_snapped = 0     # wall-wall, wall-beam, or beam-beam endpoint clusters merged
        self.t_junctions = 0         # endpoints snapped onto another member's span
        self.slab_edges_expanded = 0


# ── shared line/endpoint helpers ─────────────────────────────────────────

def _rebuild(p1, p2):
    """(centroid, angle_deg, length_m) from two endpoints."""
    cx, cy = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
    return (cx, cy), line_angle(p1, p2), distance(p1, p2) / 1000.0


def _snap_tolerance(width_a, width_b):
    return max(NODE_SNAP_MIN_MM, min(NODE_SNAP_MAX_MM, NODE_SNAP_FACTOR * max(width_a, width_b)))


class _UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


# ── wall/beam endpoint snapping ──────────────────────────────────────────

def _snap_members(members, result):
    """
    members: list of dicts {"p1", "p2", "half_width"}, mutated in place
    (p1/p2 replaced with snapped positions). Endpoint slots are indexed
    0..2n-1 (2*i = p1 of member i, 2*i+1 = p2 of member i).
    """
    n = len(members)
    if n < 2:
        return

    slots = []  # (member_index, which, point, half_width)
    for i, m in enumerate(members):
        slots.append((i, 0, m["p1"], m["half_width"]))
        slots.append((i, 1, m["p2"], m["half_width"]))

    uf = _UnionFind(len(slots))
    for a in range(len(slots)):
        ia, _, pa, wa = slots[a]
        for b in range(a + 1, len(slots)):
            ib, _, pb, wb = slots[b]
            if ia == ib:
                continue
            tol = _snap_tolerance(wa, wb)
            if distance(pa, pb) <= tol:
                uf.union(a, b)

    clusters = {}
    for idx in range(len(slots)):
        clusters.setdefault(uf.find(idx), []).append(idx)

    matched_slots = set()
    for root, idxs in clusters.items():
        if len(idxs) < 2:
            continue
        matched_slots.update(idxs)
        target = _cluster_target(idxs, slots, members)
        for idx in idxs:
            mi, which, _, _ = slots[idx]
            if which == 0:
                members[mi]["p1"] = target
            else:
                members[mi]["p2"] = target
        result.corners_snapped += 1

    # T-junctions: any endpoint not merged with another endpoint -- check
    # proximity to another member's SPAN (not just its endpoints).
    for idx in range(len(slots)):
        if idx in matched_slots:
            continue
        mi, which, pt, w = slots[idx]
        pt = members[mi]["p1"] if which == 0 else members[mi]["p2"]  # possibly updated above
        best = None
        for j, m in enumerate(members):
            if j == mi:
                continue
            proj, dist = closest_point_on_segment(pt, m["p1"], m["p2"])
            # Skip if the projection lands essentially at m's own endpoint --
            # that's an endpoint-to-endpoint case, already handled above.
            near_end = distance(proj, m["p1"]) < 1.0 or distance(proj, m["p2"]) < 1.0
            if near_end:
                continue
            tol = _snap_tolerance(w, m["half_width"])
            if dist <= tol and (best is None or dist < best[1]):
                best = (proj, dist)
        if best is not None:
            if which == 0:
                members[mi]["p1"] = best[0]
            else:
                members[mi]["p2"] = best[0]
            result.t_junctions += 1


def _cluster_target(idxs, slots, members):
    points = [slots[i][2] for i in idxs]
    avg = (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))

    # Prefer the intersection of two non-parallel member lines in the
    # cluster (the true corner point); fall back to the average position.
    # Lines that are only *nearly* parallel (a common case at shallow-angle
    # junctions) produce a mathematically valid but physically absurd
    # intersection point far away -- reject any candidate too far from the
    # cluster's own points rather than snapping a member kilometers off.
    for a in range(len(idxs)):
        ma = members[slots[idxs[a]][0]]
        for b in range(a + 1, len(idxs)):
            mb = members[slots[idxs[b]][0]]
            if slots[idxs[a]][0] == slots[idxs[b]][0]:
                continue
            pt = line_intersection(ma["p1"], ma["p2"], mb["p1"], mb["p2"])
            if pt is not None and distance(pt, avg) <= NODE_SNAP_MAX_MM * 2:
                return pt
    return avg


# ── slab edge expansion ──────────────────────────────────────────────────

def _polygon_signed_area(vertices):
    n = len(vertices)
    area = 0.0
    for i in range(n):
        x1, y1 = vertices[i]
        x2, y2 = vertices[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return area / 2.0


def _are_parallel(d1, d2, tol_deg):
    a1 = math.degrees(math.atan2(d1[1], d1[0])) % 180.0
    a2 = math.degrees(math.atan2(d2[1], d2[0])) % 180.0
    diff = abs(a1 - a2)
    return diff < tol_deg or abs(diff - 180.0) < tol_deg


def _expand_slab(vertices, members, result):
    n = len(vertices)
    if n < 3:
        return vertices

    signed_area = _polygon_signed_area(vertices)
    ccw = signed_area > 0

    target_lines = [None] * n  # per edge i: (p1, p2) of the matched member's centerline, or None
    for i in range(n):
        a, b = vertices[i], vertices[(i + 1) % n]
        edge_dir = (b[0] - a[0], b[1] - a[1])
        edge_len = math.hypot(*edge_dir)
        if edge_len == 0:
            continue
        normal = (edge_dir[1] / edge_len, -edge_dir[0] / edge_len) if ccw else \
                 (-edge_dir[1] / edge_len, edge_dir[0] / edge_len)
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)

        best = None
        for m in members:
            mdir = (m["p2"][0] - m["p1"][0], m["p2"][1] - m["p1"][1])
            if not _are_parallel(edge_dir, mdir, SLAB_EXPAND_ANGLE_TOL_DEG):
                continue
            proj, dist_to_line = closest_point_on_segment(mid, m["p1"], m["p2"])
            gap = (proj[0] - mid[0]) * normal[0] + (proj[1] - mid[1]) * normal[1]
            if gap <= 0:
                continue  # member is inward, not an outward support -- ignore
            perp_gap = abs((mid[0] - m["p1"][0]) * normal[0] + (mid[1] - m["p1"][1]) * normal[1])
            if perp_gap > SLAB_EXPAND_MAX_GAP_MM:
                continue
            if best is None or perp_gap < best[1]:
                best = ((m["p1"], m["p2"]), perp_gap)
        if best is not None:
            target_lines[i] = best[0]

    if not any(target_lines):
        return vertices

    new_vertices = list(vertices)
    expanded_edges = 0
    # A corner's two adjacent edges being only nearly parallel (shallow
    # bends) produces the same near-singular-intersection blowup as the
    # wall/beam corner case -- cap how far a vertex may move and fall back
    # to leaving it where it was, rather than corrupting the slab shape.
    max_vertex_move = SLAB_EXPAND_MAX_GAP_MM * 3
    for i in range(n):
        prev_edge = (i - 1) % n
        line_before = target_lines[prev_edge] or (vertices[prev_edge], vertices[i])
        line_after = target_lines[i] or (vertices[i], vertices[(i + 1) % n])
        if target_lines[prev_edge] is None and target_lines[i] is None:
            continue
        pt = line_intersection(line_before[0], line_before[1], line_after[0], line_after[1])
        if pt is not None and distance(pt, vertices[i]) <= max_vertex_move:
            new_vertices[i] = pt

    # Expansion should only ever grow a slab toward surrounding member
    # centerlines. On an unusual polygon (many short zigzag "step" edges
    # near the same member, for instance) the independent per-edge target
    # lines can occasionally intersect back inward instead. That's a sign
    # the per-edge matching got confused for this specific polygon, not a
    # legitimate result -- reject it and keep the original boundary rather
    # than risk drawing a corrupted slab.
    if polygon_area_sqmm(new_vertices) < polygon_area_sqmm(vertices) - 1.0:
        return vertices

    for t in target_lines:
        if t is not None:
            expanded_edges += 1
    result.slab_edges_expanded += expanded_edges

    return new_vertices


# ── entry point ───────────────────────────────────────────────────────────

def snap_connections(parsed: dict, log=lambda m: None) -> dict:
    walls = list(parsed.get("walls", []))
    beams = list(parsed.get("beams", []))
    slabs = list(parsed.get("slabs", []))
    columns = list(parsed.get("columns", []))

    result = _ConnResult()

    eligible_walls = [w for w in walls if not w.warnings and w.thickness_mm > 0 and w.length_m > 0]
    eligible_beams = [b for b in beams if not b.warnings and b.width_mm > 0 and b.length_m > 0]

    # Build member list using stored p1/p2 directly (no lossy reconstruction
    # from centroid+angle+length).
    members = []
    for w in eligible_walls:
        members.append({"p1": w.p1, "p2": w.p2, "half_width": w.thickness_mm / 2.0, "kind": "wall", "entry": w})
    for b in eligible_beams:
        members.append({"p1": b.p1, "p2": b.p2, "half_width": b.width_mm / 2.0, "kind": "beam", "entry": b})

    _snap_members(members, result)

    # A very short original member can have both ends snap into (near-)
    # coincident junctions, degenerating to ~zero length -- flag it clearly
    # instead of letting it fall through to draw_model()'s generic
    # "unresolved dimensions" skip reason.
    MIN_SNAPPED_LENGTH_M = 0.05

    new_walls = []
    new_beams = []
    member_by_entry = {id(m["entry"]): m for m in members}
    for w in walls:
        m = member_by_entry.get(id(w))
        if m is None:
            new_walls.append(w)
            continue
        centroid, angle_deg, length_m = _rebuild(m["p1"], m["p2"])
        warnings = list(w.warnings)
        if length_m < MIN_SNAPPED_LENGTH_M:
            warnings.append(
                f"Wall '{w.name}' collapsed to ~0 length after connecting both ends to nearby "
                f"junctions -- likely a very short stub; not drawn."
            )
        new_walls.append(replace(w,
                                  p1=m["p1"], p2=m["p2"],
                                  centroid=(round(centroid[0], 1), round(centroid[1], 1)),
                                  angle_deg=round(angle_deg, 2), length_m=round(length_m, 3), warnings=warnings))
    for b in beams:
        m = member_by_entry.get(id(b))
        if m is None:
            new_beams.append(b)
            continue
        centroid, angle_deg, length_m = _rebuild(m["p1"], m["p2"])
        warnings = list(b.warnings)
        if length_m < MIN_SNAPPED_LENGTH_M:
            warnings.append(
                f"Beam '{b.name}' collapsed to ~0 length after connecting both ends to nearby "
                f"junctions -- likely a very short stub; not drawn."
            )
        new_beams.append(replace(b,
                                  p1=m["p1"], p2=m["p2"],
                                  centroid=(round(centroid[0], 1), round(centroid[1], 1)),
                                  angle_deg=round(angle_deg, 2), length_m=round(length_m, 3), warnings=warnings))

    new_slabs = []
    for s in slabs:
        if s.warnings or not s.vertices:
            new_slabs.append(s)
            continue
        new_vertices = _expand_slab(s.vertices, members, result)
        if new_vertices is s.vertices:
            new_slabs.append(s)
            continue
        area_sqm = sqmm_to_sqm(polygon_area_sqmm(new_vertices))
        cx, cy = polygon_centroid(new_vertices)
        volume_cum = area_sqm * (s.thickness_mm / 1000.0)
        new_slabs.append(replace(s, vertices=new_vertices, area_sqm=round(area_sqm, 3),
                                  volume_cum=round(volume_cum, 4), centroid=(round(cx, 1), round(cy, 1))))

    log(f"Connectivity: {result.corners_snapped} corner(s) snapped, "
        f"{result.t_junctions} T-junction(s) connected, "
        f"{result.slab_edges_expanded} slab edge(s) expanded to member centerlines.")

    return {"slabs": new_slabs, "walls": new_walls, "beams": new_beams, "columns": columns}
