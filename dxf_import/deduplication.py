"""
Post-extraction deduplication.

DXF files (especially Revit exports) often represent the same structural
element as both an LWPOLYLINE outline and a HATCH fill on the same layer.
The extraction pipeline treats each as a separate element, producing
duplicates that would create overlapping objects in ETABS.

This module merges elements whose centroids coincide (within tolerance)
and whose dimensions match, keeping only one representative per group.
"""
from .geometry import distance


# Two elements are considered duplicates if their centroids are within this
# distance AND their dimensions match within tolerance.
CENTROID_TOLERANCE_MM = 50.0
DIMENSION_TOLERANCE_MM = 20.0


def deduplicate_parsed(parsed: dict, log=lambda m: None) -> dict:
    """Remove duplicate elements from the parsed result dict.

    Returns a new dict with duplicates removed.  The element with the most
    complete information (text_label > geometry > layer_name > unknown) is
    kept as the representative.
    """
    new_parsed = {}
    total_removed = 0

    for key in ("slabs", "walls", "beams", "columns"):
        entries = parsed.get(key, [])
        if not entries:
            new_parsed[key] = entries
            continue

        deduped, removed = _deduplicate_entries(entries, key)
        new_parsed[key] = deduped
        total_removed += removed
        if removed:
            log(f"Deduplication: removed {removed} duplicate {key}.")

    # Preserve any extra metadata keys (scale, origin_offset, etc.)
    for key in parsed:
        if key not in new_parsed:
            new_parsed[key] = parsed[key]

    if total_removed:
        log(f"Deduplication: {total_removed} total duplicate(s) removed.")
    else:
        log("Deduplication: no duplicates found.")

    return new_parsed


def _source_priority(entry) -> int:
    """Higher = more informative source, preferred when deduplicating."""
    source = ""
    if hasattr(entry, "thickness_source"):
        source = entry.thickness_source
    elif hasattr(entry, "dimension_source"):
        source = entry.dimension_source

    return {"text_label": 3, "geometry": 2, "layer_name": 1}.get(source, 0)


def _dimensions_match(a, b, key: str) -> bool:
    """Check if two entries of the same type have matching dimensions."""
    tol = DIMENSION_TOLERANCE_MM

    if key == "slabs":
        return abs(a.thickness_mm - b.thickness_mm) <= tol
    elif key == "walls":
        return (abs(a.thickness_mm - b.thickness_mm) <= tol and
                abs(a.length_m * 1000 - b.length_m * 1000) <= tol * 10)
    elif key == "beams":
        return (abs(a.width_mm - b.width_mm) <= tol and
                abs(a.depth_mm - b.depth_mm) <= tol)
    elif key == "columns":
        return (abs(a.width_mm - b.width_mm) <= tol and
                abs(a.depth_mm - b.depth_mm) <= tol)
    return False


def _deduplicate_entries(entries, key: str):
    """Deduplicate a list of entries.  Returns (deduped_list, count_removed)."""
    if len(entries) <= 1:
        return list(entries), 0

    used = set()
    groups = []

    for i, a in enumerate(entries):
        if i in used:
            continue
        group = [i]
        used.add(i)
        for j in range(i + 1, len(entries)):
            if j in used:
                continue
            b = entries[j]
            if (distance(a.centroid, b.centroid) <= CENTROID_TOLERANCE_MM and
                    _dimensions_match(a, b, key)):
                group.append(j)
                used.add(j)
        groups.append(group)

    # From each group, keep the entry with the best source priority.
    deduped = []
    removed = 0
    for group in groups:
        best_idx = max(group, key=lambda idx: _source_priority(entries[idx]))
        deduped.append(entries[best_idx])
        removed += len(group) - 1

    return deduped, removed
