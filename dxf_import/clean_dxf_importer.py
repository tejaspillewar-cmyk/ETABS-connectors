import ezdxf
import re
from .beam_extractor import BeamEntry
from .wall_slab_extractor import WallEntry, SlabEntry
from .column_extractor import ColumnEntry
from .geometry import (
    distance, line_angle, polygon_area_sqmm, sqmm_to_sqm, polygon_centroid,
    min_bounding_rectangle
)

def parse_clean_dxf(dxf_path: str, scale: float = 1.0, log=lambda m: None) -> dict:
    """
    Reads a clean centerline DXF and reconstructs the parsed geometry dict.
    Expected layer names:
      Beam_<W>x<D>
      Wall_<T>
      Wall_NS_<T>
      Col_<W>x<D>
      Slab_<T>
    """
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    
    parsed = {
        "beams": [],
        "walls": [],
        "columns": [],
        "slabs": []
    }
    
    beam_pattern = re.compile(r"^Beam_(\d+)[xX\*](\d+)$", re.IGNORECASE)
    wall_pattern = re.compile(r"^Wall_(\d+)$", re.IGNORECASE)
    wall_ns_pattern = re.compile(r"^Wall_NS_(\d+)$", re.IGNORECASE)
    col_pattern = re.compile(r"^Col_(\d+)[xX\*](\d+)$", re.IGNORECASE)
    slab_pattern = re.compile(r"^Slab_(\d+)$", re.IGNORECASE)

    beam_count = wall_count = wall_ns_count = col_count = slab_count = 0
    
    for entity in msp:
        layer = entity.dxf.layer
        
        # Slabs
        if entity.dxftype() in {"LWPOLYLINE", "POLYLINE"}:
            m = slab_pattern.match(layer)
            if m:
                if entity.is_closed or True: # Assuming closed
                    vertices = [(v[0]*scale, v[1]*scale) for v in entity.vertices()]
                    thk = int(m.group(1))
                    area = sqmm_to_sqm(polygon_area_sqmm(vertices))
                    cx, cy = polygon_centroid(vertices)
                    vol = area * (thk / 1000.0)
                    slab_count += 1
                    parsed["slabs"].append(SlabEntry(
                        name=f"S{slab_count}-{thk}",
                        layer=layer, thickness_mm=thk,
                        thickness_source="clean_dxf", label_text="",
                        area_sqm=round(area, 3), volume_cum=round(vol, 4),
                        centroid=(round(cx,1), round(cy,1)),
                        vertices=vertices, warnings=[]
                    ))
                continue
                
            m = col_pattern.match(layer)
            if m:
                w, d = int(m.group(1)), int(m.group(2))
                vertices = [(v[0]*scale, v[1]*scale) for v in entity.vertices()]
                cx, cy = polygon_centroid(vertices)
                rect = min_bounding_rectangle(vertices)
                area = sqmm_to_sqm(polygon_area_sqmm(vertices))
                col_count += 1
                parsed["columns"].append(ColumnEntry(
                    name=f"C{col_count}-{w}x{d}", layer=layer,
                    width_mm=w, depth_mm=d,
                    angle_deg=round(rect["angle_deg"], 2),
                    centroid=(round(cx,1), round(cy,1)),
                    area_sqm=round(area, 3),
                    dimension_source="clean_dxf", label_text="", warnings=[]
                ))
                continue
                
        # Lines
        if entity.dxftype() == "LINE":
            p1 = (entity.dxf.start.x * scale, entity.dxf.start.y * scale)
            p2 = (entity.dxf.end.x * scale, entity.dxf.end.y * scale)
            length = distance(p1, p2) / 1000.0
            cx, cy = (p1[0]+p2[0])/2, (p1[1]+p2[1])/2
            ang = line_angle(p1, p2)
            
            m = beam_pattern.match(layer)
            if m:
                w, d = int(m.group(1)), int(m.group(2))
                beam_count += 1
                parsed["beams"].append(BeamEntry(
                    name=f"B{beam_count}-{w}x{d}", layer=layer,
                    width_mm=w, depth_mm=d, length_m=round(length, 3),
                    centroid=(round(cx,1), round(cy,1)), angle_deg=round(ang,2),
                    p1=p1, p2=p2, dimension_source="clean_dxf", warnings=[]
                ))
                continue
                
            m_str = wall_pattern.match(layer)
            m_ns = wall_ns_pattern.match(layer)
            if m_str or m_ns:
                is_str = bool(m_str)
                thk = int(m_str.group(1)) if is_str else int(m_ns.group(1))
                if is_str:
                    wall_count += 1
                    name = f"W{wall_count}-{thk}"
                else:
                    wall_ns_count += 1
                    name = f"W_NS{wall_ns_count}-{thk}"
                    
                area = length * (thk / 1000.0)
                parsed["walls"].append(WallEntry(
                    name=name, layer=layer, is_structural=is_str,
                    thickness_mm=thk, thickness_raw_mm=float(thk),
                    thickness_source="clean_dxf", label_text="",
                    area_sqm=round(area, 3), length_m=round(length, 3),
                    angle_deg=round(ang, 2), centroid=(round(cx,1), round(cy,1)),
                    p1=p1, p2=p2, vertices=[], warnings=[]
                ))
                continue
                
    log(f"Imported clean DXF: {slab_count} slabs, {wall_count + wall_ns_count} walls, {beam_count} beams, {col_count} columns.")
    return parsed
