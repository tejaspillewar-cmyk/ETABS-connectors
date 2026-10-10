# ETABS Report Automation: what we can extract, and what I need from you

Source material read: `etabs-report/README.md`, `parameters\\\\\\\_for\\\\\\\_report.md`, `docs/formula-inventory.md`,
`src/model/types.ts`, the two sample report images, and the ETABS API calls `ETABSConnector` already uses.

**Goal:** fill the report (`ReportData` in the `etabs-report` app) straight from a running ETABS model, so the
engineer only types what ETABS cannot know.

**How to answer:** write under each `\\\\\\\*\\\\\\\*Answer:\\\\\\\*\\\\\\\*` line. Each question has a *Proposed default*. Writing
`default` accepts it. Skip anything that does not apply to your projects and say so.

\---

## 1\. Automation matrix

* **Tier A**: fully automatic, no input needed.
* **Tier B**: automatic after a one-time mapping or rule (case names, ground story, etc.). The questions below pin these down.
* **Tier C**: manual, or the data lives outside ETABS.

|Report block|Parameter|Tier|How|
|-|-|-|-|
|Header|Project name, file path, date|A|`GetModelFilename`, project info, system date|
|Header|Engineer initials, revision, location|C|typed once; can be remembered as a preset|
|Building|Storey description (`5B+G+3Pr+23`)|B|derived from story names + a naming rule (Q2)|
|Building|Height above / below ground, typical floor height, chart elevations|B|`Story.GetStories`; needs "which story is ground" (Q2, Q3)|
|Building|Width B, Length L|B|bounding box of joints on a chosen story (Q4)|
|Building|ETABS total area, shear wall area|B|slab area / wall plan area (L x t) from area objects (Q5)|
|Building|Typical floor area|B or C|ETABS slab area, or a CAD number (Q5)|
|Building|Slenderness limit, note|C (constant)|IS 16700:2023 Table 2, static text|
|Seismic|Z, I, R, site class|B|read from the auto-seismic pattern or response-spectrum function if defined in ETABS (Q11)|
|Seismic|Zone, category, frame type, return period TR|C|typed text|
|Seismic|Seismic weight|B|mass tables / base reaction; depends on mass-source rule (Q10)|
|Seismic|Fundamental period T|B|rule needed (Q9)|
|Seismic|Ah,min|C|typed in both samples (Q11)|
|Base shear|EQ Vx, My, Vy, Mx|B|base reaction of a named EQ case or combo (Q6, Q7)|
|Base shear|Wind (your own cases)|B|base reaction of GWX / GWY (Q6, Q8)|
|Base shear|Wind (consultant / wind tunnel)|C|external document|
|Modal|Period, UX, UY, RZ, nature|A|modal participating mass ratios; nature = dominant of UX / UY / RZ|
|Gravity|DL, SDL, LL|B|base reaction Fz of the three load patterns (Q6)|
|Drift|Max drift and its story per case|A / B|story drifts, max over stories (Q13)|
|Drift|Limits (0.002 wind, 0.004 seismic)|B|rule (Q13)|
|Mass index|Column / Beam / Wall / Floor weight|A / B|"Material List by Object Type" table, or section area x length x 25 kN/m3 (Q17)|
|Mass index|Raft|B / C|depends on how the raft is modelled (Q17)|
|Torsion|4 joint IDs + 8 displacements each (32 numbers)|B|joint displacements at 4 corner joints x 4 cases; needs the point rule (Q14)|
|Checklist|Period difference >= 10%, torsion < 1.2|A|already calculated in the app|
|Checklist|Clear modes, drift check, deflection|B|automatable if you give rules (Q16); manual today|
|Chart|Ux, Uy vs elevation, H/500|B|story displacements for GWX / GWY (Q15)|
|Crack coefficients table|beams / columns / walls / floors, service / design|B or C|read section modifiers, or it is a fixed company table (Q18)|
|Images|ETABS plan with points 1-4|A / B|render from model geometry (Q19)|
|Images|CAD typical floor plan|C|you supply a DXF / PNG (Q19)|
|Footer|Company name / "Structural Design Review" / page|C|static template text|

