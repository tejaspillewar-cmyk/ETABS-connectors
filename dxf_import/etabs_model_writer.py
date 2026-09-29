"""
Draws parsed DXF elements (walls/slabs/beams/columns) into a live ETABS
model as real objects with generated sections, organized into ETABS Groups.

Every call in here must run on the same COM-apartment thread that attached
SapModel -- submit draw_model() through etabs_worker.EtabsWorker, never call
it directly from a GUI thread.

All method calls and return-value shapes below were verified live against a
running ETABS 23 instance (blank/untitled model, safe to experiment on), not
guessed from documentation:
  - PropMaterial.SetMaterial / SetMPIsotropic / SetWeightAndMass: (0 = ok).
  - PropMaterial.SetOConcrete: rejected our parameters (returned 1). It's not
    required for elastic geometry/section behaviour -- SetMPIsotropic already
    carries E/Poisson/thermal -- so it's attempted best-effort and any
    failure is logged, never fatal.
  - PropFrame.SetRectangle / PropArea.SetWall / SetSlab: idempotent
    (define-if-absent, redefine-if-present), like the rest of the CSI OAPI's
    "Set" functions -- no existence pre-check needed.
  - FrameObj.AddByCoord returns [AssignedName, ret]; AreaObj.AddByCoord
    returns [X_out, Y_out, Z_out, AssignedName, ret] -- the coordinate
    arrays and Name are in/out, so pass '' for Name.
  - The interface is SapModel.GroupDef (not SapModel.Group). GroupDef.SetGroup
    works with just a name -- every other parameter has a default.
  - Story.GetStories_2() returns one flat tuple:
    (BaseElevation, NumberStories, StoryNames, StoryElevations, StoryHeights,
     IsMasterStory, SimilarToStory, SpliceAbove, SpliceHeight, Color, ret).
  - File.ImportFile(path, FileType, Type) returns 0 ("success") for every
    FileType tried (0, 1, 2, 9) against a real DXF, but creates zero objects
    in all cases -- it is NOT a working DXF-import path via the OAPI. Not
    implemented; see draw_model()'s docstring.

Changes since the initial version:
  - Walls/beams use stored p1/p2 directly instead of re-deriving them from
    centroid + angle + length (which was lossy after connectivity snapping).
  - Columns apply SetLocalAxes() to rotate the cross-section to match the
    drawn polygon's orientation in plan view.
  - An origin_offset parameter translates large DXF coordinates (e.g.
    survey/UTM) near the ETABS origin for numerical stability.
"""
import math
from dataclasses import dataclass, field

GROUP_STRUCTURAL = "DXF - Structural Model"
GROUP_ARCHITECTURE = "DXF - Architecture Reference"

UNITS_N_MM_C = 9        # eUnits_N_mm_C
MATTYPE_CONCRETE = 2    # eMatType_Concrete
WALLPROPTYPE_SPECIFIED = 1  # eWallPropType_Specified
SLABTYPE_SLAB = 0       # eSlabType_Slab
SHELLTYPE_THIN = 1      # eShellType_ShellThin
ITEMTYPE_OBJECTS = 0    # eItemType_Objects

CONCRETE_POISSON = 0.2
CONCRETE_THERMAL_COEFF = 5.5e-6   # per degree C
CONCRETE_UNIT_WEIGHT_N_PER_MM3 = 2.4e-5  # ~24 kN/m3, typical RCC


@dataclass
class DrawResult:
    drawn: dict = field(default_factory=lambda: {"slabs": 0, "walls": 0, "beams": 0, "columns": 0})
    skipped: dict = field(default_factory=lambda: {"slabs": [], "walls": [], "beams": [], "columns": []})
    notes: list = field(default_factory=list)


def _concrete_modulus_n_mm2(fck: float) -> float:
    """IS 456:2000 Cl. 6.2.3.1: Ec = 5000 * sqrt(fck), both in N/mm^2."""
    return 5000.0 * math.sqrt(fck)


