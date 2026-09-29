"""
Wall & slab extraction.

Convention (ported from the concrete-qty-takeoff-from-dxf reference tool,
extended for walls -- see _resolve_thickness_and_warnings below):
  - Walls/slabs are drawn as closed LWPOLYLINE footprint rectangles.
  - A nearby TEXT/MTEXT label containing "...NNN THK..." gives an explicit
    thickness for the polygon it sits inside (matched by point-in-polygon);
    this always wins when present, since it's the engineer's own annotation.
  - Slabs: thickness is a vertical dimension invisible in plan view, so
    without a text label it can only come from the layer name's trailing
    number (e.g. "STR-SLAB-REG150" -> 150).
  - Walls: thickness IS visible in plan view -- it's the short side of the
    wall's footprint rectangle. So without a text label, thickness is
    measured directly from the drawn geometry (via min_bounding_rectangle),
    not just assumed from the layer name. The layer name is only used as a
    plausibility cross-check, producing a warning on disagreement.
  - Bent/L/T walls drawn as ONE polygon: min_bounding_rectangle fits the
    polygon's *entire* extent, which for a bent shape wildly overstates its
    thickness (the fill ratio -- polygon area / bounding-box area -- is far
    below 1). Below BENT_WALL_FILL_RATIO_THRESHOLD, the polygon's own
    boundary edges are decomposed into straight legs via
    pair_parallel_segments -- the same parallel-edge-pairing technique
    beam_extractor.py uses for beams drawn as two parallel lines, just
    applied to a polygon's edges instead of independent LINE entities.
  - MULTIPLE polygons forming a wall system (T, L, cross junctions): grouped
    by vertex proximity, then medial axis extracts the combined centerline
    network with proper junction connectivity.
  - Explicit p1/p2 centerline endpoints are stored on WallEntry so that the
    connectivity pass and ETABS writer can use them directly rather than
    re-deriving them from centroid + angle + length (which is lossy after
    snapping moves one endpoint more than the other).
"""
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field

from .geometry import (
    polygon_area_sqmm, sqmm_to_sqm, polygon_area_centroid, point_in_polygon, min_bounding_rectangle,
    distance, line_angle, pair_parallel_segments, extract_centerline_from_polygon,
)
from .dxf_helpers import extract_closed_polygons, extract_text_entities, scale_polygon_dicts, scale_text_dicts

THK_PATTERN = re.compile(r"(\d+)\s*(?:MM\s*)?THK", re.IGNORECASE)
WALL_THICKNESS_PLAUSIBLE_MM = (50, 600)  # typical structural/partition wall range
BENT_WALL_FILL_RATIO_THRESHOLD = 0.85  # below this, decompose into straight legs
WALL_POLYGON_GROUP_GAP_MM = 50.0  # max gap between polygons to consider them one wall system


@dataclass
class SlabEntry:
    name: str
    layer: str
    thickness_mm: int
    thickness_source: str  # "text_label" | "layer_name" | "unknown"
    label_text: str
    area_sqm: float
    volume_cum: float
    centroid: tuple[float, float]
    vertices: list
    warnings: list[str] = field(default_factory=list)


@dataclass
class WallEntry:
    name: str
    layer: str
    is_structural: bool
    thickness_mm: int
    thickness_raw_mm: float  # unrounded value as measured/reported, before rounding to 5mm
    thickness_source: str  # "text_label" | "geometry" | "unknown"
    label_text: str
    area_sqm: float
    length_m: float
    angle_deg: float
    centroid: tuple[float, float]
    p1: tuple[float, float]
    p2: tuple[float, float]
    vertices: list
    warnings: list[str] = field(default_factory=list)


# ── helpers ───────────────────────────────────────────────────────────────

def _endpoints_from_rect(center, angle_deg, length_mm):
    """Derive the two centerline endpoints from the MBR properties."""
    cx, cy = center
    rad = math.radians(angle_deg)
    dx = math.cos(rad) * (length_mm / 2.0)
    dy = math.sin(rad) * (length_mm / 2.0)
    return (cx - dx, cy - dy), (cx + dx, cy + dy)


def _find_thk_labels(msp, scale: float = 1.0) -> list[dict]:
    """All TEXT/MTEXT entities anywhere in the drawing matching '...NNN THK...'."""
    labels = []
    raw_texts = extract_text_entities(msp)
    scale_text_dicts(raw_texts, scale)
    for label in raw_texts:
        match = THK_PATTERN.search(label["text"].upper())
        if match:
            labels.append({
                "text": label["text"],
                "thickness_mm": int(match.group(1)),
                "position": label["position"],
            })
    return labels