**Rough scale (before your answers):** about 90% of the numeric cells are extractable. The 32 torsion numbers,
the story table and the modal rows dominate the count. After a one-time mapping per project, roughly 8 to 10
judgement fields stay manual (engineer, revision, location, zone / category / frame type, TR, Ah,min,
consultant wind, CAD image, plus anything left open below). I will finalise this at the end of the file once
you have answered.

\---

## 2\. What I noticed in the two samples (why some questions are asked)

1. **Seismic weight differs between samples.** Sample 2: DL + SDL + 0.25 LL = 534,716 + 208,394 + 21,775 =
764,885, which matches the 764,886 shown. Sample 1: the same formula gives about 1,144,000 but the sheet
shows 851,557. So a different rule (basements excluded?) was used.
2. **Fundamental period T** (corrected after your Q9 answer): both samples fit `T = 0.0672 h^0.75` with h = height
above ground: Sample 1 gives 2.386 s (shows 2.39), Sample 2 gives 3.167 s (shows 3.16). My earlier reading (mode 2
period / `0.09h/sqrt(d)`) was a coincidence.
3. **Mass index weights sum exactly to DL** in both samples, so they are the self-weight split by element type.
4. **Sample 2 has both an IS 1893:2016 and a :2025 column** (with return period TR = 975). The app is
2016-only by a v1 decision.
5. **Wind headers differ:** Sample 1 uses `Fx, Fy, Cx, Cy`; Sample 2 uses `Vxx, Vyx, Vxy, Vyy`. The app models
`Vx, My, Vy, Mx`.
6. **Drift table differs:** Sample 1 shows 4 cases (GWX, GWY, SpecX, SpecY) with a story each; Sample 2 shows 2
unlabeled "Seismic Drift" rows.
7. **EQ row:** Vx equals Vy in both samples (-11,678 / -11,678 and -8,373 / -8,373), which suggests a scaled
response-spectrum value.
8. **Crack coefficients table** appears in both samples but is not in `parameters\\\\\\\_for\\\\\\\_report.md`.

\---

## 3\. Questions

### A. Workflow and scale

**Q1. How will this be run?** (a) attach to an already-open, already-analysed model, as the current connector does,
or (b) batch over a folder of `.edb` files unattended? Which ETABS version(s)? Typical model size and analysis
time? Roughly how many models per week?
*Proposed default:* (a), ETABS 23, user has run the analysis.
**Answer: yes it will attach over the current connected ran model. and since its in json and our COM is in python, we may remake the report with python and use react for frontend or we make recreate the whole ui in pyhton itself.**

### B. Building properties

**Q2. Story naming and ground level.** How are basements, ground, podium, provisional ("P"), parking and terrace
stories named in your models? Which story is elevation 0 for the chart? How is `5B+G+3Pr+23` / `G+50+2P+T`
counted (what is "Pr" vs "P", what is "T")? Can I derive it from story names, or should it stay typed?
*Proposed default:* ground story chosen once from a dropdown; description derived from story names where they
follow a pattern, otherwise typed.
**Answer Pr is provison and P is podium sometimes the user may forget and name P for both but Pr for provion is the standard from now on. but the story names are to extracted from the ETABS. if it is possible.**

**Q3. Heights.** "Height above ground" runs up to which level: the terrace slab, OHT / machine room, parapet?
"Typical floor height": the most common story height?
*Proposed default:* top of the highest story; typical = most common story height.
**Answer:yes. this also to be extracted from ETABS.**

**Q4. Width B and length L.** From which story (typical tower floor, or the podium)? Along global X / Y even for
rotated plans? Bounding box of joints, or something else?
*Proposed default:* bounding box of joints on the typical floor along global X (B) and Y (L).
**Answer: most cases typical only. but if it varys for more than 10% at any floor (except for OHT, LMRs) then it can be edited.**

**Q5. Areas.**

* "Typical floor area": from ETABS or from CAD? (Sample 1 shows 2,101 m2, Sample 2 shows 776.83 m2.)
* "ETABS total area": sum of slab area over all stories including basements and podium? Gross or net of openings?
* "Shear wall ETABS area": plan area (length x thickness) of all walls on the typical story only?

