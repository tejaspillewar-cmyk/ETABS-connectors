# %%
# =====================================================================
#  FDR AUTOMATION TOOL — Integrated with Etabs_live.py
#  Python port of FDR-tool-t2 (React/TypeScript)
#
#  This module connects to ETABS via Etabs_live.py, extracts pier
#  force data & geometry, and performs:
#    1. Required Pt% (steel percentage) calculation
#    2. 0.4 fck Capacity/Demand check
#    3. 0.2 fck Boundary (ductile detailing) check
#    4. CAD export script generation (AutoCAD PLINE + TEXT commands)
#    5. Excel report export
#
#  Usage:
#      # As an importable module:
#      from Etabs_live import connect_etabs
#      from fdr_tool import FDRTool, FDRConfig
#      _, SapModel = connect_etabs()
#      tool = FDRTool(SapModel)
#      results = tool.run_all()
#
#      # Or run directly:
#      python fdr_tool.py
#
#      # Or run cells interactively in VS Code (Ctrl+Enter on # % blocks)
# =====================================================================

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd


# %%
# =================================================================
# FDRConfig — All engineering constants in one place
# =================================================================

@dataclass
class FDRConfig:
    """Configuration for the FDR analysis.

    All magic numbers live here. When integrating with the GUI,
    create an FDRConfig from user inputs and pass it to FDRTool.
    """

    fy: int = 500
    """Steel yield strength (MPa)."""

    fck: int = 30
    """Default concrete grade (MPa). Used when pier-specific fck = 0."""

    text_height: int = 100
    """Text height for CAD exports."""

    cad_offset_1: int = 300
    """Label offset from pier center (mm)."""

    cad_offset_2: int = 200
    """Value drop-down from label (mm)."""

    cad_offset_3: int = 200
    """As value offset below Pt% value (mm)."""

    wind_keywords: list[str] = field(
        default_factory=lambda: ['gx', 'gwx', 'wx', 'wy', 'gy', 'gwy']
    )
    """Substrings that flag a load combination as wind-related."""

    story_filter: Optional[str] = None
    """Set to a story name string to filter, or None for all stories."""


# %%
# =================================================================
# Pure helper functions (no state, no side-effects)
# =================================================================

def parse_namelist(ret):
    """Parse GetNameList return tuple. Returns (success:bool, count:int, names:list)."""
    if ret is None or len(ret) < 2:
        return False, 0, []
    if len(ret) >= 3 and ret[0] == 0 and isinstance(ret[1], int):
        names = list(ret[2]) if ret[2] else []
        return True, ret[1], names
    if isinstance(ret[0], int) and ret[0] > 0:
        names = list(ret[1]) if ret[1] else []
        return True, ret[0], names
    if len(ret) >= 3 and isinstance(ret[0], int) and ret[0] != 0:
        return False, 0, []
    if ret[0] == 0:
        return True, 0, []
    return False, 0, []

def is_wind_combo(combo: str, wind_keywords: list[str]) -> bool:
    """Check if a load combination name contains any wind keyword."""
    if not combo:
        return False
    lower = combo.lower()
    return any(kw in lower for kw in wind_keywords)


def natural_sort_key(s: str):
    """Generate a sort key for natural (human-friendly) sorting.
    e.g. P2 < P10, Story 1 < Story 10."""
    parts = re.split(r'(\d+)', s)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def compute_pier_corners(x1, y1, x2, y2, b):
    """Compute 4 corners of a pier rectangle from centerline midpoints + thickness.
    Returns list of (x, y) tuples, or None if degenerate."""
    dx = x2 - x1
    dy = y2 - y1
    length = math.sqrt(dx * dx + dy * dy)
    if length == 0 or b == 0:
        return None
    nx = (-dy / length) * (b / 2)
    ny = (dx / length) * (b / 2)
    return [
        (x1 + nx, y1 + ny),
        (x2 + nx, y2 + ny),
        (x2 - nx, y2 - ny),
        (x1 - nx, y1 - ny),
    ]


def boxes_overlap(a: dict, b: dict) -> bool:
    """Check if two AABB bounding boxes overlap.
    Each box is a dict with keys: xMin, xMax, yMin, yMax."""
    return (a['xMin'] < b['xMax'] and a['xMax'] > b['xMin'] and
            a['yMin'] < b['yMax'] and a['yMax'] > b['yMin'])


# -- Steel area formulas (IS 456) --

def calc_As_min(b: float, d: float) -> float:
    """Minimum steel area: 0.25% of gross area."""
    return 0.0025 * b * d


def calc_Asc(Pmin: float, fck: float, b: float, d: float, fy: float) -> float:
    """Compression steel: Asc = (|Pmin|*1000 - 0.4*fck*b*d) / (0.67*fy)."""
    if Pmin >= 0:
        return 0.0
    raw = (abs(Pmin) * 1000 - 0.4 * fck * b * d) / (0.67 * fy)
    return max(0.0, raw)


def calc_Ast(Pmax: float, fy: float) -> float:
    """Tension steel: Ast = (Pmax * 1000) / (0.87 * fy)."""
    if Pmax <= 0:
        return 0.0
    return (Pmax * 1000) / (0.87 * fy)


def governing_case(row: pd.Series) -> str:
    """Determine which steel quantity governs the design."""
    if row['As_max'] == row['Asc'] and row['Asc'] > 0:
        return 'Compression'
    elif row['As_max'] == row['Ast'] and row['Ast'] > 0:
        return 'Tension'
    else:
        return 'Minimum'


# %%
# =================================================================
# FDRTool — Main analysis engine
# =================================================================

