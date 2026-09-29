"""
Post-extraction, pre-drawing connectivity pass.

Extraction measures every wall/beam/slab/column in isolation, so elements that
should share a structural node in ETABS often don't actually touch:
  - Two walls meeting at a bent/L/T corner have independently-measured
    centerlines that stop short of, or overlap, the true corner.
  - A beam ending at a shear wall stops wherever the DXF geometry happened
    to end, not necessarily exactly on the wall's centerline.
  - A beam ending at a column stops at the column face, not its centerline.
  - A slab is drawn to its raw DXF boundary, usually the clear span between
    beam/wall faces, not the beam/wall centerline -- leaving a gap.

snap_connections() fixes all three by:
  1. Building a structural graph: columns first (defining grid nodes), then
     walls/beams snapping endpoints to column nodes and to each other.
  2. Snapping wall/beam endpoints together at shared corners and T-junctions
     (WITHOUT changing member directions - only extending/shortening along
     their original axis)
  3. Expanding slab edges OUTWARD along their original edge normals to the
     centerline of the bounding beams/walls (keeping each edge parallel to
     its original direction)

Returns a NEW dict (via dataclasses.replace()) -- extraction stays a pure step.
"""
import math
from dataclasses import replace

from .geometry import (
    distance, line_angle, line_intersection, closest_point_on_segment, project_point_on_line,
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
        self.column_snaps = 0        # beam/wall endpoints snapped to column centroids
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


# ── Structural Graph: columns define nodes, members snap to them ───────────

class _StructuralGraph:
    """
    Nodes = column centroids + wall/beam endpoints (after snapping).
    Edges = wall/beam centerlines between nodes.
    
    Columns are added FIRST as fixed nodes. Then walls/beams snap their
    endpoints to the nearest column node (if within tolerance) or to each other.
    """
    
    def __init__(self, snap_tolerance_min=150, snap_tolerance_max=750, snap_factor=1.5):
        self.nodes = []           # list of (x, y, node_id, connected_elements_set)
        self.edges = []           # list of (node_id_1, node_id_2, element_type, element_ref)
        self._next_node_id = 0
        self.snap_tolerance_min = snap_tolerance_min
        self.snap_tolerance_max = snap_tolerance_max
        self.snap_factor = snap_factor
        self.column_node_ids = set()  # node_ids that came from columns (fixed)
    
    def _snap_tolerance(self, width_a, width_b):
        return max(self.snap_tolerance_min, min(self.snap_tolerance_max, self.snap_factor * max(width_a, width_b)))
    
    def add_column(self, col, col_id):
        """Add column centroid as a fixed structural node."""
        cx, cy = col.centroid
        # Check if very close to existing column node (duplicate column)
        for node in self.nodes:
            nx, ny, nid, _ = node
            if distance((cx, cy), (nx, ny)) < 50.0:  # 50mm = essentially same point
                return nid
        nid = self._next_node_id
        self._next_node_id += 1
        self.nodes.append([cx, cy, nid, {("column", col_id)}])
        self.column_node_ids.add(nid)
        return nid
    
    def get_node_position(self, node_id):
        for node in self.nodes:
            if node[2] == node_id:
                return (node[0], node[1])
        return None
    
    def find_nearest_node(self, pt, exclude_column_nodes=False, max_dist=None):
        """Find nearest node to pt within max_dist."""
        best_node = None
        best_dist = max_dist if max_dist is not None else float('inf')
        for node in self.nodes:
            if exclude_column_nodes and node[2] in self.column_node_ids:
                continue
            nx, ny, nid, _ = node
            d = distance(pt, (nx, ny))
            if d < best_dist:
                best_dist = d
                best_node = node
        return best_node, best_dist
    
    def snap_point_to_graph(self, pt, element_type, element_ref, element_width, element_id):
        """
        Snap a point to the structural graph:
        1. First try column nodes (highest priority - they define the grid)
        2. Then try other member endpoints
        3. If no snap, create new node
        Returns (node_id, snapped_point)
        """
        # 1. Try column nodes first
        for node in self.nodes:
            if node[2] not in self.column_node_ids:
                continue
            nx, ny, nid, _ = node
            d = distance(pt, (nx, ny))
            tol = self._snap_tolerance(element_width, 300)  # column width ~300mm typical
            if d <= tol:
                node[3].add((element_type, element_id))
                return nid, (nx, ny)
        
        # 2. Try other nodes (member endpoints)
        best_node, best_dist = self.find_nearest_node(pt, max_dist=self._snap_tolerance(element_width, element_width))
        if best_node:
            nid = best_node[2]
            snapped = (best_node[0], best_node[1])
            best_node[3].add((element_type, element_id))
            return nid, snapped
        
        # 3. Create new node
        nid = self._next_node_id
        self._next_node_id += 1
        self.nodes.append([pt[0], pt[1], nid, {(element_type, element_id)}])
        return nid, pt
    
    def add_member(self, element_type, element_ref, p1, p2, half_width, angle_deg, element_id):
        """Add a wall/beam member, snapping its endpoints to the graph."""
        n1, snapped_p1 = self.snap_point_to_graph(p1, element_type, element_ref, half_width, element_id)
        n2, snapped_p2 = self.snap_point_to_graph(p2, element_type, element_ref, half_width, element_id)
        
        # If both endpoints snapped to same node, this member is degenerate
        if n1 == n2:
            return n1, n2, snapped_p1, snapped_p2, True
        
        self.edges.append((n1, n2, element_type, element_ref))
        return n1, n2, snapped_p1, snapped_p2, False


def _snap_members_to_graph(members, graph, result):
    """
    Second pass: snap member endpoints to each other (corners and T-junctions)
    for endpoints that didn't snap to columns.
    
    CRITICAL: Snapping only moves endpoints ALONG the member's original axis.
    Direction (angle) is NEVER changed.
    
    T-JUNCTION LOGIC:
    - If member A's endpoint projects to member B's MID-SPAN (t in [0.15, 0.85])
      and member B's endpoints do NOT project to A's mid-span:
      -> A is the STEM of a T, B is the TOP
      -> EXTEND A along its axis to meet B's centerline
    - If member B's endpoint projects to A's mid-span and A's doesn't to B's:
      -> B is the stem, A is the top
      -> DON'T move A (B will be extended when its slot is processed)
    - If both project to each other's mid-span: cross junction
      -> Extend both to intersection
    """
    n = len(members)
    if n < 2:
        return

    # Collect endpoints that are NOT already at column nodes
    slots = []  # (member_index, which, point, half_width, angle_deg, node_id)
    for i, m in enumerate(members):
        # Check if p1 is at a column node
        p1_at_column = False
        for node in graph.nodes:
            if node[2] in graph.column_node_ids:
                if distance(m["p1"], (node[0], node[1])) < 1.0:
                    p1_at_column = True
                    break
        if not p1_at_column:
            slots.append((i, 0, m["p1"], m["half_width"], m["angle_deg"]))
        
        p2_at_column = False
        for node in graph.nodes:
            if node[2] in graph.column_node_ids:
                if distance(m["p2"], (node[0], node[1])) < 1.0:
                    p2_at_column = True
                    break
        if not p2_at_column:
            slots.append((i, 1, m["p2"], m["half_width"], m["angle_deg"]))

    if len(slots) < 2:
        return

    uf = _UnionFind(len(slots))
    for a in range(len(slots)):
        ia, _, pa, wa, aa = slots[a]
        for b in range(a + 1, len(slots)):
            ib, _, pb, wb, ab = slots[b]
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
            mi, which, _, _, _ = slots[idx]
            if which == 0:
                members[mi]["p1"] = target
            else:
                members[mi]["p2"] = target
        result.corners_snapped += 1

    # T-junctions: endpoints not merged with another endpoint -- 
    # EXTEND terminating member to meet the through-member's centerline
    for idx in range(len(slots)):
        if idx in matched_slots:
            continue
        mi, which, pt, w, angle_deg = slots[idx]
        # Use potentially updated endpoint
        pt = members[mi]["p1"] if which == 0 else members[mi]["p2"]
        
        # Direction vector of THIS member (the one being snapped)
        angle_rad = math.radians(angle_deg)
        dir_vec = (math.cos(angle_rad), math.sin(angle_rad))
        
        best = None
        best_score = float('inf')
        
        for j, m in enumerate(members):
            if j == mi:
                continue
            
            # Project our endpoint onto the other member's span
            proj, dist_to_line = closest_point_on_segment(pt, m["p1"], m["p2"])
            
            # Skip if projection lands at other member's endpoint (handled by corner clustering)
            near_end = distance(proj, m["p1"]) < 1.0 or distance(proj, m["p2"]) < 1.0
            if near_end:
                continue
            
            tol = _snap_tolerance(w, m["half_width"])
            if dist_to_line > tol:
                continue
            
            # KEY CHANGE: Determine if WE are the terminating member or the through-member
            # We are terminating if our endpoint is near the other member's MID-SPAN
            # We are through if the other's endpoint is near OUR mid-span
            
            # Parameter of projection on other member (0 to 1)
            t_on_other = project_point_on_line(proj, m["p1"], m["p2"])
            
            # Parameter of other member's endpoints on OUR line
            # Create a point far along our direction for projection
            far_pt = (pt[0] + dir_vec[0] * 10000, pt[1] + dir_vec[1] * 10000)
            t_other_p1_on_us = project_point_on_line(m["p1"], pt, far_pt)
            t_other_p2_on_us = project_point_on_line(m["p2"], pt, far_pt)
            
            # Other member spans across our line if its endpoints project to opposite sides
            other_spans_us = (t_other_p1_on_us < 0 and t_other_p2_on_us > 0) or \
                            (t_other_p1_on_us > 0 and t_other_p2_on_us < 0)
            
            # Our endpoint projects to other's mid-span (0.15 to 0.85) = we terminate on them
            we_terminate = 0.15 <= t_on_other <= 0.85
            
            # Other's endpoint projects to our mid-span = they terminate on us
            they_terminate = False
            # Check if either of their endpoints is near our mid-span
            our_mid = ((members[mi]["p1"][0] + members[mi]["p2"][0])/2,
                       (members[mi]["p1"][1] + members[mi]["p2"][1])/2)
            if distance(m["p1"], our_mid) < tol * 2 or distance(m["p2"], our_mid) < tol * 2:
                they_terminate = True
            
            # DECISION LOGIC:
            if we_terminate and not they_terminate:
                # WE are the stem of a T, THEY are the top - EXTEND US to meet them
                # Move our endpoint along OUR axis to the projection point
                t = (proj[0] - pt[0]) * dir_vec[0] + (proj[1] - pt[1]) * dir_vec[1]
                snapped_pt = (pt[0] + t * dir_vec[0], pt[1] + t * dir_vec[1])
                score = dist_to_line  # prefer closer
                if score < best_score:
                    best_score = score
                    best = (snapped_pt, "extend_to_meet")
                    
            elif they_terminate and not we_terminate:
                # THEY are the stem, WE are the top - DON'T MOVE US
                # But we should extend THEM to meet us (handled when processing their slot)
                continue
                
            elif we_terminate and they_terminate:
                # Both near each other's mid-span = cross junction
                # Extend both to intersection (handled by corner clustering ideally)
                t = (proj[0] - pt[0]) * dir_vec[0] + (proj[1] - pt[1]) * dir_vec[1]
                snapped_pt = (pt[0] + t * dir_vec[0], pt[1] + t * dir_vec[1])
                score = dist_to_line + 100  # slight penalty
                if score < best_score:
                    best_score = score
                    best = (snapped_pt, "cross")
            else:
                # Ambiguous - fallback to closest projection along our axis
                t = (proj[0] - pt[0]) * dir_vec[0] + (proj[1] - pt[1]) * dir_vec[1]
                snapped_pt = (pt[0] + t * dir_vec[0], pt[1] + t * dir_vec[1])
                score = dist_to_line + 1000
                if score < best_score:
                    best_score = score
                    best = (snapped_pt, "fallback")
        
        if best is not None:
            if which == 0:
                members[mi]["p1"] = best[0]
            else:
                members[mi]["p2"] = best[0]
            result.t_junctions += 1


def _cluster_target(idxs, slots, members):
    points = [slots[i][2] for i in idxs]
    avg = (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))

    # Prefer intersection of two non-parallel member lines in the cluster
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