def _ensure_material(SapModel, fck: float, log) -> str:
    name = f"DXF-C{int(fck)}"
    ret = SapModel.PropMaterial.SetMaterial(name, MATTYPE_CONCRETE, -1, "", "")
    if ret != 0:
        raise RuntimeError(f"Could not create material '{name}' (SetMaterial returned {ret}).")

    e_modulus = _concrete_modulus_n_mm2(fck)
    ret = SapModel.PropMaterial.SetMPIsotropic(name, e_modulus, CONCRETE_POISSON, CONCRETE_THERMAL_COEFF, 0)
    if ret != 0:
        raise RuntimeError(f"Could not set elastic properties for '{name}' (SetMPIsotropic returned {ret}).")

    ret = SapModel.PropMaterial.SetWeightAndMass(name, 1, CONCRETE_UNIT_WEIGHT_N_PER_MM3, 0)
    if ret != 0:
        log(f"Warning: could not set unit weight for '{name}' (non-fatal).")

    try:
        ret = SapModel.PropMaterial.SetOConcrete(name, float(fck), False, 0, 1, 0, 0, 0, 0, 0, 0)
        if ret != 0:
            log(f"Note: concrete design properties (SetOConcrete) were not accepted for '{name}' -- "
                f"elastic modeling is unaffected, but ETABS's concrete design checks may need this set "
                f"manually in Define > Material Properties.")
    except Exception as exc:
        log(f"Note: SetOConcrete failed for '{name}': {exc} (non-fatal, elastic modeling is unaffected).")

    log(f"Material '{name}': fck={fck:g} N/mm2, Ec={e_modulus:.0f} N/mm2.")
    return name


def _ensure_groups(SapModel, log):
    for group in (GROUP_STRUCTURAL, GROUP_ARCHITECTURE):
        ret = SapModel.GroupDef.SetGroup(group)
        if ret != 0:
            log(f"Warning: could not create/confirm group '{group}' (SetGroup returned {ret}).")


def _get_story_elevations(SapModel):
    """Get z_bottom and z_top for drawing from the model's story definitions.

    Returns (z_bottom, z_top, story_name) where z_bottom is the base of the
    target story and z_top is the top of that story.

    GetStories_2() returns:
    (BaseElevation, NumberStories, StoryNames, StoryElevations, StoryHeights, ..., ret).
    StoryElevations[i] = top of story i, StoryHeights[i] = height of story i.
    """
    result = SapModel.Story.GetStories_2()
    base_elevation = result[0]
    number_stories = result[1]
    story_names = result[2]
    elevations = result[3]
    heights = result[4]

    if not number_stories:
        raise RuntimeError("The active model has no stories defined -- add at least one story first.")

    # Use the lowest story by default.
    ground_index = min(range(number_stories), key=lambda i: elevations[i])
    z_top = float(elevations[ground_index])
    z_bottom = z_top - float(heights[ground_index])

    return z_bottom, z_top, story_names[ground_index]