class FDRTool:
    """FDR (Flexural Design Review) automation for ETABS pier walls.

    This class accepts a live SapModel connection (from Etabs_live.py)
    and performs the full FDR pipeline: extraction → calculation → export.

    Parameters
    ----------
    SapModel : object
        A live ETABS SapModel COM object (from Etabs_live.connect_etabs()).
    config : FDRConfig, optional
        Engineering constants. Defaults to standard IS 456 values.

    Example
    -------
    >>> from Etabs_live import connect_etabs
    >>> from fdr_tool import FDRTool, FDRConfig
    >>> _, SapModel = connect_etabs()
    >>> tool = FDRTool(SapModel, FDRConfig(fck=35, fy=500))
    >>> results = tool.run_all()
    """

    def __init__(self, SapModel, config: FDRConfig | None = None, log=None):
        self.SapModel = SapModel
        self.config = config or FDRConfig()
        self.log = log or print

        # Internal DataFrames populated by extraction/calculation steps
        self._df_raw: pd.DataFrame = pd.DataFrame()
        self._df_data: pd.DataFrame = pd.DataFrame()
        self._df_calc: pd.DataFrame = pd.DataFrame()

    # -----------------------------------------------------------------
    # STEP 1: Extract Pier Forces
    # -----------------------------------------------------------------

    def get_available_combos(self) -> list[str]:
        """Return the list of response combination names defined in the model."""
        ret = self.SapModel.RespCombo.GetNameList(0, [])
        ok, n, names = parse_namelist(ret)
        if not ok:
            raise RuntimeError(
                f"RespCombo.GetNameList failed (raw={ret}). "
                "Check Define > Load Combinations in ETABS - this model may have "
                "zero response combinations defined."
            )
        return list(names)

    def extract_pier_forces(self, selected_combos: list[str] | None = None) -> pd.DataFrame:
        """Extract pier force results from ETABS and compute Pmin/Pmax envelopes.

        Pulls all load combination results, filters to bottom-location rows,
        and groups by (Story, Pier_ID) to find overall and non-wind envelopes.

        Returns
        -------
        pd.DataFrame
            Grouped data with columns: Story, Pier_ID, b, d, fck,
            Pmin, Combo_Pmin, Pmax, Combo_Pmax, and NoWind variants.
        """
        SM = self.SapModel
        cfg = self.config

        self.log("Extracting pier data from ETABS...")
        self.log("(Make sure your model is analyzed and results are available)")

        SM.Results.Setup.DeselectAllCasesAndCombosForOutput()

        available = self.get_available_combos()
        if not available:
            raise RuntimeError(
                "No response combinations are defined in this model. "
                "Define at least one under Define > Load Combinations before running FDR."
            )

        if selected_combos is None:
            combo_names = available
            self.log(f"  No combo selection given - using all {len(combo_names)} available combos.")
        else:
            invalid = [c for c in selected_combos if c not in available]
            if invalid:
                raise ValueError(f"These combo names don't exist in the model: {invalid}")
            combo_names = selected_combos
            self.log(f"  Using {len(combo_names)} user-selected combos out of {len(available)} available.")

        for combo_name in combo_names:
            SM.Results.Setup.SetComboSelectedForOutput(combo_name, True)

        # --- Get Pier Force results (no Name/ItemType args - returns ALL piers) ---
        ret = SM.Results.PierForce(
            0, [], [], [], [],
            [], [], [], [], [], []
        )
        if ret is None or len(ret) < 11:
            raise RuntimeError(f"Results.PierForce failed (raw={ret})")
            
        if ret[0] == 0 and isinstance(ret[1], int):
            num_results   = ret[1]
            story_names   = ret[2]
            pier_names    = ret[3]
            load_cases    = ret[4]
            locations     = ret[5]
            p_values      = ret[6]
            v2_values     = ret[7]
            v3_values     = ret[8]
            t_values      = ret[9]
            m2_values     = ret[10]
            m3_values     = ret[11]
        else:
            num_results   = ret[0]
            story_names   = ret[1]
            pier_names    = ret[2]
            load_cases    = ret[3]
            locations     = ret[4]
            p_values      = ret[5]
            v2_values     = ret[6]
            v3_values     = ret[7]
            t_values      = ret[8]
            m2_values     = ret[9]
            m3_values     = ret[10]

        self.log(f"  Extracted {num_results} pier force rows.")

        # --- Get Pier Section Properties ---
        ret_piers = SM.PierLabel.GetNameList(0, [])
        ok, n, pier_label_list = parse_namelist(ret_piers)
        if not ok:
            raise RuntimeError(f"PierLabel.GetNameList failed (raw={ret_piers})")

        pier_props = {}   # keyed by (pier_name, story_name) now, not just pier_name
        for p_name in pier_label_list:
            try:
                ret_sec = SM.PierLabel.GetSectionProperties(
                    p_name, 0, [], [], [], [], [], [], [], [], [], [], [], [], [], [], []
                )
                if ret_sec is not None and len(ret_sec) >= 7:
                    if ret_sec[0] == 0 and isinstance(ret_sec[1], int):
                        num_stories_for_pier = ret_sec[1]
                        story_name_arr = ret_sec[2]
                        width_bot_arr = ret_sec[6]
                        thick_bot_arr = ret_sec[7]
                    else:
                        num_stories_for_pier = ret_sec[0]
                        story_name_arr = ret_sec[1]
                        width_bot_arr = ret_sec[5]
                        thick_bot_arr = ret_sec[6]
                    for s_idx in range(num_stories_for_pier):
                        story_nm = str(story_name_arr[s_idx]).strip()
                        pier_props[(p_name, story_nm)] = {
                            'b': float(thick_bot_arr[s_idx]),
                            'd': float(width_bot_arr[s_idx]),
                            'fck': cfg.fck,
                        }
            except Exception as e:
                self.log(f"  Warning: Could not get section for pier {p_name}: {e}")

        # --- Build raw data DataFrame (bottom-location rows only) ---
        raw_rows = []
        for i in range(num_results):
            loc = str(locations[i]).strip() if locations[i] else ""
            if loc.lower() not in ('bottom', '0', ''):
                continue
            raw_rows.append({
                'Story': str(story_names[i]).strip(),
                'Pier_ID': str(pier_names[i]).strip(),
                'Combo': str(load_cases[i]).strip(),
                'P': float(p_values[i]),
            })

        self._df_raw = pd.DataFrame(raw_rows)
        self.log(f"  Filtered to {len(self._df_raw)} bottom-location rows.")

        # --- Group by (Story, Pier_ID) → Pmin/Pmax envelopes ---
        wind_kw = cfg.wind_keywords
        grouped_data = []

        for (story, pier_id), group in self._df_raw.groupby(['Story', 'Pier_ID']):
            props = pier_props.get((pier_id, story), {'b': 0, 'd': 0, 'fck': cfg.fck})
            sorted_g = group.sort_values('P')

            min_row = sorted_g.iloc[0]
            max_row = sorted_g.iloc[-1]

            non_wind = group[~group['Combo'].apply(
                lambda c: is_wind_combo(c, wind_kw)
            )]
            if len(non_wind) > 0:
                nw_sorted = non_wind.sort_values('P')
                min_row_nw = nw_sorted.iloc[0]
                max_row_nw = nw_sorted.iloc[-1]
            else:
                min_row_nw = min_row
                max_row_nw = max_row

            grouped_data.append({
                'Story': story,
                'Pier_ID': pier_id,
                'b': props['b'],
                'd': props['d'],
                'fck': props['fck'] if props['fck'] > 0 else cfg.fck,
                'Pmin': min_row['P'],
                'Combo_Pmin': min_row['Combo'],
                'Pmax': max_row['P'],
                'Combo_Pmax': max_row['Combo'],
                'Pmin_NoWind': min_row_nw['P'],
                'Combo_Pmin_NoWind': min_row_nw['Combo'],
                'Pmax_NoWind': max_row_nw['P'],
                'Combo_Pmax_NoWind': max_row_nw['Combo'],
                'x1': 0.0, 'y1': 0.0, 'x2': 0.0, 'y2': 0.0,
            })

        self._df_data = pd.DataFrame(grouped_data)

        # Natural sort by Pier_ID
        self._df_data['_sort_key'] = self._df_data['Pier_ID'].apply(natural_sort_key)
        self._df_data = (
            self._df_data
            .sort_values('_sort_key')
            .drop(columns='_sort_key')
            .reset_index(drop=True)
        )

        self.log(f"\nEXTRACTION COMPLETE: {len(self._df_data)} unique (Story, Pier) entries")
        self.log(f"  Stories: {self._df_data['Story'].nunique()} | "
              f"Piers: {self._df_data['Pier_ID'].nunique()}")
        self.log(self._df_data[
            ['Story', 'Pier_ID', 'b', 'd', 'fck', 'Pmin', 'Pmax']
        ].head(10).to_string(index=False))

        return self._df_data.copy()

    # -----------------------------------------------------------------
    # STEP 2: Extract Pier Coordinates
    # -----------------------------------------------------------------

    def extract_pier_coordinates(self) -> pd.DataFrame:
        SM = self.SapModel
        self.log("Extracting pier coordinates...")

        # --- Build a reverse lookup: pier name -> list of area object names ---
        ret_areas = SM.AreaObj.GetNameList(0, [])
        ok, n, all_area_names = parse_namelist(ret_areas)
        if not ok:
            raise RuntimeError(f"AreaObj.GetNameList failed (raw={ret_areas})")

        pier_to_areas: dict[str, list[str]] = {}
        for area_name in all_area_names:
            try:
                ret_pl = SM.AreaObj.GetPier(area_name, "")   # confirm exact signature before trusting - see note below
                pier_label = None
                if isinstance(ret_pl, (list, tuple)):
                    if len(ret_pl) >= 2 and ret_pl[0] == 0:
                        pier_label = ret_pl[1]
                    else:
                        for item in ret_pl:
                            if isinstance(item, str) and item.strip():
                                pier_label = item.strip()
                                break
                elif isinstance(ret_pl, str):
                    pier_label = ret_pl
                    
                if pier_label:
                        pier_to_areas.setdefault(pier_label, []).append(area_name)
            except Exception:
                pass

        for idx, row in self._df_data.iterrows():
            pier_id = row['Pier_ID']
            try:
                area_names = pier_to_areas.get(pier_id, [])
                if len(area_names) > 0:
                    ret_pts = SM.AreaObj.GetPoints(area_names[0], 0, [])
                    ok, n_pts, point_names = parse_namelist(ret_pts)
                    if ok:
                        xs, ys = [], []
                        for pt_name in point_names:
                            ret_coord = SM.PointObj.GetCoordCartesian(pt_name, 0.0, 0.0, 0.0)
                            if isinstance(ret_coord, (list, tuple)) and len(ret_coord) >= 3:
                                if ret_coord[0] == 0:
                                    xs.append(float(ret_coord[1]))
                                    ys.append(float(ret_coord[2]))
                                else:
                                    xs.append(float(ret_coord[0]))
                                    ys.append(float(ret_coord[1]))
                        if len(xs) >= 2:
                            x_min, x_max = min(xs), max(xs)
                            y_min, y_max = min(ys), max(ys)
                            dx = x_max - x_min
                            dy = y_max - y_min
                            if dx >= dy:
                                mid_y = (y_min + y_max) / 2
                                self._df_data.at[idx, 'x1'] = x_min
                                self._df_data.at[idx, 'y1'] = mid_y
                                self._df_data.at[idx, 'x2'] = x_max
                                self._df_data.at[idx, 'y2'] = mid_y
                            else:
                                mid_x = (x_min + x_max) / 2
                                self._df_data.at[idx, 'x1'] = mid_x
                                self._df_data.at[idx, 'y1'] = y_min
                                self._df_data.at[idx, 'x2'] = mid_x
                                self._df_data.at[idx, 'y2'] = y_max
            except Exception:
                pass

        coords_found = (
            (self._df_data['x1'] != 0) | (self._df_data['y1'] != 0)
        ).sum()
        self.log(f"Coordinates extracted for {coords_found}/{len(self._df_data)} piers.")
        if coords_found < len(self._df_data):
            self.log("  Piers without coordinates will be skipped in CAD export.")

        return self._df_data.copy()

    # -----------------------------------------------------------------
    # STEP 3: Run All Structural Calculations
    # -----------------------------------------------------------------

    def run_calculations(self) -> pd.DataFrame:
        """Execute all structural calculations on extracted data.

        Performs:
          1. Required Pt% (steel area) calculation
          2. 0.4 fck Capacity/Demand check (non-wind)
          3. 0.2 fck Boundary element check (non-wind)
          4. CAD label placement with collision avoidance

        Returns
        -------
        pd.DataFrame
            Full results DataFrame with all calculated columns.
        """
        cfg = self.config
        self.log("Running structural calculations...")

        # Apply story filter if set
        if cfg.story_filter:
            self._df_calc = self._df_data[
                self._df_data['Story'] == cfg.story_filter
            ].copy()
            self.log(f"  Filtered to story: {cfg.story_filter} "
                  f"({len(self._df_calc)} piers)")
        else:
            self._df_calc = self._df_data.copy()

        df = self._df_calc  # shorthand

        # ── 1. STEEL AREA CALCULATIONS ──────────────────────────────────

        df['As_min'] = df.apply(
            lambda r: calc_As_min(r['b'], r['d']), axis=1
        )
        df['Asc'] = df.apply(
            lambda r: calc_Asc(r['Pmin'], r['fck'], r['b'], r['d'], cfg.fy),
            axis=1,
        )
        df['Ast'] = df.apply(
            lambda r: calc_Ast(r['Pmax'], cfg.fy), axis=1
        )
        df['As_max'] = df[['As_min', 'Asc', 'Ast']].max(axis=1)
        df['Pt_percent'] = (df['As_max'] * 100) / (df['b'] * df['d'])
        df['Governing'] = df.apply(governing_case, axis=1)

        # ── 2. 0.4 fck C/D CHECK (Non-Wind Demands) ────────────────────

        df['Pu_capacity_04'] = (0.4 * df['fck'] * df['b'] * df['d']) / 1000
        df['Demand_04'] = df['Pmin_NoWind'].abs()
        df['CD_Ratio_04'] = df.apply(
            lambda r: (r['Pu_capacity_04'] / r['Demand_04']
                       if r['Demand_04'] > 0 else 0),
            axis=1,
        )
        df['Status_04'] = df['CD_Ratio_04'].apply(
            lambda x: 'Adequate' if x >= 1 else 'Inadequate'
        )

        # ── 3. 0.2 fck BOUNDARY CHECK (Non-Wind Demands) ───────────────

        df['Pu_02fck'] = (0.2 * df['fck'] * df['b'] * df['d']) / 1000
        df['Demand_02'] = df['Pmin_NoWind'].abs()
        df['CD_Ratio_02'] = df.apply(
            lambda r: (r['Pu_02fck'] / r['Demand_02']
                       if r['Demand_02'] > 0 else 0),
            axis=1,
        )
        df['Ductile_Detailing_Reqd'] = df['CD_Ratio_02'].apply(
            lambda x: 'Yes' if x < 1 else 'No'
        )

        # ── 4. CAD LABEL PLACEMENT (collision avoidance) ────────────────

        self._place_cad_labels(df)

        # ── Summary ─────────────────────────────────────────────────────

        summary = self.get_summary()
        self.log(f"\n{'='*55}")
        self.log(f"  CALCULATION SUMMARY")
        self.log(f"{'='*55}")
        self.log(f"  Total Piers Analyzed:   {summary['total_piers']}")
        self.log(f"  Max Pt%:                {summary['max_pt']:.2f}%")
        self.log(f"  0.4fck Inadequate:      {summary['inadequate_04']}")
        self.log(f"  Ductile Detailing Reqd: {summary['ductile_reqd']}")
        self.log(f"{'='*55}")

        self._df_calc = df
        return self._df_calc.copy()

    def _place_cad_labels(self, df: pd.DataFrame) -> None:
        """Compute label positions for CAD export using uniform spacing."""
        cfg = self.config
        
        # We will use cad_offset_1 as the gap from the pier edge to the text center.
        gap = cfg.cad_offset_1
        
        # Calculate bounding boxes of piers to avoid overlaps
        pier_boxes = []
        for _, row in df.iterrows():
            corners = compute_pier_corners(
                row['x1'], row['y1'], row['x2'], row['y2'], row['b']
            )
            if corners is None:
                continue
            xs = [c[0] for c in corners]
            ys = [c[1] for c in corners]
            pier_boxes.append({
                'id': f"{row['Story']}_{row['Pier_ID']}",
                'xMin': min(xs), 'xMax': max(xs),
                'yMin': min(ys), 'yMax': max(ys),
            })
            
        placed_labels = []
        
        CHAR_W = 70
        TEXT_H = cfg.text_height
        # Text block is 4 lines (Label, Value, CD_04, CD_02, AsValue). 
        # Actually in export_cad, it exports 5 items, but typically spaced by cad_offset_2 and cad_offset_3.
        # It's roughly offset_2 down, then offset_3 down.

        for idx, row in df.iterrows():
            x1, y1, x2, y2, b = row['x1'], row['y1'], row['x2'], row['y2'], row['b']
            mid_x = (x1 + x2) / 2
            mid_y = (y1 + y2) / 2
            dx = abs(x2 - x1)
            dy = abs(y2 - y1)
            
            pier_key = f"{row['Story']}_{row['Pier_ID']}"
            other_boxes = [box for box in pier_boxes if box['id'] != pier_key]
            
            is_horiz = (dy < dx) or (y1 == y2)
            
            # The edge of the pier from the center:
            edge_x = max(dx / 2, b / 2) if not is_horiz else b / 2
            edge_y = max(dy / 2, b / 2) if is_horiz else b / 2
            
            # Try to place text block 
            if is_horiz:
                # Primary: Below
                candidates = [
                    (mid_x, mid_y - edge_y - gap),
                    (mid_x, mid_y + edge_y + gap),
                    (mid_x + edge_x + gap, mid_y),
                    (mid_x - edge_x - gap, mid_y),
                ]
            else:
                # Primary: Right
                candidates = [
                    (mid_x + edge_x + gap, mid_y),
                    (mid_x - edge_x - gap, mid_y),
                    (mid_x, mid_y - edge_y - gap),
                    (mid_x, mid_y + edge_y + gap),
                ]
                
            max_text_len = max(len(str(row['Pier_ID'])), 6)
            text_half_w = (max_text_len * CHAR_W) / 2
            
            def make_label_box(lx, ly):
                # Total block goes from ly down to ly - (cfg.cad_offset_2 + cfg.cad_offset_3)
                return {
                    'xMin': lx - text_half_w,
                    'xMax': lx + text_half_w,
                    'yMin': min(ly, ly - cfg.cad_offset_2 - cfg.cad_offset_3) - TEXT_H / 2,
                    'yMax': max(ly, ly - cfg.cad_offset_2 - cfg.cad_offset_3) + TEXT_H / 2,
                }
                
            label_x, label_y = candidates[0]
            placed = False
            
            for cx, cy in candidates:
                text_box = make_label_box(cx, cy)
                hits_pier = any(boxes_overlap(text_box, pbox) for pbox in other_boxes)
                hits_label = any(boxes_overlap(text_box, lbox) for lbox in placed_labels)
                if not hits_pier and not hits_label:
                    label_x, label_y = cx, cy
                    placed_labels.append(text_box)
                    placed = True
                    break
                    
            if not placed:
                placed_labels.append(make_label_box(label_x, label_y))
                
            df.at[idx, 'labelX'] = label_x
            df.at[idx, 'labelY'] = label_y
            df.at[idx, 'valueX'] = label_x
            df.at[idx, 'valueY'] = label_y - cfg.cad_offset_2
            df.at[idx, 'asValueX'] = label_x
            df.at[idx, 'asValueY'] = label_y - cfg.cad_offset_2 - cfg.cad_offset_3

    # -----------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------

    def get_summary(self) -> dict:
        """Return summary statistics from the calculation results.

        Returns
        -------
        dict
            Keys: total_piers, max_pt, inadequate_04, ductile_reqd,
            tension_count, compression_count, minimum_count.
        """
        df = self._df_calc
        return {
            'total_piers': len(df),
            'max_pt': df['Pt_percent'].max() if len(df) > 0 else 0.0,
            'inadequate_04': int((df['Status_04'] == 'Inadequate').sum()),
            'ductile_reqd': int(
                (df['Ductile_Detailing_Reqd'] == 'Yes').sum()
            ),
            'tension_count': int((df['Governing'] == 'Tension').sum()),
            'compression_count': int((df['Governing'] == 'Compression').sum()),
            'minimum_count': int((df['Governing'] == 'Minimum').sum()),
        }

    # -----------------------------------------------------------------
    # Print detailed results (matches original Cells 7-9)
    # -----------------------------------------------------------------

    def print_pt_results(self) -> None:
        """Print the Required Pt% analysis table (Cell 7)."""
        df = self._df_calc
        pt_cols = [
            'Story', 'Pier_ID', 'b', 'd', 'fck',
            'Pmin', 'Combo_Pmin', 'Pmax', 'Combo_Pmax',
            'As_min', 'Asc', 'Ast', 'As_max', 'Pt_percent', 'Governing',
        ]

        self.log("=" * 100)
        self.log("  OBJECTIVE 1: REQUIRED Pt% ANALYSIS")
        self.log("  Asc = compression steel (from Pmin) | "
              "Ast = tension steel (from Pmax)")
        self.log(f"  As_min = 0.0025 x b x d | As_max = max(Asc, Ast, As_min) "
              f"| fy = {self.config.fy} MPa")
        self.log("=" * 100)

        display = df[pt_cols].copy()
        display['Pt_percent'] = display['Pt_percent'].round(3)
        for col in ('As_min', 'Asc', 'Ast', 'As_max'):
            display[col] = display[col].round(0)
        for col in ('Pmin', 'Pmax'):
            display[col] = display[col].round(1)
        self.log(display.to_string(index=False))

        tension = df[df['Governing'] == 'Tension']
        compression = df[df['Governing'] == 'Compression']
        minimum = df[df['Governing'] == 'Minimum']
        self.log(f"\n  Governed by Tension:     {len(tension)}")
        self.log(f"  Governed by Compression: {len(compression)}")
        self.log(f"  Governed by Minimum:     {len(minimum)}")
        if len(tension) > 0:
            self.log(f"  Max Pt% (Tension):       {tension['Pt_percent'].max():.2f}%")
        if len(compression) > 0:
            self.log(f"  Max Pt% (Compression):   "
                  f"({compression['Pt_percent'].max():.2f}%)")

    def print_04fck_results(self) -> None:
        """Print the 0.4 fck C/D check table (Cell 8)."""
        df = self._df_calc
        cd04_cols = [
            'Story', 'Pier_ID', 'Pu_capacity_04', 'Demand_04',
            'Combo_Pmin_NoWind', 'CD_Ratio_04', 'Status_04',
        ]

        self.log("=" * 100)
        self.log("  OBJECTIVE 2: 0.4 fck CAPACITY/DEMAND CHECK")
        self.log("  Pu_capacity = 0.4 x fck x b x d / 1000  |  "
              "Demand = |Pmin_NoWind|")
        self.log("  Status: Adequate if C/D >= 1.0")
        self.log("=" * 100)

        display = df[cd04_cols].copy()
        display['Pu_capacity_04'] = display['Pu_capacity_04'].round(1)
        display['Demand_04'] = display['Demand_04'].round(1)
        display['CD_Ratio_04'] = display['CD_Ratio_04'].round(2)
        self.log(display.to_string(index=False))

        inadequate = (df['Status_04'] == 'Inadequate').sum()
        self.log(f"\n  ADEQUATE:   {(df['Status_04'] == 'Adequate').sum()}")
        self.log(f"  INADEQUATE: {inadequate}")

        if inadequate > 0:
            self.log("\n  INADEQUATE PIERS:")
            inad = df[df['Status_04'] == 'Inadequate'][
                ['Story', 'Pier_ID', 'CD_Ratio_04', 'Demand_04',
                 'Pu_capacity_04']
            ]
            self.log(inad.to_string(index=False))

    def print_02fck_results(self) -> None:
        """Print the 0.2 fck Boundary check table (Cell 9)."""
        df = self._df_calc
        cd02_cols = [
            'Story', 'Pier_ID', 'Demand_02', 'Combo_Pmin_NoWind',
            'Pu_02fck', 'CD_Ratio_02', 'Ductile_Detailing_Reqd',
        ]

        self.log("=" * 100)
        self.log("  OBJECTIVE 3: 0.2 fck BOUNDARY CHECK (DUCTILE DETAILING)")
        self.log("  Pu_02fck = 0.2 x fck x b x d / 1000  |  "
              "Demand = |Pmin_NoWind|")
        self.log("  Ductile detailing required if C/D < 1.0")
        self.log("=" * 100)

        display = df[cd02_cols].copy()
        display['Demand_02'] = display['Demand_02'].round(1)
        display['Pu_02fck'] = display['Pu_02fck'].round(1)
        display['CD_Ratio_02'] = display['CD_Ratio_02'].round(2)
        self.log(display.to_string(index=False))

        ductile_reqd = (df['Ductile_Detailing_Reqd'] == 'Yes').sum()
        self.log(f"\n  Ductile Detailing NOT Required: "
              f"{(df['Ductile_Detailing_Reqd'] == 'No').sum()}")
        self.log(f"  Ductile Detailing REQUIRED:     {ductile_reqd}")

        if ductile_reqd > 0:
            self.log("\n  PIERS REQUIRING DUCTILE DETAILING:")
            duct = df[df['Ductile_Detailing_Reqd'] == 'Yes'][
                ['Story', 'Pier_ID', 'CD_Ratio_02', 'Demand_02', 'Pu_02fck']
            ]
            self.log(duct.to_string(index=False))

    # -----------------------------------------------------------------
    # EXPORT: CAD Scripts
    # -----------------------------------------------------------------

    def export_cad(self, output_dir: str | None = None) -> list[str]:
        """Generate AutoCAD .scr script files for all result categories.

        Parameters
        ----------
        output_dir : str, optional
            Directory to save .scr files. Defaults to ./cad_export.

        Returns
        -------
        list[str]
            Paths to the generated .scr files.
        """
        if output_dir is None:
            output_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "cad_export"
            )
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        df = self._df_calc
        generated_files = []

        def _save(name, rows):
            filename = f"FDR_{name}_{timestamp}.scr"
            filepath = os.path.join(output_dir, filename)
            with open(filepath, 'w') as f:
                f.write('\n'.join(rows))
            self.log(f"  {name}: {len(rows)} rows -> {filename}")
            generated_files.append(filepath)
            return filepath

        # --- Pier Labels ---
        label_rows = []
        for _, r in df.iterrows():
            if 'labelX' not in r or pd.isna(r.get('labelX', None)):
                continue
            label_rows.append(
                f"-text\t{r['labelX']:.1f},{r['labelY']:.1f}\t100\t0\t"
                f"{r['Pier_ID']}"
            )

        # --- Required Pt% ---
        pt_rows = []
        for _, r in df.iterrows():
            if 'valueX' not in r or pd.isna(r.get('valueX', None)):
                continue
            is_comp = r['As_max'] == r['Asc'] and r['Asc'] > 0
            pt_label = (f"({r['Pt_percent']:.2f}%)" if is_comp
                        else f"{r['Pt_percent']:.2f}%")
            pt_rows.append(
                f"-text\t{r['valueX']:.1f},{r['valueY']:.1f}\t100\t0\t"
                f"{pt_label}"
            )

        # --- 0.4 fck C/D ---
        cd04_rows = []
        for _, r in df.iterrows():
            if 'valueX' not in r or pd.isna(r.get('valueX', None)):
                continue
            cd04_rows.append(
                f"-text\t{r['valueX']:.1f},{r['valueY']:.1f}\t100\t0\t"
                f"{r['CD_Ratio_04']:.2f}"
            )

        # --- 0.2 fck C/D ---
        cd02_rows = []
        for _, r in df.iterrows():
            if 'valueX' not in r or pd.isna(r.get('valueX', None)):
                continue
            cd02_rows.append(
                f"-text\t{r['valueX']:.1f},{r['valueY']:.1f}\t100\t0\t"
                f"{r['CD_Ratio_02']:.2f}"
            )

        # --- As Required ---
        as_rows = []
        for _, r in df.iterrows():
            if 'asValueX' not in r or pd.isna(r.get('asValueX', None)):
                continue
            is_comp = r['As_max'] == r['Asc'] and r['Asc'] > 0
            as_label = (f"({r['As_max']:.0f})" if is_comp
                        else f"{r['As_max']:.0f}")
            as_rows.append(
                f"-text\t{r['asValueX']:.1f},{r['asValueY']:.1f}\t100\t0\t"
                f"{as_label}"
            )

        # --- Pier Rectangles ---
        rect_rows = []
        skipped_piers = []
        for _, r in df.iterrows():
            x1, y1, x2, y2, b = r['x1'], r['y1'], r['x2'], r['y2'], r['b']
            if x1 == 0 and y1 == 0 and x2 == 0 and y2 == 0:
                skipped_piers.append(r['Pier_ID'])
                continue
            if b == 0:
                skipped_piers.append(r['Pier_ID'])
                continue
            corners = compute_pier_corners(x1, y1, x2, y2, b)
            if corners is None:
                skipped_piers.append(r['Pier_ID'])
                continue
            c1, c2, c3, c4 = corners
            rect_rows.append(
                f"PLINE {c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} "
                f"{c3[0]:.1f},{c3[1]:.1f} {c4[0]:.1f},{c4[1]:.1f} C"
            )

        self.log(f"\n{'='*60}")
        self.log(f"  CAD EXPORT -- saved to: {output_dir}")
        self.log(f"{'='*60}")

        _save("PierLabels", label_rows)
        _save("RequiredPt", pt_rows)
        _save("CD_04fck", cd04_rows)
        _save("CD_02fck", cd02_rows)
        _save("As_Required", as_rows)
        _save("PierRectangles", rect_rows)

        if skipped_piers:
            self.log(f"\n  {len(skipped_piers)} pier(s) skipped for rectangles "
                  f"(no coords/thickness)")

        self.log(f"\nAll CAD scripts saved. "
              f"Paste contents into AutoCAD command line.")

        return generated_files

    # -----------------------------------------------------------------
    # EXPORT: Excel Report
    # -----------------------------------------------------------------

    def export_excel(self, output_path: str | None = None) -> str:
        """Export full analysis results to a multi-sheet Excel workbook.

        Parameters
        ----------
        output_path : str, optional
            Full path for the .xlsx file. If None, prompts via filedialog.

        Returns
        -------
        str
            Path to the generated Excel file.
        """
        try:
            import openpyxl
            from openpyxl.styles import PatternFill, Border, Side, Font
            from openpyxl.utils import get_column_letter
        except ImportError:
            self.log("Installing openpyxl for Excel export...")
            import subprocess
            subprocess.check_call(['pip', 'install', 'openpyxl'])
            import openpyxl
            from openpyxl.styles import PatternFill, Border, Side, Font
            from openpyxl.utils import get_column_letter

        if output_path is None:
            import tkinter as tk
            from tkinter import filedialog
            
            root = tk.Tk()
            root.withdraw()
            
            try:
                model_dir = os.path.dirname(self.SapModel.GetModelFilename())
            except Exception:
                model_dir = os.path.expanduser("~")
                
            output_path = filedialog.asksaveasfilename(
                title="Save FDR Excel Report",
                initialdir=model_dir,
                initialfile=self.default_excel_name(),
                defaultextension=".xlsx",
                filetypes=[("Excel workbook", "*.xlsx"), ("All files", "*.*")]
            )
            root.destroy()
            if not output_path:
                self.log("Excel export cancelled.")
                return ""

        df = self._df_calc
        export_cols = [
            'Story', 'Pier_ID', 'b', 'd', 'fck',
            'x1', 'y1', 'x2', 'y2',
            'Pmin', 'Combo_Pmin', 'Pmax', 'Combo_Pmax',
            'Pmin_NoWind', 'Combo_Pmin_NoWind',
            'Pmax_NoWind', 'Combo_Pmax_NoWind',
            'As_min', 'Asc', 'Ast', 'As_max', 'Pt_percent', 'Governing',
            'Pu_capacity_04', 'Demand_04', 'CD_Ratio_04', 'Status_04',
            'Pu_02fck', 'Demand_02', 'CD_Ratio_02', 'Ductile_Detailing_Reqd',
        ]
        export_cols = [c for c in export_cols if c in df.columns]

        # Prepare AutoCAD Commands data
        cad_rows = []
        if not df.empty:
            for _, r in df.iterrows():
                # Rectangle
                x1, y1, x2, y2, b = r['x1'], r['y1'], r['x2'], r['y2'], r['b']
                if not (x1 == 0 and y1 == 0 and x2 == 0 and y2 == 0) and b > 0:
                    corners = compute_pier_corners(x1, y1, x2, y2, b)
                    if corners is not None:
                        c1, c2, c3, c4 = corners
                        cad_rows.append({'Pier_ID': r['Pier_ID'], 'Type': 'Rectangle', 
                                        'Command': f"PLINE {c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {c3[0]:.1f},{c3[1]:.1f} {c4[0]:.1f},{c4[1]:.1f} C"})
                # Label
                if 'labelX' in r and not pd.isna(r['labelX']):
                    cad_rows.append({'Pier_ID': r['Pier_ID'], 'Type': 'Label', 
                                    'Command': f"-text\t{r['labelX']:.1f},{r['labelY']:.1f}\t100\t0\t{r['Pier_ID']}"})
                # Required Pt%
                if 'valueX' in r and not pd.isna(r['valueX']):
                    is_comp = r.get('As_max', 0) == r.get('Asc', -1) and r.get('Asc', 0) > 0
                    pt_label = f"({r['Pt_percent']:.2f}%)" if is_comp else f"{r['Pt_percent']:.2f}%"
                    cad_rows.append({'Pier_ID': r['Pier_ID'], 'Type': 'Required Pt%', 
                                    'Command': f"-text\t{r['valueX']:.1f},{r['valueY']:.1f}\t100\t0\t{pt_label}"})
                # As Required
                if 'asValueX' in r and not pd.isna(r['asValueX']):
                    is_comp = r.get('As_max', 0) == r.get('Asc', -1) and r.get('Asc', 0) > 0
                    as_label = f"({r['As_max']:.0f})" if is_comp else f"{r['As_max']:.0f}"
                    cad_rows.append({'Pier_ID': r['Pier_ID'], 'Type': 'As Required', 
                                    'Command': f"-text\t{r['asValueX']:.1f},{r['asValueY']:.1f}\t100\t0\t{as_label}"})

        df_cad = pd.DataFrame(cad_rows)

        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            df[export_cols].to_excel(writer, sheet_name='Full Analysis', index=False)
            df[['Story', 'Pier_ID', 'b', 'd', 'fck',
                'Pmin', 'Combo_Pmin', 'Pmax', 'Combo_Pmax',
                'As_min', 'Asc', 'Ast', 'As_max',
                'Pt_percent', 'Governing']].to_excel(writer, sheet_name='Required Pt', index=False)
            df[['Story', 'Pier_ID', 'Pu_capacity_04', 'Demand_04',
                'Combo_Pmin_NoWind', 'CD_Ratio_04', 'Status_04']].to_excel(writer, sheet_name='0.4fck CD Check', index=False)
            df[['Story', 'Pier_ID', 'Demand_02', 'Combo_Pmin_NoWind',
                'Pu_02fck', 'CD_Ratio_02',
                'Ductile_Detailing_Reqd']].to_excel(writer, sheet_name='0.2fck Boundary', index=False)
            if not df_cad.empty:
                df_cad.to_excel(writer, sheet_name='AutoCAD Commands', index=False)
            if len(self._df_raw) > 0:
                self._df_raw.to_excel(writer, sheet_name='Raw Envelope', index=False)

            # Apply Formatting
            workbook = writer.book
            thin_border = Border(left=Side(style='thin'), right=Side(style='thin'),
                                 top=Side(style='thin'), bottom=Side(style='thin'))
            header_fill = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")
            header_font = Font(bold=True)

            for sheet_name in workbook.sheetnames:
                ws = workbook[sheet_name]
                for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=ws.max_column):
                    for cell in row:
                        cell.border = thin_border
                        if cell.row == 1:
                            cell.fill = header_fill
                            cell.font = header_font
                
                # Auto-adjust column widths based on the length of column headers
                for col in ws.columns:
                    max_length = 0
                    column = col[0].column_letter
                    for cell in col:
                        try:
                            if len(str(cell.value)) > max_length:
                                max_length = len(str(cell.value))
                        except:
                            pass
                    adjusted_width = (max_length + 2)
                    ws.column_dimensions[column].width = adjusted_width

        self.log(f"Excel report saved: {output_path}")
        self.log("  Sheets: Full Analysis | Required Pt | 0.4fck CD Check | 0.2fck Boundary | AutoCAD Commands | Raw Envelope")

        return output_path

    def get_stories(self) -> list[str]:
        """Return a list of all story names defined in the model."""
        ret = self.SapModel.Story.GetNameList(0, [])
        ok, n, names = parse_namelist(ret)
        if not ok:
            return []
        return list(names)

    def stories_in_results(self) -> list[str]:
        """Return a list of stories present in the calculation results."""
        if self._df_calc is None or self._df_calc.empty:
            return []
        return self._df_calc['Story'].unique().tolist()

    def default_excel_name(self) -> str:
        """Default filename for Excel export."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        return f"FDR_Report_{timestamp}.xlsx"

    def default_dxf_name(self, story: str) -> str:
        """Default filename for DXF export."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        s_safe = "".join(c if c.isalnum() else "_" for c in story)
        return f"FDR_{s_safe}_{timestamp}.dxf"

    def export_dxf(self, filepath: str, story: str) -> str:
        """Export analysis results for a specific story to DXF using ezdxf.

        Parameters
        ----------
        filepath : str
            Full path for the .dxf file.
        story : str
            The name of the story to export.

        Returns
        -------
        str
            Path to the generated DXF file.
        """
        try:
            import ezdxf
        except ImportError:
            self.log("Installing ezdxf for DXF export...")
            import subprocess
            subprocess.check_call(['pip', 'install', 'ezdxf'])
            import ezdxf

        df = self._df_calc[self._df_calc['Story'] == story]
        if df.empty:
            self.log(f"No results found for story: {story}")
            return ""

        doc = ezdxf.new('R2010')
        doc.header['$INSUNITS'] = 4  # millimeters
        msp = doc.modelspace()

        # Add layers
        doc.layers.add(name="PierRectangles", color=ezdxf.colors.WHITE)
        doc.layers.add(name="PierLabels", color=ezdxf.colors.YELLOW)
        doc.layers.add(name="RequiredPt", color=ezdxf.colors.CYAN)
        doc.layers.add(name="AsRequired", color=ezdxf.colors.MAGENTA)
        
        TEXT_H = self.config.text_height

        for _, r in df.iterrows():
            # Pier Rectangle
            x1, y1, x2, y2, b = r['x1'], r['y1'], r['x2'], r['y2'], r['b']
            if not (x1 == 0 and y1 == 0 and x2 == 0 and y2 == 0) and b > 0:
                corners = compute_pier_corners(x1, y1, x2, y2, b)
                if corners is not None:
                    c1, c2, c3, c4 = corners
                    msp.add_lwpolyline([c1, c2, c3, c4], close=True, dxfattribs={'layer': 'PierRectangles'})

            # Label
            if 'labelX' in r and not pd.isna(r['labelX']):
                msp.add_text(str(r['Pier_ID']), dxfattribs={'layer': 'PierLabels', 'height': TEXT_H}).set_placement(
                    (r['labelX'], r['labelY']), align=ezdxf.enums.TextEntityAlignment.CENTER
                )

            # Required Pt%
            if 'valueX' in r and not pd.isna(r['valueX']):
                is_comp = r.get('As_max', 0) == r.get('Asc', -1) and r.get('Asc', 0) > 0
                pt_label = f"({r['Pt_percent']:.2f}%)" if is_comp else f"{r['Pt_percent']:.2f}%"
                msp.add_text(pt_label, dxfattribs={'layer': 'RequiredPt', 'height': TEXT_H}).set_placement(
                    (r['valueX'], r['valueY']), align=ezdxf.enums.TextEntityAlignment.CENTER
                )

            # As Required
            if 'asValueX' in r and not pd.isna(r['asValueX']):
                is_comp = r.get('As_max', 0) == r.get('Asc', -1) and r.get('Asc', 0) > 0
                as_label = f"({r['As_max']:.0f})" if is_comp else f"{r['As_max']:.0f}"
                msp.add_text(as_label, dxfattribs={'layer': 'AsRequired', 'height': TEXT_H}).set_placement(
                    (r['asValueX'], r['asValueY']), align=ezdxf.enums.TextEntityAlignment.CENTER
                )

        doc.saveas(filepath)
        self.log(f"DXF saved: {filepath}")
        return filepath

    # -----------------------------------------------------------------
    # RUN ALL — One-shot full pipeline
    # -----------------------------------------------------------------

    def run_all(self, selected_combos: list[str] | None = None, output_dir: str | None = None) -> dict:
        """Execute the complete FDR pipeline in one call.

        Sequence: extract forces → extract coords → calculate →
        print results → export CAD → export Excel.

        Parameters
        ----------
        selected_combos : list[str], optional
            Subset of combinations to extract, or None for all.
        output_dir : str, optional
            Base directory for exports. Defaults to script directory.

        Returns
        -------
        dict
            Keys: 'summary', 'df_results', 'cad_files', 'excel_path'.
        """
        # Step 1: Extract
        self.extract_pier_forces(selected_combos)

        # Step 2: Coordinates
        self.extract_pier_coordinates()

        # Step 3: Calculate
        self.run_calculations()

        # Step 4: Print detailed results
        self.print_pt_results()
        self.print_04fck_results()
        self.print_02fck_results()

        # Step 5: Export
        cad_files = self.export_cad(
            os.path.join(output_dir, "cad_export") if output_dir else None
        )
        excel_path = self.export_excel(
            os.path.join(output_dir, f"FDR_Report_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx")
            if output_dir else None
        )

        return {
            'summary': self.get_summary(),
            'df_results': self._df_calc.copy(),
            'cad_files': cad_files,
            'excel_path': excel_path,
        }