*Proposed default:* typical = ETABS slab area on the typical story; total = sum over all stories, gross; wall = typical story.
**Answer:default, default, default. (for wall area i used to take element weight of the walls from table and then divide it by 25 and floor height so i can get the wall area. if you can get it automated without this method then it is good). typical is the story that repeats or is same for most stories.** 

### C. Load cases and base forces

**Q6. Load-case and pattern names.** Are they standard across projects (e.g. `EQX, EQY, SPEC-X, SPEC-Y, GWX, GWY, DL, SDL, LL`) or different each time? Is a mapping screen, saved beside the model, acceptable?
*Proposed default:* mapping dialog with auto-guessed names, saved per model.
**Answer: yes they are the same. maybe the labels vary. max case is for max along wind case**

**Q7. EQ base-shear row.** Which case or combo gives Vx, Vy, My, Mx: static equivalent or scaled response spectrum?
Vx equals Vy in both samples: is that because the spectrum is scaled to a target value?
*Proposed default:* the scaled response-spectrum case selected in the mapping dialog.
**Answer: Vx and My is from EQX and Vy and Mx is from EQY. and no the Vx and VY can be equal in some cases and it is not scaling.**

**Q8. Wind columns.** What do `Fx, Fy, Cx, Cy` (Sample 1) and `Vxx, Vyx, Vxy, Vyy` (Sample 2) mean? My guess:
base shear in X and in Y under X-wind, and in X and Y under Y-wind. Which wind row comes from ETABS and which
(consultant / wind tunnel) is always typed? Should the app adopt one header set?
*Proposed default:* `Vxx, Vyx, Vxy, Vyy` from GWX / GWY; consultant row typed.
**Answer: fx is aong and Cy is across for GWX so the take is that, all the cases of GWX and GWY are extracted and then the max case of GWX gives Fx and Cy and GWY gives Fy and Cx.**

### D. Seismic parameters

**Q9. Fundamental period T.** Which rule decides it: modal period (which mode?), IS 1893 empirical formula
`0.09h/sqrt(d)`, or hand-picked case by case? What was the reason for Sample 1 using the mode-2 period and
Sample 2 using the empirical one?
*Proposed default:* offer a switch (empirical / chosen mode), you pick per project.
**Answer: fundamental T =0.0672\*POWER(Height above ground ,0.75). but yes keep chosen mode. 0**

**Q10. Seismic weight.** From the ETABS mass source? Are basements excluded? DL + SDL + 0.25 LL (or 0.5 LL
above 3 kN/m2)? Why does Sample 1 come out so much lower than the full-building sum?
*Proposed default:* mass from ETABS above the ground story, in kN.
**Answer: default. for samle 1 it was not accurate so ignore it.**

**Q11. Z, I, R, site class and Ah,min.** Are the IS 1893 parameters defined inside ETABS (auto-seismic load pattern
or response-spectrum function) so I can read Z, I, R and site class? Is Ah,min a formula (it is 0.011 in both
samples with Z = 0.16, I = 1.2, R = 4) or always typed?
*Proposed default:* read Z, I, R, soil from ETABS when present, otherwise typed; Ah,min typed.
**Answer: default**

**Q12. IS 1893:2025 column and return period TR.** Do you want the 2025 calculation now (Sample 2 uses it) or stay
2016-only?
*Proposed default:* stay 2016-only; TR stays a typed field.
**Answer: use 2016 only**

### E. Drift, torsion, chart

**Q13. Story drift.** Which cases are reported and what is the limit per case (my reading: wind H/500 = 0.002,
seismic 0.004)? Is the drift taken without R applied? Should "SAFE / UNSAFE" be calculated (drift <= limit)
instead of typed? (v1 kept it manual.)
*Proposed default:* max drift over all stories per mapped case; limits as above; SAFE / UNSAFE calculated.
**Answer: default. taken for only SPEC and Gust w cases.**

**Q14. Torsional irregularity.**

* Which story are the 4 points on (top, a specific floor)?
* How should the 4 points be chosen: extreme corner joints of the diaphragm, or you pick them?
* The app hard-codes the workbook's 4-point pairing. Keep that, or switch to the per-story max / average
displacement ratio that ETABS reports directly (no point selection, any number of stories)?

