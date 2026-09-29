"""Geometry utilities for polygon and line calculations. Pure math, no DXF dependency.
Extended with medial axis / skeleton computation for true centerline extraction.
"""
import math
from collections import defaultdict
import heapq


def polygon_area_sqmm(vertices: list[tuple[float, float]]) -> float:
    """Shoelace-formula area of a polygon. `vertices` are (x, y) in mm. Returns mm^2."""
    n = len(vertices)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        x1, y1 = vertices[i]
        x2, y2 = vertices[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def sqmm_to_sqm(area_sqmm: float) -> float:
    return area_sqmm / 1_000_000.0


def polygon_centroid(vertices: list[tuple[float, float]]) -> tuple[float, float]:
    n = len(vertices)
    if n == 0:
        return (0.0, 0.0)
    cx = sum(v[0] for v in vertices) / n
    cy = sum(v[1] for v in vertices) / n
    return (cx, cy)


def point_in_polygon(px: float, py: float, polygon: list[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon test."""
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def polygon_bbox(vertices: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    """Returns (min_x, min_y, max_x, max_y)."""
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    return (min(xs), min(ys), max(xs), max(ys))


# ── Line / beam-and-column geometry helpers ─────────────────────────────────

def distance(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    return math.hypot(p1[0] - p2[0], p1[1] - p2[1])


def line_angle(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Angle of line in degrees, normalized to [0, 180)."""
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    angle = math.degrees(math.atan2(dy, dx))
    if angle < 0:
        angle += 180.0
    elif angle >= 180.0:
        angle -= 180.0
    return angle


def point_line_distance(pt, lp1, lp2) -> float:
    """Perpendicular distance from pt to the infinite line through lp1, lp2."""
    num = abs((lp2[1] - lp1[1]) * pt[0] - (lp2[0] - lp1[0]) * pt[1] + lp2[0] * lp1[1] - lp2[1] * lp1[0])
    den = distance(lp1, lp2)
    if den == 0:
        return distance(pt, lp1)
    return num / den


def are_lines_parallel(l1p1, l1p2, l2p1, l2p2, tol_deg: float = 2.0) -> bool:
    a1 = line_angle(l1p1, l1p2)
    a2 = line_angle(l2p1, l2p2)
    diff = abs(a1 - a2)
    return diff < tol_deg or abs(diff - 180.0) < tol_deg


def project_point_on_line(pt, lp1, lp2) -> float:
    """Parameter t where projected point = lp1 + t*(lp2 - lp1)."""
    dx = lp2[0] - lp1[0]
    dy = lp2[1] - lp1[1]
    l2 = dx * dx + dy * dy
    if l2 == 0:
        return 0.0
    return ((pt[0] - lp1[0]) * dx + (pt[1] - lp1[1]) * dy) / l2


def check_lines_overlap(l1p1, l1p2, l2p1, l2p2) -> float:
    """Overlapping length (mm) of parallel line 2 projected onto line 1. 0 if none."""
    t1 = project_point_on_line(l2p1, l1p1, l1p2)
    t2 = project_point_on_line(l2p2, l1p1, l1p2)
    t_min, t_max = min(t1, t2), max(t1, t2)
    overlap_start = max(0.0, t_min)
    overlap_end = min(1.0, t_max)
    if overlap_start < overlap_end:
        return (overlap_end - overlap_start) * distance(l1p1, l1p2)
    return 0.0


def convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew's monotone chain convex hull. Returns hull vertices, counter-clockwise."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def min_bounding_rectangle(vertices: list[tuple[float, float]]) -> dict:
    """
    Minimum-area bounding rectangle of a point set, via rotating calipers over
    the convex hull. Used to measure a wall/beam/column footprint's true
    short side (thickness/width) and long side (length) directly from the
    drawn geometry, robust to rotation and to extra/collinear vertices.

    Returns {"width": short side mm, "length": long side mm,
             "angle_deg": long-axis angle in [0, 180), "center": (cx, cy)}.
    """
    hull = convex_hull(vertices)
    n = len(hull)
    if n < 3:
        min_x, min_y, max_x, max_y = polygon_bbox(vertices)
        w, h = max_x - min_x, max_y - min_y
        return {
            "width": min(w, h), "length": max(w, h), "angle_deg": 0.0,
            "center": ((min_x + max_x) / 2, (min_y + max_y) / 2),
        }

    best = None
    for i in range(n):
        p1, p2 = hull[i], hull[(i + 1) % n]
        edge_angle = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
        cos_a, sin_a = math.cos(-edge_angle), math.sin(-edge_angle)
        rotated = [(px * cos_a - py * sin_a, px * sin_a + py * cos_a) for px, py in hull]
        xs = [p[0] for p in rotated]
        ys = [p[1] for p in rotated]
        min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
        area = (max_x - min_x) * (max_y - min_y)
        if best is None or area < best[0]:
            best = (area, min_x, max_x, min_y, max_y, edge_angle)

    _, min_x, max_x, min_y, max_y, edge_angle = best
    dim1, dim2 = max_x - min_x, max_y - min_y
    width, length = min(dim1, dim2), max(dim1, dim2)

    cx_r, cy_r = (min_x + max_x) / 2, (min_y + max_y) / 2
    cos_b, sin_b = math.cos(edge_angle), math.sin(edge_angle)
    center = (cx_r * cos_b - cy_r * sin_b, cx_r * sin_b + cy_r * cos_b)

    angle_deg = math.degrees(edge_angle) % 180.0
    if dim1 < dim2:
        angle_deg = (angle_deg + 90.0) % 180.0

    return {"width": width, "length": length, "angle_deg": angle_deg, "center": center}


def closest_point_on_segment(pt, a, b) -> tuple[tuple[float, float], float]:
    """Closest point on segment [a, b] to pt, and the distance to it."""
    l2 = (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2
    if l2 == 0:
        return a, distance(pt, a)
    t = max(0, min(1, ((pt[0] - a[0]) * (b[0] - a[0]) + (pt[1] - a[1]) * (b[1] - a[1])) / l2))
    proj = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
    return proj, distance(pt, proj)


def distance_pt_to_polygon(pt, poly) -> float:
    """Shortest distance from pt to any edge of poly."""
    min_dist = float("inf")
    n = len(poly)
    for i in range(n):
        _, d = closest_point_on_segment(pt, poly[i], poly[(i + 1) % n])
        if d < min_dist:
            min_dist = d
    return min_dist


def line_intersection(p1, p2, p3, p4):
    """
    Intersection point of the INFINITE lines through (p1, p2) and (p3, p4).
    Returns None if the lines are parallel (no unique intersection).
    """
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    x4, y4 = p4
    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < 1e-9:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


# ── Medial Axis / Skeleton for True Centerlines ─────────────────────────────

def _polygon_edges(vertices: list[tuple[float, float]]) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Return list of edges as (start, end) tuples."""
    n = len(vertices)
    return [(vertices[i], vertices[(i + 1) % n]) for i in range(n)]


def _point_to_segment_dist(pt, seg_start, seg_end) -> float:
    """Distance from point to line segment."""
    _, d = closest_point_on_segment(pt, seg_start, seg_end)
    return d


def _segment_midpoint(seg_start, seg_end) -> tuple[float, float]:
    return ((seg_start[0] + seg_end[0]) / 2.0, (seg_start[1] + seg_end[1]) / 2.0)


def _angle_between(v1, v2) -> float:
    """Angle between two vectors in radians [0, pi]."""
    dot = v1[0]*v2[0] + v1[1]*v2[1]
    norm1 = math.hypot(v1[0], v1[1])
    norm2 = math.hypot(v2[0], v2[1])
    if norm1 == 0 or norm2 == 0:
        return 0.0
    cos = max(-1.0, min(1.0, dot / (norm1 * norm2)))
    return math.acos(cos)


def _vector(p1, p2):
    return (p2[0] - p1[0], p2[1] - p1[1])


def _normalize(v):
    norm = math.hypot(v[0], v[1])
    if norm == 0:
        return (0.0, 0.0)
    return (v[0]/norm, v[1]/norm)


def _offset_point(pt, vec, dist):
    """Offset point by distance along normalized vector."""
    nv = _normalize(vec)
    return (pt[0] + nv[0]*dist, pt[1] + nv[1]*dist)


def compute_medial_axis(vertices: list[tuple[float, float]], 
                        max_iterations: int = 1000,
                        convergence_tol: float = 1.0) -> list[tuple[float, float]]:
    """
    Compute approximate medial axis (skeleton) of a polygon using 
    Voronoi-based approach on polygon vertices and edge midpoints.
    
    Returns list of skeleton points (x, y) that form the centerline network.
    For simple rectangular walls, this gives the true centerline.
    For complex shapes (L, T, cross), it gives the junction topology.
    """
    if len(vertices) < 3:
        return []
    
    edges = _polygon_edges(vertices)
    
    # Sample points: vertices + edge midpoints
    sample_points = list(vertices)
    for e_start, e_end in edges:
        sample_points.append(_segment_midpoint(e_start, e_end))
    
    # For each sample point, compute distance to polygon boundary
    # The medial axis points are local maxima of this distance field
    medial_points = []
    
    # Use a grid-based approach for robustness
    min_x, min_y, max_x, max_y = polygon_bbox(vertices)
    bbox_w = max_x - min_x
    bbox_h = max_y - min_y
    
    # Adaptive grid resolution based on polygon size
    grid_size = max(20, min(100, int(max(bbox_w, bbox_h) / 50)))
    cell_size = max(bbox_w, bbox_h) / grid_size
    
    # Compute distance field on grid
    dist_field = {}
    for i in range(grid_size + 1):
        for j in range(grid_size + 1):
            x = min_x + i * cell_size
            y = min_y + j * cell_size
            if point_in_polygon(x, y, vertices):
                # Distance to nearest edge
                min_dist = float('inf')
                for e_start, e_end in edges:
                    d = _point_to_segment_dist((x, y), e_start, e_end)
                    if d < min_dist:
                        min_dist = d
                dist_field[(i, j)] = min_dist
    
    # Find local maxima (ridge points)
    for (i, j), d in dist_field.items():
        is_max = True
        for di in [-1, 0, 1]:
            for dj in [-1, 0, 1]:
                if di == 0 and dj == 0:
                    continue
                ni, nj = i + di, j + dj
                if (ni, nj) in dist_field and dist_field[(ni, nj)] > d + convergence_tol:
                    is_max = False
                    break
            if not is_max:
                break
        if is_max and d > convergence_tol:
            medial_points.append((min_x + i * cell_size, min_y + j * cell_size))
    
    # Thin the medial points - keep only well-separated ones
    if not medial_points:
        return []
    
    # Cluster nearby medial points
    clusters = []
    used = set()
    for i, pt in enumerate(medial_points):
        if i in used:
            continue
        cluster = [pt]
        used.add(i)
        for j, pt2 in enumerate(medial_points):
            if j in used:
                continue
            if distance(pt, pt2) < cell_size * 1.5:
                cluster.append(pt2)
                used.add(j)
        # Cluster centroid
        cx = sum(p[0] for p in cluster) / len(cluster)
        cy = sum(p[1] for p in cluster) / len(cluster)
        clusters.append((cx, cy))
    
    return clusters


def extract_centerline_from_polygon(vertices: list[tuple[float, float]],
                                    expected_thickness: float = None) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """
    Extract centerline segments from a wall/beam polygon.
    
    Returns list of (p1, p2) centerline segments with associated width.
    For a simple rectangle: returns one segment.
    For L/T shapes: returns multiple segments meeting at junctions.
    """
    if len(vertices) < 4:
        return []
    
    # Get medial axis points
    medial_pts = compute_medial_axis(vertices)
    if not medial_pts:
        # Fallback: use minimum bounding rectangle
        mbr = min_bounding_rectangle(vertices)
        center = mbr["center"]
        angle = math.radians(mbr["angle_deg"])
        half_len = mbr["length"] / 2.0
        dx = math.cos(angle) * half_len
        dy = math.sin(angle) * half_len
        p1 = (center[0] - dx, center[1] - dy)
        p2 = (center[0] + dx, center[1] + dy)
        return [((p1, p2), mbr["width"])]
    
    # Build graph connecting nearby medial points
    # Two medial points connect if the line between them stays inside polygon
    # and is roughly equidistant from boundaries
    edges = _polygon_edges(vertices)
    
    def is_valid_centerline(p1, p2):
        """Check if segment p1-p2 is a valid centerline (inside polygon, equidistant)."""
        mid = ((p1[0]+p2[0])/2, (p1[1]+p2[1])/2)
        if not point_in_polygon(mid[0], mid[1], vertices):
            return False
        # Check distance to boundaries at multiple points along segment
        for t in [0.25, 0.5, 0.75]:
            px = p1[0] + t * (p2[0] - p1[0])
            py = p1[1] + t * (p2[1] - p1[1])
            if not point_in_polygon(px, py, vertices):
                return False
            # Check equidistance (roughly)
            dists = [_point_to_segment_dist((px, py), e[0], e[1]) for e in edges]
            if max(dists) / max(1.0, min(dists)) > 3.0:  # Not roughly equidistant
                return False
        return True
    
    # Connect medial points
    graph = defaultdict(list)
    for i, p1 in enumerate(medial_pts):
        for j, p2 in enumerate(medial_pts):
            if i >= j:
                continue
            if distance(p1, p2) < max(polygon_bbox(vertices)[2] - polygon_bbox(vertices)[0],
                                      polygon_bbox(vertices)[3] - polygon_bbox(vertices)[1]) * 0.5:
                if is_valid_centerline(p1, p2):
                    graph[i].append(j)
                    graph[j].append(i)
    
    # Extract paths from graph (centerline segments)
    visited_edges = set()
    centerline_segments = []
    
    def trace_path(start, prev):
        path = [start]
        current = start
        while True:
            neighbors = [n for n in graph[current] if n != prev]
            if not neighbors:
                break
            # Prefer straight continuation
            if len(path) >= 2:
                v1 = _vector(medial_pts[path[-2]], medial_pts[path[-1]])
                best_n = None
                best_angle = float('inf')
                for n in neighbors:
                    v2 = _vector(medial_pts[current], medial_pts[n])
                    ang = _angle_between(v1, v2)
                    if ang < best_angle:
                        best_angle = ang
                        best_n = n
                if best_n is not None and best_angle < math.radians(45):
                    next_node = best_n
                else:
                    next_node = neighbors[0]
            else:
                next_node = neighbors[0]
            
            edge_key = tuple(sorted((current, next_node)))
            if edge_key in visited_edges:
                break
            visited_edges.add(edge_key)
            path.append(next_node)
            prev, current = current, next_node
        return path
    
    # Find endpoints (degree 1) and junctions (degree > 2)
    endpoints = [i for i, nbrs in graph.items() if len(nbrs) == 1]
    junctions = [i for i, nbrs in graph.items() if len(nbrs) > 2]
    
    # Trace from each endpoint
    for ep in endpoints:
        if any(tuple(sorted((ep, n))) in visited_edges for n in graph[ep]):
            continue
        path = trace_path(ep, -1)
        if len(path) >= 2:
            # Convert path to segments
            for k in range(len(path) - 1):
                p1 = medial_pts[path[k]]
                p2 = medial_pts[path[k+1]]
                # Estimate width at midpoint
                mid = ((p1[0]+p2[0])/2, (p1[1]+p2[1])/2)
                dists = [_point_to_segment_dist(mid, e[0], e[1]) for e in edges]
                width = 2 * min(dists) if dists else (expected_thickness or 200)
                centerline_segments.append(((p1, p2), width))
    
    # Trace between junctions
    for j in junctions:
        for n in graph[j]:
            edge_key = tuple(sorted((j, n)))
            if edge_key in visited_edges:
                continue
            path = trace_path(j, n)
            if len(path) >= 2:
                for k in range(len(path) - 1):
                    p1 = medial_pts[path[k]]
                    p2 = medial_pts[path[k+1]]
                    mid = ((p1[0]+p2[0])/2, (p1[1]+p2[1])/2)
                    dists = [_point_to_segment_dist(mid, e[0], e[1]) for e in edges]
                    width = 2 * min(dists) if dists else (expected_thickness or 200)
                    centerline_segments.append(((p1, p2), width))
                visited_edges.add(edge_key)
    
    # If no segments found, fallback to MBR
    if not centerline_segments:
        mbr = min_bounding_rectangle(vertices)
        center = mbr["center"]
        angle = math.radians(mbr["angle_deg"])
        half_len = mbr["length"] / 2.0
        dx = math.cos(angle) * half_len
        dy = math.sin(angle) * half_len
        p1 = (center[0] - dx, center[1] - dy)
        p2 = (center[0] + dx, center[1] + dy)
        return [((p1, p2), mbr["width"])]
    
    return centerline_segments


def pair_parallel_segments(
    segments: list[tuple[tuple[float, float], tuple[float, float]]],
    min_width: float,
    max_width: float,
    min_overlap_frac: float = 0.7,
    tol_deg: float = 2.0,
) -> list[dict]:
    """
    Pairs up near-parallel, overlapping, closely-spaced segments into
    centerline segments -- the shared logic behind "a beam drawn as two
    parallel LINEs" and "a bent wall polygon's two long edges forming one
    straight leg". Each input segment is used in at most one pair (greedy,
    best-overlap-first per segment, same as the original beam-only version).

    Returns a list of {"p1", "p2", "width", "overlap_length", "source_indices"}
    dicts. p1/p2 are the paired segments' true midline -- endpoint
    correspondence is resolved by whichever pairing (same-order vs crossed)
    puts endpoints closer together, so a segment stored in the opposite
    direction to its partner still produces a straight (not twisted)
    centerline. source_indices are the two indices into the input `segments`
    list that were paired, so callers can look up their own per-segment
    metadata (e.g. layer name).
    """
    n = len(segments)
    used = set()
    pairs = []

    for i in range(n):
        if i in used:
            continue
        p1a, p1b = segments[i]
        len1 = distance(p1a, p1b)

        best_j, best_overlap, best_width = -1, 0.0, 0.0
        for j in range(n):
            if i == j or j in used:
                continue
            p2a, p2b = segments[j]
            len2 = distance(p2a, p2b)
            if not are_lines_parallel(p1a, p1b, p2a, p2b, tol_deg=tol_deg):
                continue
            overlap = check_lines_overlap(p1a, p1b, p2a, p2b)
            if overlap <= len1 * min_overlap_frac or overlap <= len2 * min_overlap_frac:
                continue
            width = point_line_distance(p2a, p1a, p1b)
            if min_width < width < max_width and overlap > best_overlap:
                best_j, best_overlap, best_width = j, overlap, width

        if best_j != -1:
            used.add(i)
            used.add(best_j)
            p2a, p2b = segments[best_j]
            same_order = distance(p1a, p2a) + distance(p1b, p2b)
            crossed = distance(p1a, p2b) + distance(p1b, p2a)
            if crossed < same_order:
                p2a, p2b = p2b, p2a
            cl_p1 = ((p1a[0] + p2a[0]) / 2, (p1a[1] + p2a[1]) / 2)
            cl_p2 = ((p1b[0] + p2b[0]) / 2, (p1b[1] + p2b[1]) / 2)
            pairs.append({
                "p1": cl_p1, "p2": cl_p2, "width": best_width, "overlap_length": best_overlap,
                "source_indices": (i, best_j),
            })

    return pairs


# ── Structural Graph Connectivity ───────────────────────────────────────────

class StructuralGraph:
    """Graph representation of structural connectivity for snapping."""
    
    def __init__(self, snap_tolerance: float = 150.0):
        self.nodes = []  # (x, y, node_id, connected_elements)
        self.edges = []  # (node_id_1, node_id_2, element_type, element_ref)
        self.snap_tolerance = snap_tolerance
        self._next_node_id = 0
        self._kdtree = None  # Simple list for now, can optimize later
    
    def add_element(self, element_type: str, element_ref, p1: tuple, p2: tuple, 
                    width: float, element_id: str = None):
        """Add a structural element (wall, beam, column) to the graph."""
        # Snap endpoints to existing nodes or create new ones
        n1 = self._snap_or_create_node(p1, element_type, element_ref, element_id)
        n2 = self._snap_or_create_node(p2, element_type, element_ref, element_id)
        
        # Add edge
        self.edges.append((n1, n2, element_type, element_ref))
        
        # Update node connectivity
        for nid in (n1, n2):
            for node in self.nodes:
                if node[2] == nid:
                    node[3].add((element_type, element_ref))
                    break
        
        return n1, n2
    
    def _snap_or_create_node(self, pt, element_type, element_ref, element_id):
        """Snap point to existing node within tolerance, or create new node."""
        best_node = None
        best_dist = self.snap_tolerance
        
        for node in self.nodes:
            nx, ny, nid, _ = node
            d = distance(pt, (nx, ny))
            if d < best_dist:
                best_dist = d
                best_node = node
        
        if best_node:
            # Snap to existing node
            return best_node[2]
        else:
            # Create new node
            nid = self._next_node_id
            self._next_node_id += 1
            self.nodes.append([pt[0], pt[1], nid, {(element_type, element_ref)}])
            return nid
    
    def get_node_position(self, node_id):
        for node in self.nodes:
            if node[2] == node_id:
                return (node[0], node[1])
        return None
    
    def get_connected_elements(self, node_id):
        for node in self.nodes:
            if node[2] == node_id:
                return node[3]
        return set()
    
    def finalize_positions(self):
        """Optionally adjust node positions to average of snapped points."""
        # For now, positions are already the snapped positions
        pass


def build_structural_graph(parsed: dict, snap_tolerance: float = 150.0) -> StructuralGraph:
    """Build a structural connectivity graph from parsed elements."""
    graph = StructuralGraph(snap_tolerance)
    
    # Add columns first (they define grid points)
    for col in parsed.get("columns", []):
        if col.warnings or col.width_mm <= 0 or col.depth_mm <= 0:
            continue
        cx, cy = col.centroid
        # Columns are vertical - their plan position is a node
        graph.add_element("column", col, (cx, cy), (cx, cy), max(col.width_mm, col.depth_mm), col.name)
    
    # Add walls
    for wall in parsed.get("walls", []):
        if wall.warnings or wall.thickness_mm <= 0 or wall.length_m <= 0:
            continue
        graph.add_element("wall", wall, wall.p1, wall.p2, wall.thickness_mm, wall.name)
    
    # Add beams
    for beam in parsed.get("beams", []):
        if beam.warnings or beam.width_mm <= 0 or beam.depth_mm <= 0 or beam.length_m <= 0:
            continue
        graph.add_element("beam", beam, beam.p1, beam.p2, beam.width_mm, beam.name)
    
    # Slabs don't add edges but their vertices should snap to wall/beam nodes
    for slab in parsed.get("slabs", []):
        if slab.warnings or slab.thickness_mm <= 0 or not slab.vertices:
            continue
        for v in slab.vertices:
            graph._snap_or_create_node(v, "slab", slab, slab.name)
    
    return graph


def snap_to_graph(parsed: dict, graph: StructuralGraph) -> dict:
    """Snap all element endpoints to the structural graph nodes."""
    # Walls
    new_walls = []
    for wall in parsed.get("walls", []):
        if wall.warnings or wall.thickness_mm <= 0 or wall.length_m <= 0:
            new_walls.append(wall)
            continue
        n1 = graph._snap_or_create_node(wall.p1, "wall", wall, wall.name)
        n2 = graph._snap_or_create_node(wall.p2, "wall", wall, wall.name)
        p1_snapped = graph.get_node_position(n1)
        p2_snapped = graph.get_node_position(n2)
        if p1_snapped and p2_snapped:
            from dataclasses import replace
            new_walls.append(replace(wall, p1=p1_snapped, p2=p2_snapped))
        else:
            new_walls.append(wall)
    
    # Beams
    new_beams = []
    for beam in parsed.get("beams", []):
        if beam.warnings or beam.width_mm <= 0 or beam.depth_mm <= 0 or beam.length_m <= 0:
            new_beams.append(beam)
            continue
        n1 = graph._snap_or_create_node(beam.p1, "beam", beam, beam.name)
        n2 = graph._snap_or_create_node(beam.p2, "beam", beam, beam.name)
        p1_snapped = graph.get_node_position(n1)
        p2_snapped = graph.get_node_position(n2)
        if p1_snapped and p2_snapped:
            from dataclasses import replace
            new_beams.append(replace(beam, p1=p1_snapped, p2=p2_snapped))
        else:
            new_beams.append(beam)
    
    # Columns (already at nodes, but ensure consistency)
    new_columns = []
    for col in parsed.get("columns", []):
        if col.warnings or col.width_mm <= 0 or col.depth_mm <= 0:
            new_columns.append(col)
            continue
        cx, cy = col.centroid
        n = graph._snap_or_create_node((cx, cy), "column", col, col.name)
        snapped_pos = graph.get_node_position(n)
        if snapped_pos:
            from dataclasses import replace
            new_columns.append(replace(col, centroid=snapped_pos))
        else:
            new_columns.append(col)
    
    # Slabs - expand vertices to wall/beam centerlines
    new_slabs = []
    for slab in parsed.get("slabs", []):
        if slab.warnings or slab.thickness_mm <= 0 or not slab.vertices:
            new_slabs.append(slab)
            continue
        new_vertices = []
        for v in slab.vertices:
            n = graph._snap_or_create_node(v, "slab", slab, slab.name)
            snapped = graph.get_node_position(n)
            if snapped:
                new_vertices.append(snapped)
            else:
                new_vertices.append(v)
        if new_vertices != slab.vertices:
            from dataclasses import replace
            from .geometry import polygon_area_sqmm, sqmm_to_sqm, polygon_centroid
            area_sqm = sqmm_to_sqm(polygon_area_sqmm(new_vertices))
            cx, cy = polygon_centroid(new_vertices)
            volume_cum = area_sqm * (slab.thickness_mm / 1000.0)
            new_slabs.append(replace(slab, vertices=new_vertices, 
                                     area_sqm=round(area_sqm, 3),
                                     volume_cum=round(volume_cum, 4),
                                     centroid=(round(cx, 1), round(cy, 1))))
        else:
            new_slabs.append(slab)
    
    return {
        "slabs": new_slabs,
        "walls": new_walls,
        "beams": new_beams,
        "columns": new_columns,
        "origin_offset": parsed.get("origin_offset", (0.0, 0.0)),
        "scale": parsed.get("scale", 1.0),
        "scale_source": parsed.get("scale_source", "unknown")
    }
