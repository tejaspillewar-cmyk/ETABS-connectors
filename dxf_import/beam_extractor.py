"""
Beam extraction.

Convention (ported from the concrete-qty-takeoff-from-dxf reference tool):
  - Beams are NOT centerlines. They're drawn as either:
      (a) a closed LWPOLYLINE rectangle outline, or
      (b) a pair of parallel LINEs (the two long edges of the beam strip).
  - Width & depth come from a nearby TEXT/MTEXT label like "B 300X1350/850"
    or "PB1 230x450" (name, width, depth[/depth2...] -- max depth taken),
    matched to the nearest beam footprint whose geometric width is within
    tolerance. Depth is a vertical dimension invisible in plan view, so it
    can only ever come from text (or, as a fallback, the layer name).
  - Each text label is consumed at most once (used_labels tracking) and only
    matched within MAX_BEAM_LABEL_DISTANCE_MM of the beam centroid to prevent
    distant labels from mis-matching.
  - Explicit p1/p2 centerline endpoints are stored on BeamEntry so that the
    connectivity pass and ETABS writer can use them directly rather than
    re-deriving them from centroid + angle + length (which is lossy after
    snapping moves one endpoint more than the other).
"""
import re
from dataclasses import dataclass, field

from .geometry import distance, line_angle, pair_parallel_segments
from .dxf_helpers import (
    extract_closed_polygons, extract_lines, extract_text_entities,
    scale_polygon_dicts, scale_line_dicts, scale_text_dicts,
)

WIDTH_TOLERANCE_MM = 50
MIN_BEAM_LENGTH_MM = 500
MIN_BEAM_WIDTH_MM = 50
MAX_BEAM_WIDTH_MM = 1500
MAX_BEAM_LABEL_DISTANCE_MM = 5000


@dataclass
class VirtualBeam:
    p1: tuple[float, float]
    p2: tuple[float, float]
    calc_width: float
    calc_length: float
    is_rect: bool
    layer: str = ""


@dataclass
class BeamEntry:
    name: str
    layer: str
    width_mm: float
    depth_mm: float
    length_m: float
    centroid: tuple[float, float]
    angle_deg: float
    p1: tuple[float, float]
    p2: tuple[float, float]
    dimension_source: str  # "text_label" | "layer_name" | "unmatched"
    warnings: list[str] = field(default_factory=list)


def parse_dim_text(text: str, name_prefix_pattern: str = r"[A-Z]*") -> tuple[str, float, float]:
    """
    Parses strings like 'B 300X1350/850' or 'PB1 230x450' -> (name, width, depth).
    If multiple depths are given (stepped/haunched beam, separated by '/'),
    the maximum is taken.
    """
    text = text.upper().strip()
    parts = text.split()
    name = "BEAM"
    dims_str = ""

    if len(parts) >= 2:
        name = parts[0]
        dims_str = parts[1]
    elif len(parts) == 1:
        dims_str = parts[0]

    if "X" in dims_str:
        dim_parts = dims_str.split("X")
    elif "*" in dims_str:
        dim_parts = dims_str.split("*")
    else:
        return name, 0.0, 0.0

    if len(dim_parts) < 2:
        return name, 0.0, 0.0

    try:
        width = float(re.sub(r"[^0-9.]", "", dim_parts[0]))
        depth_str = dim_parts[1]
        depths = [float(re.sub(r"[^0-9.]", "", d)) for d in depth_str.split("/") if re.sub(r"[^0-9.]", "", d)]
        depth = max(depths) if depths else 0.0
        return name, width, depth
    except ValueError:
        return name, 0.0, 0.0


def _extract_dim_texts(msp, scale: float = 1.0) -> list[dict]:
    """All TEXT/MTEXT entities that look like a 'WxD' dimension label."""
    texts = []
    raw_texts = extract_text_entities(msp)
    scale_text_dicts(raw_texts, scale)
    for label in raw_texts:
        raw = label["text"]
        if "X" in raw.upper() or "*" in raw:
            name, w, d = parse_dim_text(raw)
            if w > 0 and d > 0:
                texts.append({"raw": raw, "name": name, "width": w, "depth": d, "position": label["position"]})
    return texts