*Proposed default:* keep the 4-point table, auto-pick the 4 extreme corner joints of the top regular story, and
let you override.
**Answer: default. but choose the floor where drift is max, because in general it is tested at all floors but typical floors repeat mostly and the terrace has higher number so top floor is chosen for report.**

**Q15. Displacement chart.** Ux from GWX and Uy from GWY? Which joint: center of mass, or the maximum over the
story? Ground level upward only (basements excluded)?
*Proposed default:* max story displacement under GWX (Ux) and GWY (Uy), from the ground story up.
**Answer:default.**

**Q16. Checklist rules.** Please define:

* "First 2 modes translational" / "Clear Modes": e.g. each of modes 1 and 2 has RZ below some fraction?
* "Deflection": top displacement <= H/500 under wind?
* "Drift check": all mapped drifts <= their limits?

*Proposed default:* as written above, with an editable threshold; you can still override each item manually.
**Answer:default. if RZ is more than translation in X and Y then it is second mode torsion. and translation are recommended to have >.6 and RZ <0.3**

### F. Mass index, crack coefficients, images

**Q17. Mass index.**

* How is the raft identified (slab property of type Mat, or the stories below ground)? It is blank in both samples.
* The `J48:J52` column (m3 / sqft) lost its divisor. Is it volume / (ETABS total area in sqft)?
* Any non-concrete material (steel, composite) to exclude from the 25 kN/m3 conversion?

*Proposed default:* raft = Mat-type slabs (else 0); divisor = ETABS total area x 10.7639 sqft/m2; concrete only.
**Answer: yes**

**Q18. Crack coefficients table** (beams 0.5 / 0.35, columns 1 / 0.7, walls 1 / 0.7, floors 0.36 / 0.25). Fixed
company standard, or should it be read from the model's property modifiers? What do "Service" and "Design" mean
in the model (two different modifier sets)?
*Update:* the code can set stiffness modifiers itself (see Q21 to Q23), so this table is most likely the two
modifier sets the tool applies before each run (SLS / "Service" and ULS / "Design"), not something to read back.
*Proposed default:* fixed standard table that the tool applies; it also fills the report's table.
**Answer: YES BUT ,refer this**

&#x09;				**Service	   Design**

&#x09;		**beams	0.70			0.35**

&#x09;		**columns	0.90			0.7**

&#x09;		**walls	0.90			0.7**

&#x09;		**floors	0.35			0.25**



**Q19. Images.**

* OK to draw the ETABS plan with numbered points ourselves from the model geometry (instead of an ETABS
screenshot)?
* How will the CAD typical-floor plan be supplied: DXF, DWG, or PNG? (The connector already has a DXF importer.)

*Proposed default:* generated plan from geometry; CAD plan uploaded as PNG.
**Answer: default.**

### G. Delivery

**Q20. Output.** Should this be a new "Report" card in the ETABS Connector GUI that writes a `ReportData` JSON file
which you then open in the existing `etabs-report` app (no changes to its calc engine), or do you want the
connector to produce the final PDF / Excel directly?
*Proposed default:* JSON handoff to the existing app.
**Answer: yes i want final pdf or excel directly**

### H. Stiffness modifiers and SLS / ULS runs

The ETABS API can read and set modifiers on frame and area properties (and on individual objects), save the
model, run analysis and read results. A report would then be produced in two passes on a working copy:
set SLS modifiers, analyse, extract; set ULS modifiers, analyse, extract; restore the original modifiers.
That costs two analyses per report.

**Q21. Modifier sets.** For each of beams, columns, walls and floors, which property names or groups does it apply
to (by name pattern, material, or a group)? Which modifier components are changed: beams (I22 / I33 only, or
also area / shear), columns, walls (membrane f11 / f22 / f12 or bending m11 / m22 / m12), floors (which of
f11 / f22 / m11 / m22)? Do any models carry object-level modifier overrides that must be kept? Is it acceptable
for the tool to change modifiers on a saved working copy of the model (never your original file)?
*Proposed default:* beams / columns by frame section name pattern; walls and floors by area property type; modifiers
applied to the section / property definitions on a working copy; object-level overrides left alone and reported.
**Answer: default. for shells use f11,f22,f12,m11,m22,m12. and for frames modify torsion constant and moment of inertia about 2 axis and 3 axis.**

