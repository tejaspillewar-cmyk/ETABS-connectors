# %%
# =====================================================================
#  Fin_test.py -- Step-by-step ETABS data extraction
#  Run cells one by one (Ctrl+Enter) in VS Code to isolate issues.
# =====================================================================

# %%
# =================================================================
# CELL 1: CONNECT TO ETABS -- Find PID & attach
# =================================================================
import comtypes.client
import psutil

SapModel = None
myETABSObject = None

# --- Find ETABS processes ---
etabs_processes = [
    p.info for p in psutil.process_iter(['pid', 'name'])
    if p.info['name'] and 'ETABS' in p.info['name'].upper()
]
print(f"ETABS processes found: {etabs_processes}")

if not etabs_processes:
    raise RuntimeError("[FAIL] No running ETABS.exe process found. Open ETABS first!")

pid = etabs_processes[0]['pid']
print(f"Using PID: {pid}")

# --- Connect using the exact pattern that worked in fdr_tool.py ---
helper = comtypes.client.CreateObject('ETABSv1.Helper')
print(f"  Helper created: {helper}")

helper = helper.QueryInterface(comtypes.gen.ETABSv1.cHelper)
print(f"  Helper QI'd to cHelper: {helper}")

myETABSObject = helper.GetObjectProcess("CSI.ETABS.API.ETABSObject", pid)
print(f"  GetObjectProcess returned: {myETABSObject}")

if myETABSObject is None:
    print("[WARN] GetObjectProcess returned None. Trying GetObject (no PID)...")
    try:
        myETABSObject = helper.GetObject("CSI.ETABS.API.ETABSObject")
        print(f"  GetObject returned: {myETABSObject}")
    except Exception as e:
        print(f"  GetObject also failed: {e}")

if myETABSObject is None:
    raise RuntimeError(
        f"[FAIL] Could not attach to ETABS PID {pid}.\n"
        "  Make sure ETABS is fully loaded (not just the splash screen).\n"
        "  Try: File > Save in ETABS, then re-run this cell."
    )

myETABSObject = myETABSObject.QueryInterface(comtypes.gen.ETABSv1.cOAPI)
SapModel = myETABSObject.SapModel

filepath = SapModel.GetModelFilename()
print(f"\n[OK] Attached to ETABS!")
print(f"  Active file: {filepath or '(no file open)'}")


# %%
# =================================================================
# CELL 2: ELEMENT INVENTORY -- Beams, Columns, Walls, Floors, etc.
# =================================================================
print("=" * 60)
print("  MODEL ELEMENT INVENTORY")
print("=" * 60)

# --- Helper: parse GetNameList return ---
# ETABS COM returns vary by object type:
#   Some return (count, names_array)           e.g. FrameObj, AreaObj, Story
#   Some return (retcode, count, names_array)   e.g. RespCombo, PierLabel
# We detect by checking: if ret[0] is small (0 or 1) and len>=3, assume (retcode, count, names).
# If ret[0] is a large number, assume (count, names).
def parse_namelist(ret):
    """Parse GetNameList return tuple. Returns (success:bool, count:int, names:list)."""
    if ret is None or len(ret) < 2:
        return False, 0, []

    # Format A: (retcode=0, count, names_array) -- 3+ items, first is 0
    if len(ret) >= 3 and ret[0] == 0 and isinstance(ret[1], int):
        names = list(ret[2]) if ret[2] else []
        return True, ret[1], names

    # Format B: (count, names_array) -- ret[0] IS the count
    if isinstance(ret[0], int) and ret[0] > 0:
        names = list(ret[1]) if ret[1] else []
        return True, ret[0], names

    # Format A with error: retcode != 0
    if len(ret) >= 3 and isinstance(ret[0], int) and ret[0] != 0:
        return False, 0, []

    # Count = 0 (empty list, not an error)
    if ret[0] == 0:
        return True, 0, []

    return False, 0, []


# --- Basic counts ---
num_points = SapModel.PointObj.Count()
num_frames = SapModel.FrameObj.Count()
num_areas  = SapModel.AreaObj.Count()

print(f"  Nodes (Points):     {num_points}")
print(f"  Frame Elements:     {num_frames}")
print(f"  Area Elements:      {num_areas}")

# --- Classify frames into Beams / Columns / Braces ---
frame_counts = {'Column': 0, 'Beam': 0, 'Brace': 0, 'Other': 0}

