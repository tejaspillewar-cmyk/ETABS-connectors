"""Geometry utilities for polygon and line calculations. Pure math, no DXF dependency."""
import math


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
    drawn geometry, robust to rotation and to extra/collinear vertices --
    unlike assuming vertices [0..3] are already an axis-aligned rectangle.

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

    # Rotate the rotated-frame center back to the original frame.
    cx_r, cy_r = (min_x + max_x) / 2, (min_y + max_y) / 2
    cos_b, sin_b = math.cos(edge_angle), math.sin(edge_angle)
    center = (cx_r * cos_b - cy_r * sin_b, cx_r * sin_b + cy_r * cos_b)

    angle_deg = math.degrees(edge_angle) % 180.0
    if dim1 < dim2:
        angle_deg = (angle_deg + 90.0) % 180.0  # long axis is the y-side, not the edge itself

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