**Q22. Which results come from which set?** Please mark each as SLS or ULS: modal periods and mass ratios, EQ base
shear and moments, wind base shear, wind drift / displacement chart / deflection check, seismic drift, torsional
irregularity displacements, gravity loads and mass index (probably independent of modifiers), seismic weight.
*Proposed default:* SLS for all wind and deflection results; ULS for seismic, modal, torsion and drift under seismic;
gravity and mass independent.
**Answer: default. but torsion check for GW and normal wind to be in SLS.**

**Q23. Analysis runs.** Do models contain nonlinear, P-delta or staged-construction cases that change how long a run
takes or whether results can be compared across passes? How long does one full analysis usually take, and is
two runs per report acceptable (otherwise the tool runs only the cases needed for each set)?
*Proposed default:* run only the cases needed per pass; fail loudly if a case is missing or the model stays unlocked.
**Answer: the model is neede to be unlocked anyhow for modifier updated for ULS and SLS. run time is anywhere between 5 to 15 mins and yes the model contains both non linear and Pdelta cases.**

\---

## 4\. Anything else?

Parameters that appear in your reports but I missed, rules you apply that are not written anywhere, or
projects where the above assumptions break:

**Answer:**

\---

## 5\. Scale estimate (final, based on your answers)

| | Estimate |
|---|---|
| Fully automatic from ETABS | about 95% of the values in the report (modal, base shear, gravity, drift, torsion, story / chart table, mass index, areas, heights, storey description, T by formula, Z / I / R when defined in ETABS) |
| Typed or decided by the engineer | about 8 items: engineer initials, revision, location, zone / category / frame type (and site class if not in ETABS), Ah,min, consultant wind row, CAD plan image, and the pass / fail status of the 3 judgement checks (the tool suggests, the engineer decides) |
| Run time per report | about 10 to 30 minutes unattended (two analyses of 5 to 15 minutes each, fewer cases per pass), plus 1 to 2 minutes of extraction and about 5 minutes of engineer review |
| Scale | one model at a time on an attached ETABS session (the ETABS window is needed). Unattended batch over many files is not part of this plan |
| Main risks | nonlinear / P-delta cases (handled by the rules in section 6), object-level modifier overrides, restoring the original modifiers, and embedding the two plan images in the Excel export (see section 6, step 5) |

\---

## 6\. Decisions and implementation plan

### 6.1 Decisions taken (from your answers)

* Attach to the open model; Python does all ETABS work; React is the front end; **Excel** is the deliverable.
* **Q14:** check all stories, pick the one with the maximum drift (across the mapped GW and SPEC cases) and use it for the torsion points.
* **Q16:** 0.6 / 0.3 are recommended values only. The tool shows them as a suggestion; the engineer sets the status.
* **Q23 (your decision: run all cases):** case definitions are never edited and each pass runs all cases as currently flagged (`RunAnalysis`, no `SetRunCaseFlag`). After each pass the tool checks every case reports "finished" and stops with a clear message if not. The report records which modifier set each result came from. I checked the attached model myself (section 6.5): the only nonlinear case is the auto-created `~P-Delta`, and nothing depends on it, so there is no special ordering to handle.
* **Q18 / Q21 / Q22:** modifier table, components and SLS / ULS split as answered (GW cases in SLS, SPEC and EQ cases and modal in ULS). **Modifiers are written on each object, not on the properties** (that is how the checked model has them, see 6.5). Beams and columns are told apart by geometry (non-vertical vs vertical frames); walls and floors by area property type (wall vs slab); Mat-type slabs are raft and are left alone.
* **Defaults I will apply** to the small items still open: ground story = the story named "G" or "Ground", otherwise the base (elevation 0), with a dropdown to override; report note reads "P = Podium, Pr = Provisional"; wind case patterns `GWX*` and `GWY*` (editable in the mapping screen); the stray "0" in Q9 is ignored. Tell me if any of these is wrong.

### 6.2 Architecture

```
ETABS (COM) -> Python extractor (+ two-pass modifier runner) -> local server (127.0.0.1)
                                                                  |
                                          React app (existing etabs-report) <- fetches ReportData JSON
                                                                  |
                                                      Excel export (existing template + formulas + chart)
```