def _build_virtual_beams(msp, beam_layers: list[str], scale: float = 1.0) -> list[VirtualBeam]:
    if not beam_layers:
        return []
    polys = extract_closed_polygons(msp, beam_layers)
    scale_polygon_dicts(polys, scale)
    lines = extract_lines(msp, beam_layers)
    scale_line_dicts(lines, scale)

    virtual_beams = []

    # Rectangles: assume the 4 polygon points form a rectangle.
    for poly in polys:
        pts = poly["vertices"]
        if len(pts) < 4:
            continue
        p0, p1, p2, p3 = pts[0], pts[1], pts[2], pts[3]
        d1, d2 = distance(p0, p1), distance(p1, p2)
        if d1 > d2:
            length, width = d1, d2
            cl_p1 = ((p0[0] + p3[0]) / 2, (p0[1] + p3[1]) / 2)
            cl_p2 = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
        else:
            length, width = d2, d1
            cl_p1 = ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2)
            cl_p2 = ((p2[0] + p3[0]) / 2, (p2[1] + p3[1]) / 2)
        virtual_beams.append(VirtualBeam(cl_p1, cl_p2, width, length, is_rect=True, layer=poly["layer"]))

    # Parallel-line pairs: the two long edges of a beam strip.
    long_lines = [ln for ln in lines if distance(ln["p1"], ln["p2"]) >= MIN_BEAM_LENGTH_MM]
    segments = [(ln["p1"], ln["p2"]) for ln in long_lines]
    for pair in pair_parallel_segments(segments, MIN_BEAM_WIDTH_MM, MAX_BEAM_WIDTH_MM):
        i, _ = pair["source_indices"]
        virtual_beams.append(VirtualBeam(
            pair["p1"], pair["p2"], pair["width"], pair["overlap_length"], is_rect=False,
            layer=long_lines[i]["layer"],
        ))

    return virtual_beams


def extract_beams(msp, beam_layers: list[str], scale: float = 1.0) -> list[BeamEntry]:
    virtual_beams = _build_virtual_beams(msp, beam_layers, scale)
    dim_texts = _extract_dim_texts(msp, scale)

    used_labels: set[int] = set()
    entries = []
    for vb in virtual_beams:
        cx = (vb.p1[0] + vb.p2[0]) / 2
        cy = (vb.p1[1] + vb.p2[1]) / 2
        length_m = vb.calc_length / 1000.0
        angle = line_angle(vb.p1, vb.p2)

        best_idx, best_dist = -1, float("inf")
        for idx, txt in enumerate(dim_texts):
            if idx in used_labels:
                continue
            if abs(txt["width"] - vb.calc_width) < WIDTH_TOLERANCE_MM:
                d = distance((cx, cy), txt["position"])
                if d < best_dist and d <= MAX_BEAM_LABEL_DISTANCE_MM:
                    best_dist, best_idx = d, idx

        if best_idx != -1:
            used_labels.add(best_idx)
            best_txt = dim_texts[best_idx]
            entries.append(BeamEntry(
                name=best_txt["name"], layer=vb.layer,
                width_mm=best_txt["width"], depth_mm=best_txt["depth"],
                length_m=round(length_m, 3),
                centroid=(round(cx, 1), round(cy, 1)),
                angle_deg=round(angle, 2),
                p1=vb.p1, p2=vb.p2,
                dimension_source="text_label",
            ))
        else:
            name, w, d = parse_dim_text(vb.layer)
            warnings = []
            source = "layer_name"
            if w <= 0 or d <= 0:
                w, d = vb.calc_width, 0.0
                source = "unmatched"
                warnings.append(
                    f"Beam on layer '{vb.layer}' near ({cx:.0f}, {cy:.0f}): no matching WxD text label found; "
                    f"depth is unknown."
                )
            entries.append(BeamEntry(
                name=name if w > 0 else "BEAM", layer=vb.layer, width_mm=w, depth_mm=d,
                length_m=round(length_m, 3),
                centroid=(round(cx, 1), round(cy, 1)),
                angle_deg=round(angle, 2),
                p1=vb.p1, p2=vb.p2,
                dimension_source=source, warnings=warnings,
            ))
    return entries