def draw_model(SapModel, parsed: dict, fck: float,
               origin_offset: tuple[float, float] = (0.0, 0.0),
               log=print) -> DrawResult:
    """
    Draws parsed["slabs"/"walls"/"beams"/"columns"] (Phase 1's extracted
    dataclass lists) into SapModel as real objects, at the model's base/
    ground story. Skips any entry that carries extraction warnings or an
    unresolved (zero) dimension, recording why in the returned DrawResult.

    origin_offset: (ox, oy) subtracted from all XY coordinates before
    drawing, to bring large DXF coordinates (survey/UTM) near the ETABS
    origin for numerical stability. (0, 0) for no offset.

    Note on the "architecture background layer" idea: ETABS's File.ImportFile
    was tried live against a real ETABS instance with a real DXF and, despite
    returning a "success" code for every FileType tried, created zero objects
    in every case -- it is not a working path for bringing in a raw DXF as a
    background reference. That idea is dropped; "architecture" elements
    (non-structural walls) are instead drawn as real objects in the
    GROUP_ARCHITECTURE group, same as everything else, just filterable by
    group inside ETABS.
    """
    result = DrawResult()
    ox, oy = origin_offset
    original_units = SapModel.GetPresentUnits()
    try:
        ret = SapModel.SetPresentUnits(UNITS_N_MM_C)
        if ret != 0:
            raise RuntimeError(f"Could not switch ETABS to N-mm units (SetPresentUnits returned {ret}).")

        material = _ensure_material(SapModel, fck, log)
        _ensure_groups(SapModel, log)
        z_bottom, z_top, story_name = _get_story_elevations(SapModel)
        log(f"Drawing at story '{story_name}': z_bottom={z_bottom:g}mm, z_top={z_top:g}mm "
            f"(height={z_top - z_bottom:g}mm).")
        if ox != 0.0 or oy != 0.0:
            log(f"Origin offset applied: ({ox:g}, {oy:g}) mm.")

        _draw_slabs(SapModel, parsed.get("slabs", []), material, z_top, ox, oy, result, log)
        _draw_walls(SapModel, parsed.get("walls", []), material, z_bottom, z_top, ox, oy, result, log)
        _draw_beams(SapModel, parsed.get("beams", []), material, z_top, ox, oy, result, log)
        _draw_columns(SapModel, parsed.get("columns", []), material, z_bottom, z_top, ox, oy, result, log)

    finally:
        SapModel.SetPresentUnits(original_units)

    return result


def _draw_slabs(SapModel, slabs, material, z, ox, oy, result, log):
    for slab in slabs:
        if slab.warnings or slab.thickness_mm <= 0:
            result.skipped["slabs"].append((slab.name, slab.warnings or ["unresolved thickness"]))
            continue
        section = f"DXF-Slab-{slab.thickness_mm}"
        ret = SapModel.PropArea.SetSlab(section, SLABTYPE_SLAB, SHELLTYPE_THIN, material, float(slab.thickness_mm), -1, "")
        if ret != 0:
            result.skipped["slabs"].append((slab.name, [f"SetSlab returned {ret}"]))
            continue
        xs = [v[0] - ox for v in slab.vertices]
        ys = [v[1] - oy for v in slab.vertices]
        zs = [z] * len(slab.vertices)
        out = SapModel.AreaObj.AddByCoord(len(slab.vertices), xs, ys, zs, "", section, slab.name, "Global")
        ret = out[-1]
        if ret != 0:
            result.skipped["slabs"].append((slab.name, [f"AddByCoord returned {ret}"]))
            continue
        name = out[-2]
        SapModel.AreaObj.SetGroupAssign(name, GROUP_STRUCTURAL, False, ITEMTYPE_OBJECTS)
        result.drawn["slabs"] += 1


def _draw_walls(SapModel, walls, material, z_bottom, z_top, ox, oy, result, log):
    for wall in walls:
        if wall.warnings or wall.thickness_mm <= 0 or wall.length_m <= 0:
            result.skipped["walls"].append((wall.name, wall.warnings or ["unresolved dimensions"]))
            continue
        section = f"DXF-Wall-{wall.thickness_mm}"
        ret = SapModel.PropArea.SetWall(section, WALLPROPTYPE_SPECIFIED, SHELLTYPE_THIN, material,
                                        float(wall.thickness_mm), -1, "")
        if ret != 0:
            result.skipped["walls"].append((wall.name, [f"SetWall returned {ret}"]))
            continue
        # Use stored p1/p2 directly (already connectivity-snapped).
        p1x, p1y = wall.p1[0] - ox, wall.p1[1] - oy
        p2x, p2y = wall.p2[0] - ox, wall.p2[1] - oy
        xs = [p1x, p2x, p2x, p1x]
        ys = [p1y, p2y, p2y, p1y]
        zs = [z_bottom, z_bottom, z_top, z_top]
        out = SapModel.AreaObj.AddByCoord(4, xs, ys, zs, "", section, wall.name, "Global")
        ret = out[-1]
        if ret != 0:
            result.skipped["walls"].append((wall.name, [f"AddByCoord returned {ret}"]))
            continue
        name = out[-2]
        group = GROUP_STRUCTURAL if wall.is_structural else GROUP_ARCHITECTURE
        SapModel.AreaObj.SetGroupAssign(name, group, False, ITEMTYPE_OBJECTS)
        result.drawn["walls"] += 1