The React app already exports into the original workbook layout (formulas and chart kept, via
`src/persistence/originalWorkbookExport.ts` and `public/template/etabs_report_template.xlsx`), so Excel needs no new
generator. The Python side produces a `ReportData` JSON (`src/model/types.ts`, schemaVersion 1) and serves it; the
connector does not need to port the calculation engine.

### 6.3 Steps

1. **Read-only extractor** (`report_extractor.py`): attach with the existing worker (`etabs_worker.py`) and units handling
   (`fdr_tool.py`); read story data, modal, base reactions, drifts, joint displacements, areas and mass tables; build `ReportData`.
   First task is a spike on one real model to confirm the exact API calls and table names (not verified yet).
2. **Two-pass runner** (`modifier_runner.py`): save a working copy, snapshot every object's modifiers, apply SLS to all
   objects, run all cases, extract, apply ULS, run all cases, extract, restore the snapshot. Progress and cancel support.
3. **Local server** (`report_server.py`, localhost only): start extraction, report progress, return `ReportData`, serve the built React app. Started from a new "Report" card in `etabs_gui.pyw`.
4. **React changes:** implement the stub `src/adapters/etabsApiAdapter.ts` as a fetch to the local server with a progress view;
   mapping screen (case names, ground / typical story); drift status auto-calculated; checklist shows suggestions that the engineer overrides; wind and drift rows per your Q8 / Q13 rules.
5. **Plan images:** render the ETABS plan with numbered torsion points from model geometry (Python), accept the CAD plan as PNG
   (the app already has a layout-images section). The Excel export currently strips plan pictures from the template, so embedding both images needs new code in the TS export. This is the one piece with real uncertainty.
6. **Validation:** transcribe both sample reports into fixtures, run the extractor on matching models, and compare every field; keep the existing 42 tests passing.

### 6.3b Progress

* **Step 1 done:** `report_extractor.py` reads the attached, already-analysed model (about 16 seconds on the 55-story model) and writes a `ReportData` JSON.
  Ran on `Antop T4-ULS-FDR.EDB`; output saved as the test fixture `etabs-report/src/__tests__/fixtures/antop_t4_extraction.json`.
  New test `etabsExtraction.test.ts` checks the JSON imports, calculates (no NaN / Infinity) and exports into the workbook layout with no warnings; all 52 app tests pass.
* **Cross-checks that agreed:** wall plan area from geometry (46.72 m2) equals your weight / 25 / height method (46.72 m2); self weight from the material list
  (390,893 kN) equals the DL pattern total as in the samples; seismic weight from the mass table (503,093 kN) is within 0.5% of DL + 0.25 LL (505,546 kN).
* **Later update (steps 2 to 5 built):** the two-pass SLS / ULS runner, the case-mapping dialog (EQ / SPEC / gust / static wind),
  the plan picture with corner points 1 to 4, and opening the report directly in the report app (no JSON import) are done.
  The DXF Model Builder tile was removed from ETABS Live. How each value is obtained and how the SLS / ULS runs work:
  `docs/REPORT_EXTRACTION_EXPLAINED.md`.
* **Correction to the first version:** the first extractor only read the model's current state (ULS for Antop); its 16 seconds
  was reading time, and it did **not** produce SLS results. The full pipeline now does.
* **Still not done:** a full SLS / ULS run on one of your real models (verified on a throwaway ETABS model instead), and
  validation of every field against the two sample reports.

### 6.4 Still open (not blocking the first steps)

Q2 ground-story rule and the P / Pr wording (defaults above), Section 4 (anything else), and the four items in 6.5
marked "needs your answer".

### 6.5 What I found by reading the attached model (read-only, nothing was changed)

Model: `Antop T4-ULS-FDR.EDB`, locked with all results present.

* **Cases (23):** `Modal-Ritz`, `DL`, `LL`, `WX`, `WY`, `GWX1`, `GWX2`, `GWY1`, `GWY2`, `WMEANX`, `WMEANY`, `EX`, `EY`, `SX`, `SY`, `DX`, `DY`,
  plus ETABS auto cases `~P-Delta`, `~LLRF`, `~TorsionSX/SY/DX/DY`. All are flagged to run and all show "finished".
  Types: `DL`..`EY` and `WMEAN*` are linear static, `SX/SY/DX/DY` are response spectrum, `Modal-Ritz` is modal. The only nonlinear
  case is the auto-created `~P-Delta`. The modal case starts from zero stiffness ("None"), and no linear case starts from `~P-Delta`.
