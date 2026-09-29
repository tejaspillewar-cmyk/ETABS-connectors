"""
Column extraction.

Convention (as described by the user):
  - Columns are drawn as a closed LWPOLYLINE rectangle, or a HATCH, on their
    own layer(s).
  - A nearby size label reads "C200x300", "C(200x300)", "C200*300", or
    "C(200*300)" where 200x300 = B x D (width x depth). The leading letter(s)
    and parentheses are cosmetic and not load-bearing for parsing -- we just
    look for "<digits> [x|X|*] <digits>" anywhere in the text.
  - When no text label is found, column dimensions are measured from the drawn
    geometry (via min_bounding_rectangle), so they are not skipped as 0×0.
  - The column's rotation angle in plan view is always extracted from the
    drawn geometry, regardless of how dimensions are sourced.
"""
import re
from dataclasses import dataclass, field

from .geometry import distance, polygon_area_centroid, polygon_area_sqmm, sqmm_to_sqm, min_bounding_rectangle, extract_centerline_from_polygon, line_angle
from .dxf_helpers import extract_all_polygons, extract_text_entities, scale_polygon_dicts, scale_text_dicts

COL_DIM_PATTERN = re.compile(r"(\d+)\s*[xX\*]\s*(\d+)")
MAX_LABEL_DISTANCE_MM = 3000


@dataclass
class ColumnEntry:
    name: str
    layer: str
    width_mm: float   # B (short side of cross-section)
    depth_mm: float   # D (long side of cross-section)
    angle_deg: float  # rotation of the long axis (depth direction) in plan, degrees
    centroid: tuple[float, float]
    area_sqm: float
    dimension_source: str  # "text_label" | "geometry" | "unmatched"
    label_text: str
    warnings: list[str] = field(default_factory=list)


def parse_column_text(text: str) -> tuple[str, float, float]:
    """Parses 'C200x300', 'C(200x300)', 'C200*300', 'C(200*300)' -> (name, B, D)."""
    match = COL_DIM_PATTERN.search(text)
    if not match:
        return "COL", 0.0, 0.0
    b, d = float(match.group(1)), float(match.group(2))
    prefix = text[:match.start()].strip(" (")
    name = prefix if prefix else "COL"
    return name, b, d


def _extract_column_texts(msp, scale: float = 1.0) -> list[dict]:
    texts = []
    raw_texts = extract_text_entities(msp)
    scale_text_dicts(raw_texts, scale)
    for label in raw_texts:
        name, b, d = parse_column_text(label["text"])
        if b > 0 and d > 0:
            texts.append({"raw": label["text"], "name": name, "width": b, "depth": d, "position": label["position"]})
    return texts


def extract_columns(msp, column_layers: list[str], scale: float = 1.0) -> list[ColumnEntry]:
    if not column_layers:
        return []
    footprints = extract_all_polygons(msp, column_layers)
    scale_polygon_dicts(footprints, scale)
    label_texts = _extract_column_texts(msp, scale)
    used_labels: set[int] = set()

    entries = []
    counter = 0
    for fp in footprints:
        # USE AREA CENTROID (not vertex average)
        cx, cy = polygon_area_centroid(fp["vertices"])
        area_sqm = sqmm_to_sqm(polygon_area_sqmm(fp["vertices"]))

        # TRY MEDIAL AXIS for true centerline + orientation
        centerlines = extract_centerline_from_polygon(fp["vertices"])
        
        if centerlines:
            # For columns, medial axis gives center point(s). Use longest segment's angle.
            longest = max(centerlines, key=lambda cl: distance(cl[0][0], cl[0][1]))
            (cl_p1, cl_p2), _ = longest
            angle_deg = line_angle(cl_p1, cl_p2)
            # Width/depth from MBR (more stable for section dims)
            rect = min_bounding_rectangle(fp["vertices"])
            geom_width = round(rect["width"] / 5.0) * 5
            geom_depth = round(rect["length"] / 5.0) * 5
        else:
            # Fallback to MBR
            rect = min_bounding_rectangle(fp["vertices"])
            angle_deg = rect["angle_deg"]
            geom_width = round(rect["width"] / 5.0) * 5
            geom_depth = round(rect["length"] / 5.0) * 5

        counter += 1
        if best_idx != -1 and best_dist <= MAX_LABEL_DISTANCE_MM:
            used_labels.add(best_idx)
            txt = label_texts[best_idx]
            entries.append(ColumnEntry(
                name=f"{txt['name']}-{counter}", layer=fp["layer"],
                width_mm=txt["width"], depth_mm=txt["depth"],
                angle_deg=round(angle_deg, 2),
                centroid=(round(cx, 1), round(cy, 1)), area_sqm=round(area_sqm, 3),
                dimension_source="text_label", label_text=txt["raw"],
            ))
        else:
            # Geometry fallback: measure dimensions from the drawn polygon
            # instead of reporting 0×0 and skipping the column entirely.
            if geom_width > 0 and geom_depth > 0:
                entries.append(ColumnEntry(
                    name=f"COL-{counter}", layer=fp["layer"],
                    width_mm=geom_width, depth_mm=geom_depth,
                    angle_deg=round(angle_deg, 2),
                    centroid=(round(cx, 1), round(cy, 1)), area_sqm=round(area_sqm, 3),
                    dimension_source="geometry",
                    label_text=f"(measured: {geom_width:.0f}×{geom_depth:.0f}mm)",
                ))
            else:
                entries.append(ColumnEntry(
                    name=f"COL-{counter}", layer=fp["layer"],
                    width_mm=0.0, depth_mm=0.0,
                    angle_deg=round(angle_deg, 2),
                    centroid=(round(cx, 1), round(cy, 1)), area_sqm=round(area_sqm, 3),
                    dimension_source="unmatched", label_text="",
                    warnings=[f"Column on layer '{fp['layer']}' near ({cx:.0f}, {cy:.0f}): "
                              f"no nearby size label and geometry measurement failed."],
                ))
    return entries