try:
    ret_frames = SapModel.FrameObj.GetNameList(0, [])
    ok, n, frame_names = parse_namelist(ret_frames)
    if ok:
        print(f"\n  Classifying {n} frames...")
        for fname in frame_names:
            try:
                ret_orient = SapModel.FrameObj.GetDesignOrientation(fname)
                orient = ret_orient[0] if isinstance(ret_orient, (list, tuple)) else ret_orient
                if orient == 1:
                    frame_counts['Column'] += 1
                elif orient == 2:
                    frame_counts['Beam'] += 1
                elif orient == 3:
                    frame_counts['Brace'] += 1
                else:
                    frame_counts['Other'] += 1
            except Exception:
                frame_counts['Other'] += 1

        print(f"  Frame Breakdown:")
        print(f"    Columns:  {frame_counts['Column']}")
        print(f"    Beams:    {frame_counts['Beam']}")
        print(f"    Braces:   {frame_counts['Brace']}")
        if frame_counts['Other'] > 0:
            print(f"    Other:    {frame_counts['Other']}")
    else:
        print(f"\n  [WARN] FrameObj.GetNameList failed (raw={ret_frames[:3]}...)")
except Exception as e:
    print(f"\n  [WARN] Could not classify frames: {e}")

# --- Classify areas into Walls / Floors / Ramps / Other ---
area_counts = {'Wall': 0, 'Floor': 0, 'Ramp': 0, 'Other': 0}

try:
    ret_areas_cell2 = SapModel.AreaObj.GetNameList(0, [])
    ok, n, area_names_cell2 = parse_namelist(ret_areas_cell2)
    if ok:
        print(f"\n  Classifying {n} area elements...")
        for aname in area_names_cell2:
            try:
                ret_orient = SapModel.AreaObj.GetDesignOrientation(aname)
                orient = ret_orient[0] if isinstance(ret_orient, (list, tuple)) else ret_orient
                if orient == 1:
                    area_counts['Wall'] += 1
                elif orient == 2:
                    area_counts['Floor'] += 1
                elif orient == 3:
                    area_counts['Ramp'] += 1
                else:
                    area_counts['Other'] += 1
            except Exception:
                area_counts['Other'] += 1

        print(f"  Area Breakdown:")
        print(f"    Walls:    {area_counts['Wall']}")
        print(f"    Floors:   {area_counts['Floor']}")
        if area_counts['Ramp'] > 0:
            print(f"    Ramps:    {area_counts['Ramp']}")
        if area_counts['Other'] > 0:
            print(f"    Other:    {area_counts['Other']}")
    else:
        print(f"\n  [WARN] AreaObj.GetNameList failed")
except Exception as e:
    print(f"\n  [WARN] Could not classify areas: {e}")

# --- Stories ---
try:
    ret_stories = SapModel.Story.GetNameList(0, [])
    ok, n, story_names = parse_namelist(ret_stories)
    if ok:
        print(f"\n  Stories ({n}):")
        for s in story_names:
            print(f"    - {s}")
    else:
        print(f"\n  [WARN] Story.GetNameList failed")
except Exception as e:
    print(f"\n  [WARN] Could not get stories: {e}")

print("\n" + "=" * 60)


# %%
# =================================================================
# CELL 3: PIER DATA -- Labels, Section Properties, Geometry
# =================================================================
print("=" * 60)
print("  PIER DATA")
print("=" * 60)

# --- Step 3a: Get pier label names ---
pier_names = []
try:
    ret_piers = SapModel.PierLabel.GetNameList(0, [])
    ok, n, pier_names = parse_namelist(ret_piers)
    if ok:
        print(f"  [OK] Found {n} pier labels:")
        for p in pier_names:
            print(f"    - {p}")
    else:
        print(f"  [WARN] PierLabel.GetNameList returned: {ret_piers}")
except Exception as e:
    print(f"  [FAIL] PierLabel.GetNameList error: {e}")

# --- Step 3b: Get section properties for each pier ---
if pier_names:
    print(f"\n  --- Pier Section Properties ---")
    for p_name in pier_names:
        try:
            ret_sec = SapModel.PierLabel.GetSectionProperties(
                p_name, 0, [], [], [], [], [], [], [], [], [], [], [], [], [], [], []
            )
            # Debug: print raw return shape for the first pier
            if p_name == pier_names[0]:
                print(f"  [DEBUG] GetSectionProperties: len={len(ret_sec)}, ret[0]={ret_sec[0]}, ret[1]={ret_sec[1]}")

            if ret_sec is not None:
                # Detect format: (retcode=0, count, ...) vs (count, ...)
                if ret_sec[0] == 0 and isinstance(ret_sec[1], int):
                    # Format: (retcode, count, stories, ..., width_bot[6], thick_bot[7])
                    num_stories = ret_sec[1]
                    story_arr   = ret_sec[2]
                    width_bot   = ret_sec[6]
                    thick_bot   = ret_sec[7]
                else:
                    # Format: (count, stories, ..., width_bot[5], thick_bot[6])
                    num_stories = ret_sec[0]
                    story_arr   = ret_sec[1]
                    width_bot   = ret_sec[5]
                    thick_bot   = ret_sec[6]

                print(f"\n  Pier '{p_name}' -- {num_stories} story section(s):")
                for i in range(num_stories):
                    w_mm = float(width_bot[i])
                    t_mm = float(thick_bot[i])
                    print(f"    Story: {str(story_arr[i]):20s}  Width(d)={w_mm:.0f} mm  Thick(b)={t_mm:.0f} mm")
        except Exception as e:
            print(f"  [FAIL] Pier '{p_name}': {e}")