def _match_thickness(vertices, layer, thk_labels, used_labels) -> tuple[int, str, str, list[str]]:
    """Returns (thickness_mm, source, label_text, warnings) for one polygon."""
    warnings = []
    layer_thk = None
    numbers = re.findall(r"\d+", layer)
    if numbers:
        layer_thk = int(numbers[-1])

    for idx, label in enumerate(thk_labels):
        if idx in used_labels:
            continue
        lx, ly = label["position"]
        if point_in_polygon(lx, ly, vertices):
            used_labels.add(idx)
            thickness_mm = label["thickness_mm"]
            if layer_thk is not None and thickness_mm != layer_thk:
                warnings.append(
                    f"Layer '{layer}' implies {layer_thk}mm but text label says "
                    f"{thickness_mm}mm ('{label['text']}'); using the text label."
                )
            return thickness_mm, "text_label", label["text"], warnings

    if layer_thk is not None:
        return layer_thk, "layer_name", f"(from layer: {layer})", warnings

    warnings.append(f"Polygon on layer '{layer}': no THK text label and no thickness in the layer name.")
    return 0, "unknown", "UNKNOWN", warnings


# ── Wall polygon grouping for T/L/cross junctions ──────────────────────────

def _group_wall_polygons(polys, max_gap=WALL_POLYGON_GROUP_GAP_MM):
    """
    Group wall polygons that touch or are within max_gap of each other.
    Returns list of polygon groups (each group = list of polygons that form one wall system).
    """
    if not polys:
        return []
    
    n = len(polys)
    # Build adjacency based on vertex proximity
    adj = [[] for _ in range(n)]
    for i in range(n):
        verts_i = polys[i]["vertices"]
        for j in range(i+1, n):
            verts_j = polys[j]["vertices"]
            # Check if any vertex of i is close to any vertex of j
            close = False
            for vi in verts_i:
                for vj in verts_j:
                    if distance(vi, vj) <= max_gap:
                        close = True
                        break
                if close:
                    break
            if close:
                adj[i].append(j)
                adj[j].append(i)
    
    # Find connected components
    visited = [False] * n
    groups = []
    for i in range(n):
        if visited[i]:
            continue
        component = []
        stack = [i]
        visited[i] = True
        while stack:
            idx = stack.pop()
            component.append(polys[idx])
            for nb in adj[idx]:
                if not visited[nb]:
                    visited[nb] = True
                    stack.append(nb)
        groups.append(component)
    return groups


def _merge_polygons_union(polygons):
    """
    Collect all boundary edges from multiple polygons.
    For medial axis, we need the combined edge network.
    """
    all_edges = []
    for poly in polygons:
        verts = poly["vertices"]
        for k in range(len(verts)):
            p1 = verts[k]
            p2 = verts[(k+1) % len(verts)]
            all_edges.append((p1, p2))
    return all_edges


def _connect_centerlines_at_junctions(centerlines, junction_tol=50.0):
    """
    Given centerline segments from adjacent polygons, connect them at junctions.
    Returns merged centerline network.
    """
    if not centerlines:
        return []
    
    # Build graph of centerline endpoints
    n = len(centerlines)
    endpoints = []  # (seg_idx, which_end, point, width)
    for i, ((p1, p2), width) in enumerate(centerlines):
        endpoints.append((i, 0, p1, width))
        endpoints.append((i, 1, p2, width))
    
    # Union-find to group endpoints at junctions
    parent = list(range(len(endpoints)))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    
    for a in range(len(endpoints)):
        for b in range(a+1, len(endpoints)):
            if endpoints[a][0] == endpoints[b][0]:
                continue  # same segment
            if distance(endpoints[a][2], endpoints[b][2]) <= junction_tol:
                union(a, b)
    
    # For each junction cluster, merge the connected segments
    clusters = defaultdict(list)
    for idx in range(len(endpoints)):
        clusters[find(idx)].append(idx)
    
    merged = []
    used_segments = set()
    
    for cluster_indices in clusters.values():
        if len(cluster_indices) == 1:
            # Single endpoint - check if its segment is already used
            seg_idx = endpoints[cluster_indices[0]][0]
            if seg_idx not in used_segments:
                merged.append(centerlines[seg_idx])
                used_segments.add(seg_idx)
        else:
            # Junction: multiple segments meet here
            # Collect all unique segments in this cluster
            seg_indices = set(endpoints[i][0] for i in cluster_indices)
            # Compute junction point as average of cluster endpoints
            jx = sum(endpoints[i][2][0] for i in cluster_indices) / len(cluster_indices)
            jy = sum(endpoints[i][2][1] for i in cluster_indices) / len(cluster_indices)
            junction_pt = (jx, jy)
            
            # For each segment, replace the endpoint in this cluster with junction_pt
            for seg_idx in seg_indices:
                if seg_idx in used_segments:
                    continue
                (p1, p2), width = centerlines[seg_idx]
                # Determine which endpoint(s) are in this cluster
                new_p1 = p1
                new_p2 = p2
                for ep_idx in cluster_indices:
                    if endpoints[ep_idx][0] == seg_idx:
                        if endpoints[ep_idx][1] == 0:
                            new_p1 = junction_pt
                        else:
                            new_p2 = junction_pt
                merged.append(((new_p1, new_p2), width))
                used_segments.add(seg_idx)
    
    return merged


