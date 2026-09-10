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
                ret_orient = SapModel.FrameObj.GetDesignOrientation(fname, 0)
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
                ret_orient = SapModel.AreaObj.GetDesignOrientation(aname, 0)
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
story_pier_data = {}

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

            import math
            
            if ret_sec is not None:
                # Detect format: (retcode=0, count, ...) vs (count, ...)
                if ret_sec[0] == 0 and isinstance(ret_sec[1], int):
                    # Format: (retcode, NumberStories, StoryName, AxisAngle, NumAreaObjs, NumLineObjs, WidthBot, ThicknessBot, WidthTop, ThicknessTop, MatProp, CGBotX, CGBotY, CGBotZ, CGTopX, CGTopY, CGTopZ)
                    num_stories = ret_sec[1]
                    story_arr   = ret_sec[2]
                    axis_angle  = ret_sec[3]
                    width_bot   = ret_sec[6]
                    thick_bot   = ret_sec[7]
                    cg_bot_x    = ret_sec[11]
                    cg_bot_y    = ret_sec[12]
                else:
                    num_stories = ret_sec[0]
                    story_arr   = ret_sec[1]
                    axis_angle  = ret_sec[2]
                    width_bot   = ret_sec[5]
                    thick_bot   = ret_sec[6]
                    cg_bot_x    = ret_sec[10]
                    cg_bot_y    = ret_sec[11]

                print(f"\n  Pier '{p_name}' -- {num_stories} story section(s):")
                for i in range(num_stories):
                    w_mm = float(width_bot[i]) * 1000
                    t_mm = float(thick_bot[i]) * 1000
                    
                    cg_x = float(cg_bot_x[i])
                    cg_y = float(cg_bot_y[i])
                    ang_rad = math.radians(float(axis_angle[i]))
                    
                    # CGBot is the exact center of the pier in plan. WidthBot is the length of the wall.
                    # Start/End nodes are (WidthBot/2) away from the center along the axis angle.
                    x1 = cg_x - (float(width_bot[i]) / 2.0) * math.cos(ang_rad)
                    y1 = cg_y - (float(width_bot[i]) / 2.0) * math.sin(ang_rad)
                    
                    x2 = cg_x + (float(width_bot[i]) / 2.0) * math.cos(ang_rad)
                    y2 = cg_y + (float(width_bot[i]) / 2.0) * math.sin(ang_rad)
                    
                    story_name = str(story_arr[i])
                    story_pier_data.setdefault(story_name, []).append({
                        "label": p_name,
                        "cg_x": cg_x,
                        "cg_y": cg_y,
                        "length_m": float(width_bot[i]),
                        "thick_m": float(thick_bot[i]),
                        "angle_rad": ang_rad
                    })
                    
                    print(f"    Story: {story_name:20s}  Width(d)={w_mm:.0f} mm  Thick(b)={t_mm:.0f} mm")
                    print(f"           Start Node: X={x1:.3f}, Y={y1:.3f} | End Node: X={x2:.3f}, Y={y2:.3f}")
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
                ret_pl = SapModel.AreaObj.GetPier(aname, "")
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



# --- Step 4: Export to DXF via ezdxf ---
print(f"\n  --- Interactive DXF Generation ---")

# --- Console-based Story Selection (replaces the Listbox popup) ---
if not story_pier_data:
    print("    [WARN] No pier data collected. Skipping DXF export.")