# --- Step 3c: Find area objects assigned to each pier ---
print(f"\n  --- Pier-to-Area Mapping ---")
pier_area_map = {}

try:
    ret_areas_c3 = SapModel.AreaObj.GetNameList(0, [])
    ok, n, all_area_names = parse_namelist(ret_areas_c3)
    if ok:
        for aname in all_area_names:
            try:
                ret_pl = SapModel.AreaObj.GetPier(aname)
                pier_label = None
                if isinstance(ret_pl, (list, tuple)):
                    for item in ret_pl:
                        if isinstance(item, str) and item.strip():
                            pier_label = item.strip()
                            break
                elif isinstance(ret_pl, str) and ret_pl.strip():
                    pier_label = ret_pl.strip()

                if pier_label:
                    pier_area_map.setdefault(pier_label, []).append(aname)
            except Exception:
                pass

        if pier_area_map:
            for plabel, areas in sorted(pier_area_map.items()):
                print(f"    Pier '{plabel}': {len(areas)} area(s)")
        else:
            print("    [WARN] No area objects have pier labels assigned.")
    else:
        print(f"    [WARN] AreaObj.GetNameList failed")
except Exception as e:
    print(f"    [FAIL] Error mapping piers to areas: {e}")


# --- Step 3d: Get Start/End Coordinates for each Pier ---
print(f"\n  --- Pier Coordinates (Centerlines) ---")
try:
    if not pier_area_map:
        print("    [WARN] No pier-to-area map available to extract coordinates.")
    else:
        for plabel, areas in sorted(pier_area_map.items()):
            try:
                # Get the points of the FIRST area object for this pier
                # (For multi-area piers, taking the first area is a simplified approach,
                # but we'll try to collect points from ALL areas for a better bounding box)
                xs, ys = [], []
                for aname in areas:
                    ret_pts = SapModel.AreaObj.GetPoints(aname, 0, [])
                    # format: (retcode, count, names) or (count, names)
                    ok, n_pts, pt_names = parse_namelist(ret_pts)
                    if ok:
                        for pt in pt_names:
                            ret_coord = SapModel.PointObj.GetCoordCartesian(pt, 0.0, 0.0, 0.0)
                            if ret_coord is not None and len(ret_coord) >= 4:
                                # Determine order: (retcode, x, y, z) vs (x, y, z, retcode)
                                if isinstance(ret_coord[0], int) and ret_coord[0] == 0:
                                    x, y = ret_coord[1], ret_coord[2]
                                else:
                                    x, y = ret_coord[0], ret_coord[1]
                                xs.append(float(x))
                                ys.append(float(y))

                if len(xs) >= 2:
                    x_min, x_max = min(xs), max(xs)
                    y_min, y_max = min(ys), max(ys)
                    dx = x_max - x_min
                    dy = y_max - y_min
                    
                    # Assume the longer dimension is the wall length (centerline)
                    if dx >= dy:
                        mid_y = (y_min + y_max) / 2
                        x1, y1 = x_min, mid_y
                        x2, y2 = x_max, mid_y
                        orient = "Horizontal"
                    else:
                        mid_x = (x_min + x_max) / 2
                        x1, y1 = mid_x, y_min
                        x2, y2 = mid_x, y_max
                        orient = "Vertical"
                        
                    length = max(dx, dy)
                    print(f"    Pier '{plabel}' ({orient}, L={length:.0f} mm):")
                    print(f"      Start Node: X={x1:.0f}, Y={y1:.0f}")
                    print(f"      End Node:   X={x2:.0f}, Y={y2:.0f}")
                else:
                    print(f"    Pier '{plabel}': Could not extract enough coordinates.")
            except Exception as e:
                print(f"    [FAIL] Pier '{plabel}' coordinate extraction failed: {e}")
except Exception as e:
    print(f"  [FAIL] Pier coordinate extraction error: {e}")

print("\n" + "=" * 60)
print("  CELL 3 COMPLETE")
print("=" * 60)