def _extract_centerline_from_polygon_group(polygons):
    """
    Given a group of polygons that form one wall system (straight, L, T, cross),
    compute the medial axis centerline network.
    """
    # Compute medial axis on each polygon and then connect centerlines
    # at junctions where polygons meet
    all_centerlines = []
    for poly in polygons:
        cls = extract_centerline_from_polygon(poly["vertices"])
        all_centerlines.extend(cls)
    
    # Now connect centerlines that meet at junctions (endpoint proximity)
    return _connect_centerlines_at_junctions(all_centerlines)


# ── slab extraction ───────────────────────────────────────────────────────

def extract_slabs(msp, slab_layers: list[str], scale: float = 1.0) -> list[SlabEntry]:
    if not slab_layers:
        return []
    polys = extract_closed_polygons(msp, slab_layers)
    scale_polygon_dicts(polys, scale)
    thk_labels = _find_thk_labels(msp, scale)
    used_labels: set[int] = set()

    entries = []
    counters: dict[int, int] = {}
    for poly in polys:
        vertices = poly["vertices"]
        layer = poly["layer"]
        thickness_mm, source, label_text, warnings = _match_thickness(vertices, layer, thk_labels, used_labels)

        area_sqm = sqmm_to_sqm(polygon_area_sqmm(vertices))
        volume_cum = area_sqm * (thickness_mm / 1000.0)
        cx, cy = polygon_area_centroid(vertices)

        counters[thickness_mm] = counters.get(thickness_mm, 0) + 1
        name = f"S{counters[thickness_mm]}-{thickness_mm}"

        entries.append(SlabEntry(
            name=name, layer=layer, thickness_mm=thickness_mm, thickness_source=source,
            label_text=label_text, area_sqm=round(area_sqm, 3), volume_cum=round(volume_cum, 4),
            centroid=(round(cx, 1), round(cy, 1)), vertices=vertices, warnings=warnings,
        ))
    return entries


# ── wall extraction ───────────────────────────────────────────────────────

def _resolve_thickness_and_warnings(geom_thickness, layer, text_match_vertices, thk_labels, used_labels):
    """
    Resolves a final thickness for a wall (or one decomposed leg of a bent
    wall) given its measured geometric thickness, preferring a matched TEXT
    label (searched via point-in-polygon against text_match_vertices --
    always the *original* polygon, even for a decomposed leg, since the
    leg's own centerline necessarily lies inside it) over the layer name.

    Returns (thickness_mm, thickness_raw_mm, source, label_text, warnings).
    """
    warnings = []
    lo, hi = WALL_THICKNESS_PLAUSIBLE_MM
    if not (lo <= geom_thickness <= hi):
        warnings.append(
            f"Wall on layer '{layer}': measured geometric thickness {geom_thickness:.0f}mm is outside the "
            f"typical wall range ({lo}-{hi}mm); verify this one manually."
        )

    layer_thk = None
    numbers = re.findall(r"\d+", layer)
    if numbers:
        layer_thk = int(numbers[-1])

    for idx, label in enumerate(thk_labels):
        if idx in used_labels:
            continue
        lx, ly = label["position"]
        if point_in_polygon(lx, ly, text_match_vertices):
            used_labels.add(idx)
            thickness_mm = label["thickness_mm"]
            if abs(thickness_mm - geom_thickness) > 10:
                warnings.append(
                    f"Wall on layer '{layer}': text label says {thickness_mm}mm but the drawn geometry "
                    f"measures {geom_thickness:.0f}mm; using the text label."
                )
            return thickness_mm, float(thickness_mm), "text_label", label["text"], warnings

    # No text label inside this polygon: trust what's actually drawn over the layer name.
    thickness_mm = round(geom_thickness / 5.0) * 5  # nearest 5mm -- standard construction increment
    if layer_thk is not None and abs(layer_thk - geom_thickness) > 10:
        warnings.append(
            f"Wall on layer '{layer}': layer name implies {layer_thk}mm but the drawn geometry measures "
            f"{geom_thickness:.0f}mm; using the measured geometry."
        )
    label_text = f"(measured from drawing: {geom_thickness:.1f}mm)"
    return thickness_mm, geom_thickness, "geometry", label_text, warnings