else:
    available_stories = list(story_pier_data.keys())
    print("\n  Available stories:")
    for i, s in enumerate(available_stories, 1):
        print(f"    {i}. {s}")

    sel = input("\n  Enter story numbers to export (comma-separated, or 'all'): ").strip()

    if sel.lower() == "all":
        selected_stories = available_stories
    else:
        try:
            idxs = [int(x.strip()) - 1 for x in sel.split(",") if x.strip()]
            selected_stories = [available_stories[i] for i in idxs if 0 <= i < len(available_stories)]
        except ValueError:
            selected_stories = []

    if not selected_stories:
        print("  [INFO] No valid stories selected. Export aborted.")
    else:
        import tkinter as tk
        from tkinter import filedialog
        import os

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)

        default_dir = ""
        try:
            model_path = SapModel.GetModelFilepath()
            if model_path:
                default_dir = os.path.dirname(model_path)
        except:
            pass

        dxf_filename = filedialog.asksaveasfilename(
            parent=root, title="Save Pier Layout DXF", initialdir=default_dir,
            initialfile="pier_layout.dxf", defaultextension=".dxf",
            filetypes=[("DXF Files", "*.dxf"), ("All Files", "*.*")]
        )
        root.destroy()

        if not dxf_filename:
            print("  [INFO] DXF save cancelled.")
        else:
            try:
                print("  [DEBUG] Importing ezdxf and creating new document...")
                import ezdxf
                import math
                
                doc = ezdxf.new("R2010")
                msp = doc.modelspace()
                
                print("  [DEBUG] Processing stories and generating DXF entities...")
                
                for story_name in selected_stories:
                    piers = story_pier_data[story_name]
                    layer_name = f"Story_{story_name}".replace(" ", "_").replace("-", "_")
                    doc.layers.add(layer_name)
                    
                    for pier in piers:
                        cg_x = pier["cg_x"]
                        cg_y = pier["cg_y"]
                        L = pier["length_m"]
                        T = pier["thick_m"]
                        angle_rad = pier["angle_rad"]
                        label = pier["label"]
                        
                        # Calculate 4 corners of the rectangle
                        # Local X is along length, Local Y is along thickness
                        dx_L = (L / 2.0) * math.cos(angle_rad)
                        dy_L = (L / 2.0) * math.sin(angle_rad)
                        
                        dx_T = (T / 2.0) * math.cos(angle_rad + math.pi/2)
                        dy_T = (T / 2.0) * math.sin(angle_rad + math.pi/2)
                        
                        p1 = (cg_x - dx_L - dx_T, cg_y - dy_L - dy_T)
                        p2 = (cg_x + dx_L - dx_T, cg_y + dy_L - dy_T)
                        p3 = (cg_x + dx_L + dx_T, cg_y + dy_L + dy_T)
                        p4 = (cg_x - dx_L + dx_T, cg_y - dy_L + dy_T)
                        
                        # Draw closed polyline
                        msp.add_lwpolyline([p1, p2, p3, p4], format="xy", close=True, dxfattribs={"layer": layer_name})
                        
                        # Intelligent Text Placement
                        # Determine if vertical or horizontal (using degrees for simplicity)
                        deg = math.degrees(angle_rad) % 180
                        is_vertical = (45 < deg < 135)
                        
                        text_height = 0.5
                        offset = 0.5 # gap between pier edge and text
                        
                        from ezdxf.enums import TextEntityAlignment
                        if is_vertical:
                            # Place text to the right
                            txt_x = cg_x + (T / 2.0) + offset
                            txt_y = cg_y
                            align = TextEntityAlignment.MIDDLE_LEFT
                        else:
                            # Place text above
                            txt_x = cg_x
                            txt_y = cg_y + (T / 2.0) + offset
                            align = TextEntityAlignment.BOTTOM_CENTER
                            
                        text_ent = msp.add_text(label, dxfattribs={"layer": layer_name, "height": text_height})
                        text_ent.set_placement((txt_x, txt_y), align=align)
                        
                doc.saveas(dxf_filename)
                print(f"  [OK] Successfully saved DXF to: {dxf_filename}")
                
            except Exception as e:
                import traceback
                print(f"  [FAIL] Failed to generate DXF: {e}")
                traceback.print_exc()

print("\n" + "=" * 60)
print("  CELL 3 COMPLETE")
print("=" * 60)

# %%