def _draw_beams(SapModel, beams, material, z, ox, oy, result, log):
    for beam in beams:
        if beam.warnings or beam.width_mm <= 0 or beam.depth_mm <= 0 or beam.length_m <= 0:
            result.skipped["beams"].append((beam.name, beam.warnings or ["unresolved dimensions"]))
            continue
        section = f"DXF-Beam-{beam.width_mm:.0f}x{beam.depth_mm:.0f}"
        ret = SapModel.PropFrame.SetRectangle(section, material, float(beam.depth_mm), float(beam.width_mm), -1, "", "")
        if ret != 0:
            result.skipped["beams"].append((beam.name, [f"SetRectangle returned {ret}"]))
            continue
        # Use stored p1/p2 directly (already connectivity-snapped).
        p1x, p1y = beam.p1[0] - ox, beam.p1[1] - oy
        p2x, p2y = beam.p2[0] - ox, beam.p2[1] - oy
        out = SapModel.FrameObj.AddByCoord(p1x, p1y, z, p2x, p2y, z, "", section, beam.name, "Global")
        ret = out[-1]
        if ret != 0:
            result.skipped["beams"].append((beam.name, [f"AddByCoord returned {ret}"]))
            continue
        name = out[-2]
        SapModel.FrameObj.SetGroupAssign(name, GROUP_STRUCTURAL, False, ITEMTYPE_OBJECTS)
        result.drawn["beams"] += 1


def _draw_columns(SapModel, columns, material, z_bottom, z_top, ox, oy, result, log):
    for col in columns:
        if col.warnings or col.width_mm <= 0 or col.depth_mm <= 0:
            result.skipped["columns"].append((col.name, col.warnings or ["unresolved dimensions"]))
            continue
        section = f"DXF-Col-{col.width_mm:.0f}x{col.depth_mm:.0f}"
        ret = SapModel.PropFrame.SetRectangle(section, material, float(col.depth_mm), float(col.width_mm), -1, "", "")
        if ret != 0:
            result.skipped["columns"].append((col.name, [f"SetRectangle returned {ret}"]))
            continue
        cx, cy = col.centroid[0] - ox, col.centroid[1] - oy
        out = SapModel.FrameObj.AddByCoord(cx, cy, z_bottom, cx, cy, z_top, "", section, col.name, "Global")
        ret = out[-1]
        if ret != 0:
            result.skipped["columns"].append((col.name, [f"AddByCoord returned {ret}"]))
            continue
        name = out[-2]
        SapModel.FrameObj.SetGroupAssign(name, GROUP_STRUCTURAL, False, ITEMTYPE_OBJECTS)

        # Apply column rotation: align the cross-section's depth direction
        # (local-3 axis) with the drawn polygon's long-axis angle.
        # For a vertical column, SetLocalAxes rotates local-2 (width direction)
        # by 'Ang' degrees CCW from its default (+X).  Since local-3 (depth) is
        # always 90° CCW from local-2, we set Ang = angle_deg - 90 so that
        # local-3 aligns with angle_deg.
        if col.angle_deg != 0.0:
            rotation_angle = col.angle_deg - 90.0
            try:
                SapModel.FrameObj.SetLocalAxes(name, rotation_angle, ITEMTYPE_OBJECTS)
            except Exception as exc:
                log(f"Warning: could not set local axes for column '{col.name}': {exc}")

        result.drawn["columns"] += 1