# ── slab edge expansion (PRESERVES EDGE DIRECTIONS) ────────────────────────

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
    """
    Expand each slab edge OUTWARD along its normal to the nearest parallel
    member centerline. New vertices are computed by intersecting the EXTENDED
    original edge lines (not the member centerlines), preserving all edge
    directions exactly.
    
    Only expands to members that SPAN the full slab edge (projection of edge
    endpoints onto member covers [0,1] with tolerance).
    """
    n = len(vertices)
    if n < 3:
        return vertices

    signed_area = _polygon_signed_area(vertices)
    ccw = signed_area > 0

    # For each edge, find the target member centerline and compute offset distance
    edge_offsets = [None] * n  # distance to expand each edge (positive = outward)
    
    for i in range(n):
        a, b = vertices[i], vertices[(i + 1) % n]
        edge_dir = (b[0] - a[0], b[1] - a[1])
        edge_len = math.hypot(*edge_dir)
        if edge_len == 0:
            continue
        
        # Outward normal (perpendicular to edge, pointing outside slab)
        if ccw:
            normal = (edge_dir[1] / edge_len, -edge_dir[0] / edge_len)
        else:
            normal = (-edge_dir[1] / edge_len, edge_dir[0] / edge_len)
        
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)

        best = None
        best_score = float('inf')
        for m in members:
            mdir = (m["p2"][0] - m["p1"][0], m["p2"][1] - m["p1"][1])
            if not _are_parallel(edge_dir, mdir, SLAB_EXPAND_ANGLE_TOL_DEG):
                continue
            
            # Project slab edge MIDPOINT onto member centerline
            proj, dist_to_line = closest_point_on_segment(mid, m["p1"], m["p2"])
            
            # NEW: Check if member spans the slab edge (projection of edge endpoints onto member)
            t_a = project_point_on_line(a, m["p1"], m["p2"])
            t_b = project_point_on_line(b, m["p1"], m["p2"])
            t_min, t_max = min(t_a, t_b), max(t_a, t_b)
            # Member covers slab edge if [t_min, t_max] covers [0, 1] with some tolerance
            covers = (t_min <= 0.1 and t_max >= 0.9)
            
            # Gap along slab edge normal (positive = member is outward)
            gap = (proj[0] - mid[0]) * normal[0] + (proj[1] - mid[1]) * normal[1]
            if gap <= 0:
                continue  # member is inward, not an outward support
            
            # Perpendicular distance from slab edge to member centerline
            perp_gap = abs((mid[0] - m["p1"][0]) * normal[0] + (mid[1] - m["p1"][1]) * normal[1])
            if perp_gap > SLAB_EXPAND_MAX_GAP_MM:
                continue
            
            # Prefer members that fully span the edge
            score = perp_gap + (0.0 if covers else 1000.0)
            if score < best_score:
                best_score = score
                best = gap
        
        if best is not None:
            edge_offsets[i] = best

    if not any(edge_offsets):
        return vertices

    # NEW VERTICES: Intersect the OFFSET original edge lines
    # Each edge line is shifted outward by its offset along its normal
    # This PRESERVES the original edge directions exactly
    new_vertices = []
    expanded_edges = 0
    
    for i in range(n):
        prev_i = (i - 1) % n
        
        # Edge i-1 (incoming to vertex i)
        a1, b1 = vertices[prev_i], vertices[i]
        dir1 = (b1[0] - a1[0], b1[1] - a1[1])
        len1 = math.hypot(*dir1)
        if len1 == 0:
            new_vertices.append(vertices[i])
            continue
        if ccw:
            norm1 = (dir1[1] / len1, -dir1[0] / len1)
        else:
            norm1 = (-dir1[1] / len1, dir1[0] / len1)
        offset1 = edge_offsets[prev_i] if edge_offsets[prev_i] is not None else 0.0
        
        # Edge i (outgoing from vertex i)
        a2, b2 = vertices[i], vertices[(i + 1) % n]
        dir2 = (b2[0] - a2[0], b2[1] - a2[1])
        len2 = math.hypot(*dir2)
        if len2 == 0:
            new_vertices.append(vertices[i])
            continue
        if ccw:
            norm2 = (dir2[1] / len2, -dir2[0] / len2)
        else:
            norm2 = (-dir2[1] / len2, dir2[0] / len2)
        offset2 = edge_offsets[i] if edge_offsets[i] is not None else 0.0
        
        # Offset the two edge lines
        p1 = (a1[0] + offset1 * norm1[0], a1[1] + offset1 * norm1[1])
        p2 = (a2[0] + offset2 * norm2[0], a2[1] + offset2 * norm2[1])
        
        pt = line_intersection(p1, (p1[0] + dir1[0], p1[1] + dir1[1]),
                               p2, (p2[0] + dir2[0], p2[1] + dir2[1]))
        
        if pt is not None:
            max_move = SLAB_EXPAND_MAX_GAP_MM * 3
            if distance(pt, vertices[i]) <= max_move:
                new_vertices.append(pt)
            else:
                new_vertices.append(vertices[i])
        else:
            new_vertices.append(vertices[i])
        
        if edge_offsets[i] is not None:
            expanded_edges += 1

    # Validation: expansion should only grow the slab
    if polygon_area_sqmm(new_vertices) < polygon_area_sqmm(vertices) - 1.0:
        return vertices

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
    eligible_columns = [c for c in columns if not c.warnings and c.width_mm > 0 and c.depth_mm > 0]

    # Build structural graph: columns FIRST (they define the grid nodes)
    graph = _StructuralGraph(NODE_SNAP_MIN_MM, NODE_SNAP_MAX_MM, NODE_SNAP_FACTOR)
    
    # Add columns as fixed nodes
    for col in eligible_columns:
        graph.add_column(col, id(col))
    
    # Add walls and beams, snapping endpoints to column nodes
    members = []
    for w in eligible_walls:
        n1, n2, sp1, sp2, degenerate = graph.add_member(
            "wall", w, w.p1, w.p2, w.thickness_mm / 2.0, w.angle_deg, id(w)
        )
        if not degenerate:
            members.append({
                "p1": sp1, "p2": sp2, 
                "half_width": w.thickness_mm / 2.0, 
                "angle_deg": w.angle_deg,
                "kind": "wall", "entry": w,
                "graph_n1": n1, "graph_n2": n2
            })
        else:
            result.column_snaps += 1  # both ends at same column = degenerate
    
    for b in eligible_beams:
        n1, n2, sp1, sp2, degenerate = graph.add_member(
            "beam", b, b.p1, b.p2, b.width_mm / 2.0, b.angle_deg, id(b)
        )
        if not degenerate:
            members.append({
                "p1": sp1, "p2": sp2, 
                "half_width": b.width_mm / 2.0, 
                "angle_deg": b.angle_deg,
                "kind": "beam", "entry": b,
                "graph_n1": n1, "graph_n2": n2
            })
        else:
            result.column_snaps += 1

    # Second pass: snap member endpoints to each other (corners/T-junctions)
    _snap_members_to_graph(members, graph, result)

    MIN_SNAPPED_LENGTH_M = 0.05

    # Rebuild walls with snapped endpoints
    new_walls = []
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

    # Rebuild beams with snapped endpoints
    new_beams = []
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

    # Columns stay at their centroids (they define the grid)
    new_columns = columns  # unchanged

    # Slabs: expand to snapped member centerlines
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
        f"{result.column_snaps} beam/wall-to-column snaps, "
        f"{result.slab_edges_expanded} slab edge(s) expanded to member centerlines "
        f"(all directions preserved).")

    return {"slabs": new_slabs, "walls": new_walls, "beams": new_beams, "columns": new_columns}
