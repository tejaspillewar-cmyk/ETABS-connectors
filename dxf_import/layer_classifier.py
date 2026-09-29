"""Best-guess auto-classification of DXF layers into element-type buckets.

This is only a starting point for the LayerConfirmDialog -- the user always
reviews and can move layers between buckets before extraction runs, since
the real project's layer-naming convention isn't fixed/known in advance.
"""

BUCKET_KEYS = ("slabs", "str_walls", "ns_walls", "beams", "columns")

# Markers that flag a wall layer as non-structural when present alongside
# a wall keyword.
_NS_WALL_MARKERS = ("NS", "NON", "ARCH", "PARTITION")


def auto_classify_layers(all_layers: list[str]) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {key: [] for key in BUCKET_KEYS}

    for layer in all_layers:
        upper = layer.upper()

        # Slabs
        if "SLAB" in upper or "DECK" in upper:
            buckets["slabs"].append(layer)

        # Walls (structural vs non-structural)
        is_wall = "WALL" in upper or "SHEARWALL" in upper
        if is_wall:
            is_non_structural = any(marker in upper for marker in _NS_WALL_MARKERS)
            if is_non_structural:
                buckets["ns_walls"].append(layer)
            else:
                buckets["str_walls"].append(layer)

        # Beams
        if "BEAM" in upper or "LINTEL" in upper:
            buckets["beams"].append(layer)

        # Columns
        if "COL" in upper or "PIER" in upper:
            buckets["columns"].append(layer)

    return buckets
