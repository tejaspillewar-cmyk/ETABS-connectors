"""
Generic DXF entity extraction helpers.
Reads ezdxf entities and returns plain Python dicts/tuples -- no ezdxf objects
leak out of this module. Layer lists are always passed in explicitly by the
caller (from the LayerConfirmDialog selections) -- nothing here hardcodes a
project's layer-naming convention.
"""
import re

import ezdxf


# ── DXF unit detection ────────────────────────────────────────────────────

# $INSUNITS header variable → mm conversion factor.
_INSUNITS_TO_MM = {
    1: 25.4,     # Inches
    2: 304.8,    # Feet
    4: 1.0,      # Millimeters
    5: 10.0,     # Centimeters
    6: 1000.0,   # Meters
    14: 100.0,   # Decimeters
}


def detect_dxf_scale_to_mm(doc, msp=None) -> tuple[float, str]:
    """Detect drawing units from the DXF header ($INSUNITS).

    Falls back to a bounding-box heuristic when the header field is missing
    or set to 'unitless'.  Returns (scale_factor_to_mm, human_description).
    """
    try:
        insunits = doc.header.get('$INSUNITS', 0)
    except Exception:
        insunits = 0

    if insunits in _INSUNITS_TO_MM:
        factor = _INSUNITS_TO_MM[insunits]
        unit_names = {1: "inches", 2: "feet", 4: "mm", 5: "cm", 6: "meters", 14: "dm"}
        name = unit_names.get(insunits, f"unit#{insunits}")
        return factor, f"$INSUNITS={insunits} ({name})"

    # Heuristic fallback: sample the bounding box of modelspace geometry.
    if msp is None:
        return 1.0, "assumed mm (no header, no geometry)"

    xs, ys = [], []
    sample_count = 0
    for entity in msp:
        try:
            if entity.dxftype() == "LWPOLYLINE":
                for x, y in entity.get_points(format="xy"):
                    xs.append(x)
                    ys.append(y)
            elif entity.dxftype() == "LINE":
                xs.extend([entity.dxf.start.x, entity.dxf.end.x])
                ys.extend([entity.dxf.start.y, entity.dxf.end.y])
        except Exception:
            continue
        sample_count += 1
        if sample_count >= 5000:
            break

    if not xs:
        return 1.0, "assumed mm (no geometry found)"

    max_extent = max(max(xs) - min(xs), max(ys) - min(ys))
    if max_extent < 500:
        return 1000.0, f"heuristic: max extent {max_extent:.1f} < 500 → assumed meters"
    elif max_extent < 5000:
        return 10.0, f"heuristic: max extent {max_extent:.1f} < 5000 → assumed centimeters"
    else:
        return 1.0, f"heuristic: max extent {max_extent:.1f} ≥ 5000 → assumed mm"


# ── Coordinate scaling helpers ────────────────────────────────────────────

def scale_polygon_dicts(polys: list[dict], factor: float) -> list[dict]:
    """Scale vertex coordinates in extracted polygon dicts.  Mutates in place."""
    if factor == 1.0:
        return polys
    for p in polys:
        p["vertices"] = [(x * factor, y * factor) for x, y in p["vertices"]]
    return polys


def scale_line_dicts(lines: list[dict], factor: float) -> list[dict]:
    """Scale endpoint coordinates in extracted line dicts.  Mutates in place."""
    if factor == 1.0:
        return lines
    for ln in lines:
        ln["p1"] = (ln["p1"][0] * factor, ln["p1"][1] * factor)
        ln["p2"] = (ln["p2"][0] * factor, ln["p2"][1] * factor)
    return lines


def scale_text_dicts(texts: list[dict], factor: float) -> list[dict]:
    """Scale position coordinates in extracted text dicts.  Mutates in place."""
    if factor == 1.0:
        return texts
    for t in texts:
        t["position"] = (t["position"][0] * factor, t["position"][1] * factor)
    return texts


# ── Existing helpers (unchanged) ──────────────────────────────────────────

def load_dxf(filepath: str):
    """Load and validate a DXF file. Returns (doc, msp)."""
    doc = ezdxf.readfile(filepath)
    msp = doc.modelspace()
    return doc, msp