def _decompose_bent_wall(vertices, layer, thk_labels, used_labels):
    """
    Uses medial axis to get true centerline legs for bent/L/T walls.
    Returns a list of leg dicts, or [] if no legs could be found.
    """
    centerlines = extract_centerline_from_polygon(vertices)
    if not centerlines:
        return []

    legs = []
    for (p1, p2), width in centerlines:
        length_m = distance(p1, p2) / 1000.0
        if length_m < 0.1:  # skip tiny spurs
            continue
        centroid = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
        angle_deg = line_angle(p1, p2)
        
        # Resolve thickness (same logic as straight walls)
        thickness_mm, thickness_raw_mm, source, label_text, warnings = _resolve_thickness_and_warnings(
            width, layer, vertices, thk_labels, used_labels
        )
        legs.append({
            "p1": p1, "p2": p2,
            "centroid": centroid, "angle_deg": angle_deg, "length_m": length_m,
            "thickness_mm": thickness_mm, "thickness_raw_mm": thickness_raw_mm,
            "source": source, "label_text": label_text, "warnings": warnings,
        })
    return legs


def extract_walls(msp, str_wall_layers: list[str], ns_wall_layers: list[str],
                  scale: float = 1.0) -> list[WallEntry]:
    all_layers = list(str_wall_layers) + list(ns_wall_layers)
    if not all_layers:
        return []
    polys = extract_closed_polygons(msp, all_layers)
    scale_polygon_dicts(polys, scale)
    thk_labels = _find_thk_labels(msp, scale)
    used_labels: set[int] = set()
    str_set = set(str_wall_layers)

    entries = []
    str_count = 1
    ns_counters: dict[int, int] = {}

    def build_entry(name, layer, is_structural, thickness_mm, thickness_raw_mm, length_m, angle_deg,
                     centroid, p1, p2, vertices, source, label_text, warnings):
        area_sqm = (sqmm_to_sqm(polygon_area_sqmm(vertices)) if vertices
                    else length_m * thickness_mm / 1000.0)
        return WallEntry(
            name=name, layer=layer, is_structural=is_structural, thickness_mm=thickness_mm,
            thickness_raw_mm=round(thickness_raw_mm, 1), thickness_source=source, label_text=label_text,
            area_sqm=round(area_sqm, 3), length_m=round(length_m, 3), angle_deg=round(angle_deg, 2),
            centroid=(round(centroid[0], 1), round(centroid[1], 1)),
            p1=p1, p2=p2,
            vertices=vertices or [], warnings=warnings,
        )

    # GROUP POLYGONS THAT TOUCH/NEAR-TOUCH (form T/L/I systems)
    poly_groups = _group_wall_polygons(polys, max_gap=WALL_POLYGON_GROUP_GAP_MM)
    
    for group in poly_groups:
        if len(group) == 1:
            # Single polygon - existing logic
            poly = group[0]
            vertices = poly["vertices"]
            layer = poly["layer"]
            is_structural = layer in str_set

            rect = min_bounding_rectangle(vertices)
            bbox_area = rect["width"] * rect["length"]
            fill_ratio = (polygon_area_sqmm(vertices) / bbox_area) if bbox_area > 0 else 1.0

            if fill_ratio >= BENT_WALL_FILL_RATIO_THRESHOLD:
                thickness_mm, thickness_raw_mm, source, label_text, warnings = _resolve_thickness_and_warnings(
                    rect["width"], layer, vertices, thk_labels, used_labels
                )
                p1, p2 = _endpoints_from_rect(rect["center"], rect["angle_deg"], rect["length"])
                if is_structural:
                    name = f"SW-{str_count}"
                    str_count += 1
                else:
                    ns_counters[thickness_mm] = ns_counters.get(thickness_mm, 0) + 1
                    name = f"NSW{thickness_mm}-{ns_counters[thickness_mm]}"
                entries.append(build_entry(
                    name, layer, is_structural, thickness_mm, thickness_raw_mm, rect["length"] / 1000.0,
                    rect["angle_deg"], rect["center"], p1, p2, vertices, source, label_text, warnings,
                ))
                continue

            legs = _decompose_bent_wall(vertices, layer, thk_labels, used_labels)
            if not legs:
                # Couldn't decompose a polygon that looked bent -- fall back to
                # the single-rectangle measurement (with its implausibility
                # warning) rather than silently dropping this wall.
                thickness_mm, thickness_raw_mm, source, label_text, warnings = _resolve_thickness_and_warnings(
                    rect["width"], layer, vertices, thk_labels, used_labels
                )
                warnings = warnings + [
                    f"Wall on layer '{layer}': fill ratio {fill_ratio:.2f} suggests a bent/compound polygon, but "
                    f"no straight legs could be decomposed; drawn as a single rectangle -- verify manually."
                ]
                p1, p2 = _endpoints_from_rect(rect["center"], rect["angle_deg"], rect["length"])
                if is_structural:
                    name = f"SW-{str_count}"
                    str_count += 1
                else:
                    ns_counters[thickness_mm] = ns_counters.get(thickness_mm, 0) + 1
                    name = f"NSW{thickness_mm}-{ns_counters[thickness_mm]}"
                entries.append(build_entry(
                    name, layer, is_structural, thickness_mm, thickness_raw_mm, rect["length"] / 1000.0,
                    rect["angle_deg"], rect["center"], p1, p2, vertices, source, label_text, warnings,
                ))
                continue

            if is_structural:
                base_name = f"SW-{str_count}"
                str_count += 1
            else:
                key_thickness = legs[0]["thickness_mm"]
                ns_counters[key_thickness] = ns_counters.get(key_thickness, 0) + 1
                base_name = f"NSW{key_thickness}-{ns_counters[key_thickness]}"

            suffixes = "abcdefghijklmnopqrstuvwxyz"
            for idx, leg in enumerate(legs):
                suffix = suffixes[idx] if idx < len(suffixes) else str(idx)
                entries.append(build_entry(
                    f"{base_name}{suffix}", layer, is_structural, leg["thickness_mm"], leg["thickness_raw_mm"],
                    leg["length_m"], leg["angle_deg"], leg["centroid"],
                    leg["p1"], leg["p2"],
                    None, leg["source"], leg["label_text"],
                    leg["warnings"],
                ))
        else:
            # MULTIPLE POLYGONS FORMING A WALL SYSTEM (T, L, cross, etc.)
            # Extract combined centerline network
            centerlines = _extract_centerline_from_polygon_group(group)
            
            # All polygons in group share the same layer (by extraction logic)
            layer = group[0]["layer"]
            is_structural = layer in str_set
            
            # Resolve thickness once for the group (use first polygon for text matching)
            thickness_mm, thickness_raw_mm, source, label_text, warnings = _resolve_thickness_and_warnings(
                group[0]["vertices"], layer, group[0]["vertices"], thk_labels, used_labels
            )
            
            # Each centerline becomes a wall leg
            if is_structural:
                base_name = f"SW-{str_count}"
                str_count += 1
            else:
                key_thickness = thickness_mm
                ns_counters[key_thickness] = ns_counters.get(key_thickness, 0) + 1
                base_name = f"NSW{key_thickness}-{ns_counters[key_thickness]}"

            suffixes = "abcdefghijklmnopqrstuvwxyz"
            for idx, ((p1, p2), width) in enumerate(centerlines):
                length_m = distance(p1, p2) / 1000.0
                if length_m < 0.1:
                    continue
                centroid = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
                angle_deg = line_angle(p1, p2)
                suffix = suffixes[idx] if idx < len(suffixes) else str(idx)
                
                entries.append(build_entry(
                    f"{base_name}{suffix}", layer, is_structural, thickness_mm, thickness_raw_mm,
                    length_m, angle_deg, centroid,
                    p1, p2,
                    None, source, label_text,
                    warnings,
                ))

    return entries