# %%
# =================================================================
# CELL 1: CONNECT TO ETABS (Standalone / Interactive)
# =================================================================
if __name__ == '__main__':
    import comtypes.client
    import psutil

    # 1. Find the running ETABS process
    etabs_processes = [
        p.info for p in psutil.process_iter(['pid', 'name'])
        if p.info['name'] and 'ETABS' in p.info['name'].upper()
    ]

    if not etabs_processes:
        raise RuntimeError("No running ETABS.exe process found.")
    elif len(etabs_processes) > 1:
        print("Multiple ETABS processes found:")
        for p in etabs_processes:
            print(f"  PID {p['pid']}  ({p['name']})")
        raise RuntimeError("Multiple ETABS instances running — specify the correct PID manually instead of auto-picking.")
    else:
        pid = etabs_processes[0]['pid']
        print(f"Found ETABS process: PID {pid}")

    # 2. Attach to that exact process
    helper = comtypes.client.CreateObject('ETABSv1.Helper')
    helper = helper.QueryInterface(comtypes.gen.ETABSv1.cHelper)

    myETABSObject = helper.GetObjectProcess("CSI.ETABS.API.ETABSObject", pid)

    if myETABSObject is None:
        raise RuntimeError(f"GetObjectProcess returned None for PID {pid} — attach failed.")

    myETABSObject = myETABSObject.QueryInterface(comtypes.gen.ETABSv1.cOAPI)
    SapModel = myETABSObject.SapModel

    print("Attached successfully. Active file:", SapModel.GetModelFilename())

    # =================================================================
    # CELL 1.5: CHECK AVAILABLE COMBOS
    # =================================================================
    config = FDRConfig(
        fy=500,
        fck=30,
        story_filter=None,  # Set to e.g. "Story 1" to filter
    )
    tool = FDRTool(SapModel, config)

    available = tool.get_available_combos()
    print(f"{len(available)} combos found:")
    for c in available:
        print(" ", c)

    # Example: If you only want to run specific combos, uncomment and list them:
    # my_combos = ["STD_G+40_ULS_D1a", "STD_G+40_ULS_D1b"]
    my_combos = None

    # =================================================================
    # CELL 2: RUN FDR ANALYSIS
    # =================================================================
    print("=" * 60)
    print("  FDR AUTOMATION TOOL — Running Analysis")
    print("=" * 60)

    results = tool.run_all(selected_combos=my_combos)

    print("\n" + "=" * 60)
    print("  ALL DONE!")
    print("=" * 60)