def get_layer_names(doc) -> list[str]:
    return [layer.dxf.name for layer in doc.layers]


def thickness_from_layer_name(layer: str) -> int | None:
    """Parse the trailing integer in a layer name, e.g. 'STR-SLAB-REG150' -> 150."""
    numbers = re.findall(r"\d+", layer)
    if numbers:
        return int(numbers[-1])
    return None


def extract_closed_polygons(msp, layers: list[str]) -> list[dict]:
    """
    Extract closed LWPOLYLINE entities on the given layers.

    Returns list of:
        {"layer": str, "vertices": [(x, y), ...], "handle": str,
         "thickness_from_layer": int | None}
    """
    polys = []
    layer_set = set(layers)
    for entity in msp:
        if entity.dxftype() != "LWPOLYLINE":
            continue
        layer = entity.dxf.get("layer", "")
        if layer not in layer_set:
            continue
        if not entity.closed:
            continue
        vertices = list(entity.get_points(format="xy"))
        polys.append({
            "layer": layer,
            "vertices": vertices,
            "handle": entity.dxf.get("handle", ""),
            "thickness_from_layer": thickness_from_layer_name(layer),
        })
    return polys


def extract_hatch_polygons(msp, layers: list[str]) -> list[dict]:
    """
    Extract the outer boundary loop of HATCH entities on the given layers.

    Returns list of:
        {"layer": str, "vertices": [(x, y), ...], "handle": str,
         "thickness_from_layer": int | None}
    """
    polys = []
    layer_set = set(layers)
    for entity in msp:
        if entity.dxftype() != "HATCH":
            continue
        layer = entity.dxf.get("layer", "")
        if layer not in layer_set:
            continue

        for path in entity.paths:
            vertices = _hatch_path_vertices(path)
            if len(vertices) < 3:
                continue
            polys.append({
                "layer": layer,
                "vertices": vertices,
                "handle": entity.dxf.get("handle", ""),
                "thickness_from_layer": thickness_from_layer_name(layer),
            })
            break  # only the first (outer) boundary loop per hatch
    return polys


def _hatch_path_vertices(path) -> list[tuple[float, float]]:
    """Flatten a HATCH boundary path (polyline or edge path) to (x, y) vertices."""
    # Polyline-type boundary path: has .vertices directly.
    if hasattr(path, "vertices"):
        return [(v[0], v[1]) for v in path.vertices]
    # Edge-type boundary path: made of line/arc/ellipse/spline edges.
    if hasattr(path, "edges"):
        vertices = []
        for edge in path.edges:
            if hasattr(edge, "start"):
                vertices.append((edge.start[0], edge.start[1]))
        return vertices
    return []


def extract_all_polygons(msp, layers: list[str]) -> list[dict]:
    """Convenience: closed LWPOLYLINEs + HATCH outer loops on the given layers."""
    return extract_closed_polygons(msp, layers) + extract_hatch_polygons(msp, layers)


def extract_text_entities(msp, layers: list[str] | None = None) -> list[dict]:
    """
    Extract TEXT and MTEXT entities, optionally filtered to `layers`.

    Returns list of:
        {"text": str, "layer": str, "position": (x, y)}
    """
    labels = []
    layer_set = set(layers) if layers is not None else None
    for entity in msp:
        dxftype = entity.dxftype()
        if dxftype not in ("TEXT", "MTEXT"):
            continue
        layer = entity.dxf.get("layer", "")
        if layer_set is not None and layer not in layer_set:
            continue
        text = (entity.dxf.text if dxftype == "TEXT" else entity.text).strip()
        if not text:
            continue
        pos = entity.dxf.insert
        labels.append({"text": text, "layer": layer, "position": (pos[0], pos[1])})
    return labels


def extract_lines(msp, layers: list[str]) -> list[dict]:
    """Extract LINE entities on the given layers. Returns [{"layer", "p1", "p2"}]."""
    lines = []
    layer_set = set(layers)
    for entity in msp:
        if entity.dxftype() != "LINE":
            continue
        layer = entity.dxf.get("layer", "")
        if layer not in layer_set:
            continue
        start, end = entity.dxf.start, entity.dxf.end
        lines.append({"layer": layer, "p1": (start.x, start.y), "p2": (end.x, end.y)})
    return lines