* **Load patterns:** `SW`, `SD`, `LL`, `LL2`, `WX`, `WY`, `GWX1/2`, `GWY1/2`, `WMEANX/Y`, `EX`, `EY`. So DL = `SW`, SDL = `SD` (patterns, not cases), and there are two gust cases per direction, which fits your "max case" rule.
* **Stories:** `F1` to `F55`, no "G" and no basements. Elevations are in mm: F1 = 4,200, then 3,350 for every story up to F55 = 185,100. So ground is the base (elevation 0) here, which is why I added that fallback.
* **Modifiers are on the objects, not the properties.** All 192 frame properties and 308 area properties are at 1.0. The objects carry the values:
  all 6,215 frames are non-vertical (no column frames; columns are modelled as walls) with I22 = I33 = 0.35 and J = 1.0;
  13,200 wall objects have all six shell modifiers (f11, f22, f12, m11, m22, m12) = 0.7; 2,090 slab objects have f = 1.0 and m = 0.25.
  This matches your Design column (beams 0.35, walls 0.7, floors 0.25), so the model is currently in its ULS state.
* **Results API spike (read-only, units switched to kN, mm and restored):** `Results.ModalParticipatingMassRatios` (165 rows, mode 1 = 7.26 s, mostly UY),
  `Results.BaseReact` (per case, e.g. `GWX1`, `EX`), `Results.StoryDrifts` (per story, per direction, with the joint label of the max), and
  `Results.JointDispl` all returned data for the attached model. `DatabaseTables` lists 150 tables including `Mass Summary by Story`,
  `Material List by Object Type` and `Centers Of Mass And Rigidity`. The calls and their return layouts are now confirmed for ETABS 23 on this model.
* **Run time:** not measured (I did not run any analysis).

**Resolved (you said "SX and SY count as SPEC, everything else you decide"):**

* **SPEC cases = `SX`, `SY` only.** `DX` / `DY` are not used in the report (they stay selectable in the mapping screen).
* **Wind = gust cases `GWX*` / `GWY*`.** Static `WX` / `WY` and `WMEANX` / `WMEANY` are not used. The wind row, drift (with SX, SY) and the torsion and chart data use them; the "max case" is the gust case with the largest along-wind base shear.
* **EQ row = `EX` / `EY`** (Vx, My from `EX`; Vy, Mx from `EY`), as you answered in Q7.
* **Frames J (my decision):** leave J unchanged by default and apply the table only to I22 and I33, because that is how the model is set up today. A switch can also scale J. Reason: before the tool changes anything it will apply your ULS table to the model's current state and compare it to what is there; with J untouched the two match exactly, which proves the setup reproduces your own ULS model.
* **Floors (my decision):** apply 0.35 (SLS) / 0.25 (ULS) to m11, m22, m12 only and keep f11, f22, f12 at 1.0, as in the model. Walls get all six components (0.9 SLS / 0.7 ULS). Beams and columns follow your table (beams 0.70 / 0.35, columns 0.90 / 0.70).

The four questions below are therefore closed; they are kept for the record.

Previously asked:

1. **Frames J:** the model has J = 1.0 with only I22 and I33 reduced, but you said to modify the torsion constant too. Do I change J (to what value) or leave J at 1.0?
2. **Floors:** the model changes only the bending terms (m11, m22, m12) and keeps f = 1.0. I will apply your 0.35 (SLS) / 0.25 (ULS) to m only. Is that right?
3. **Spectrum cases:** what are `DX` / `DY` versus `SX` / `SY` (for example drift vs strength)? Which ones count as "SPEC" for the drift and torsion tables?
4. **Wind and static seismic:** is `WX` / `WY` the static wind and `GWX1/2`, `GWY1/2` the gust wind in your reports? Is `WMEANX/Y` ignored? And is `EX` / `EY` the static seismic for the EQ row?

