import ezdxf
import math
from dataclasses import asdict

def export_clean_dxf(parsed: dict, out_path: str, log=lambda m: None):
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    
    # Pre-define some colors for clarity
    COLOR_BEAM = 3     # Green
    COLOR_WALL_STR = 4 # Cyan
    COLOR_WALL_NS = 8  # Gray
    COLOR_COL = 1      # Red
    COLOR_SLAB = 6     # Magenta
    
    def _ensure_layer(name, color):
        if name not in doc.layers:
            doc.layers.add(name=name, color=color)
            
    # Beams -> LINE
    beams = parsed.get("beams", [])
    for b in beams:
        if b.warnings or b.width_mm <= 0 or b.depth_mm <= 0 or b.length_m <= 0:
            continue
        layer_name = f"Beam_{int(b.width_mm)}x{int(b.depth_mm)}"
        _ensure_layer(layer_name, COLOR_BEAM)
        msp.add_line(b.p1, b.p2, dxfattribs={"layer": layer_name})
        
    # Walls -> LINE
    walls = parsed.get("walls", [])
    for w in walls:
        if w.warnings or w.thickness_mm <= 0 or w.length_m <= 0:
            continue
        prefix = "Wall" if w.is_structural else "Wall_NS"
        layer_name = f"{prefix}_{int(w.thickness_mm)}"
        _ensure_layer(layer_name, COLOR_WALL_STR if w.is_structural else COLOR_WALL_NS)
        msp.add_line(w.p1, w.p2, dxfattribs={"layer": layer_name})
        
    # Columns -> closed LWPOLYLINE (rectangle)
    columns = parsed.get("columns", [])
    for c in columns:
        if c.warnings or c.width_mm <= 0 or c.depth_mm <= 0:
            continue
        layer_name = f"Col_{int(c.width_mm)}x{int(c.depth_mm)}"
        _ensure_layer(layer_name, COLOR_COL)
            
        cx, cy = c.centroid
        w, d = c.width_mm, c.depth_mm
        ang = math.radians(c.angle_deg)
        cos_a = math.cos(ang)
        sin_a = math.sin(ang)
        
        # d is along angle_deg, w is perpendicular
        # half vectors
        dx = (d / 2.0) * cos_a
        dy = (d / 2.0) * sin_a
        # perpendicular is angle + 90
        wx = (w / 2.0) * -sin_a
        wy = (w / 2.0) * cos_a
        
        p1 = (cx + dx + wx, cy + dy + wy)
        p2 = (cx - dx + wx, cy - dy + wy)
        p3 = (cx - dx - wx, cy - dy - wy)
        p4 = (cx + dx - wx, cy + dy - wy)
        
        msp.add_lwpolyline([p1, p2, p3, p4], close=True, dxfattribs={"layer": layer_name})
        
    # Slabs -> closed LWPOLYLINE
    slabs = parsed.get("slabs", [])
    for s in slabs:
        if s.warnings or s.thickness_mm <= 0 or not s.vertices:
            continue
        layer_name = f"Slab_{int(s.thickness_mm)}"
        _ensure_layer(layer_name, COLOR_SLAB)
        msp.add_lwpolyline(s.vertices, close=True, dxfattribs={"layer": layer_name})
        
    doc.saveas(out_path)
    log(f"Exported clean centerline DXF to: {out_path}")
