# Design review report: how every value is obtained

This explains what the **Design Review Report** card in ETABS Live does, value by value, and exactly how the
SLS and ULS results are produced. It describes the code as built (`report_extractor.py`, `report_modifiers.py`,
`report_pipeline.py`, `report_plan_image.py`, `report_server.py`, and the report app in `etabs-report`).
The decisions behind it are in `docs/report_automation_questions.md`.

---

## 1. The short version

1. You attach to ETABS (card 01) and press **Cases...** once per model to confirm which load cases are EQ, SPEC, gust wind and static wind. This is saved next to the model.
2. **Generate** checks the model, tells you what it is going to do and asks to proceed.
3. It reads the results, runs ETABS again **only if a stiffness state is missing**, assembles the report, draws the plan with the four corner points, and **opens the report in your browser**, already filled in.
4. You check and correct the values there, add what only an engineer knows, and press **Export Excel**.

The JSON file is still written (in a `report_work` folder next to the model) but you never have to import it.

---

## 2. SLS and ULS: what really happens (please read)

### A correction first

In an earlier message I described the extraction as quick (about 16 seconds). **That version did not produce SLS and ULS
results at all.** It only *read* whatever results the model already held, in whatever stiffness state the model happened to be
in (for your Antop model that was ULS). The 16 seconds was only the time to read results; it is not the time to produce a
report. I should have said that more clearly, and the wind (SLS) values from that version were therefore not SLS values.

### Why a second analysis cannot be avoided

ETABS keeps **one** stiffness state in a model. The report needs the wind results at the service stiffness (SLS) and the seismic
and modal results at the design stiffness (ULS). Stiffness modifiers change how force is shared between walls, slabs and beams,
so results from one state cannot be converted to the other by scaling. The only correct way to get both is to set the modifiers
for one state, run the analysis, read the results, then set the other state and run again.

### What the code does

* It writes the modifiers **on each object** (6,215 beams, 13,200 wall objects, 2,090 slab objects in your Antop model),
  because that is where your models carry them (all property-level modifiers are 1.0; if they are not, you get a warning).
* The values are the table you gave: beams 0.70 / 0.35, columns 0.90 / 0.70, walls 0.90 / 0.70, floors 0.35 / 0.25 (SLS / ULS).
  Beams and columns change I22 and I33 only (J is left alone unless you tick the box in the Cases dialog); walls get all six
  shell modifiers (f11, f22, f12, m11, m22, m12); floors get the three bending terms (m11, m22, m12) and keep membrane at 1.0,
  exactly as your Antop model has them.
* Beams and columns are told apart by geometry (a vertical frame is a column); walls and floors by the area property type.
  Mat and footing slabs, and any other shell, are never touched.

### How it avoids doing more runs than needed

Before touching anything it snapshots every object's modifiers and compares them with the SLS and ULS tables:

| The model is in | and has results | What happens | Analysis runs |
|---|---|---|---|
| the ULS state | yes | ULS is read from the existing results; SLS is written on a copy and run | **1** |
| the SLS state | yes | SLS is read from the existing results; ULS is written on a copy and run | **1** |
| neither state (for example all 1.0) or no results | any | both SLS and ULS are written on a copy and run | **2** |

Each run is a normal "run all cases" (typically 5 to 15 minutes for your models; I have not timed one on a real model of yours).
So a report takes roughly **5 to 15 minutes when the model is already in ULS or SLS, and 10 to 30 minutes otherwise**.

### The safety measures

* **Your file is never modified.** Before changing anything the model is saved under a new name in a `report_work` folder next
  to it, and ETABS switches to that copy. After a run, ETABS is therefore showing the copy; your original is exactly as it was
  and can be reopened. (ETABS reports the working file as `.$et` after a Save As; the code accounts for that.)
* **Every set is read back and must match.** After writing a set, all objects are read again; if even one object differs from
  the table the analysis is not run and the run stops.
* **All flagged cases must finish.** After each run every case that is flagged to run must report "finished" and the model must
  be locked; otherwise the run stops with the list of unfinished cases. Cases you mapped that were switched off are switched on
  in the copy only, and this is logged.
* **Consistency checks between the two states:** the dead-load reaction must be identical in SLS and ULS (a stiffness change
  must not change weight) and the mode 1 period in SLS must not be longer than in ULS. Both are shown in the report's check list.
* **If something fails after the copy exists,** the message says so and tells you ETABS is on an unfinished working copy and
  that your original is untouched.
* **Your confirmation is asked before any write or run**, with the plan in plain language (including the number of runs).

### What was tested

* The logic that builds the modifier arrays, detects the model's state, writes a set and verifies it, and handles the `.$et`
  file name, is covered by offline tests (`tests/test_report_logic.py`, 14 tests, including a fake ETABS that "forgets" writes to
  prove the run is refused).
* The whole path was run live on a **separate throwaway ETABS instance** with a small generated 8-story model (not your models):
  state detection, working copy, writing and read-back of 48 objects, running, reading, the shortcut of reading an existing state
  (1 run instead of 2), and the two routes giving the same numbers (differences below 1e-12).
* **Not yet done:** a full run on one of your real models (the 5 to 15 minute runs). The reading side was run on your real
  Antop and Dhanukarwadi models. The throwaway model could not run its modal case (no usable mass), so for that test the modal
  result was substituted; the modal reading itself was tested on your real models.

---

## 3. Every value, where it comes from

"State" = which stiffness state the value is reported from. "Auto" = no input from you. Names in `code font` are ETABS API
calls or tables. All reading is done with the present units switched to **kN, mm** and restored afterwards.

### 3.1 Header

| Value | Source | State |
|---|---|---|
| Project name | the model file name without extension | auto |
| File path | the model path (the original `.EDB`) | auto |
| Report date | today's date | auto |
| Engineer initials, Revision | typed in the app (revision starts as R0) | you |

### 3.2 Building properties

| Value | How it is obtained | State |
|---|---|---|
| Location, Storey description | typed (story names are too varied to count reliably) | you |
| Ground level | the story named G / Ground, otherwise the base (elevation 0); can be set in the Cases dialog | auto |
| Height above ground (D8) | top story elevation minus ground elevation (`Story.GetElevation`) | auto |
| Height below ground (D10) | ground elevation minus base elevation (base = lowest story elevation minus its height) | auto |
| Typical floor height (D9) | the most common story height (the lowest story is excluded) | auto |
| Typical story | the story whose slab area is the most repeated among stories above ground (the middle one of that group); can be fixed in the Cases dialog | auto |
| Typical floor area (D15) | sum of the slab polygon areas on the typical story (`AreaObj.GetNameListOnStory`, `GetPoints`, shoelace formula); Mat / footing slabs excluded | auto |
| ETABS total area (D16) | the same sum over **all** stories, gross (openings not deducted) | auto |
| Shear wall ETABS area (D17) | on the typical story, for every wall object: the plan length of its lower edge times the wall thickness (`PropArea.GetWall`) | auto |
| Width B, Length L | the smaller and larger side of the bounding box of the slabs on the typical story | auto |
| Slenderness limit | 8 (IS 16700:2023 Table 2), fixed | auto |
| Aspect ratio, slenderness, shear wall % | calculated by the report app | calc |

Checks shown: slab area compared with the typical story on all other stories (warns if more than 10% off, excluding the top 2);
wall area compared with your own method (wall weight / 25 / total height).

### 3.3 Seismic parameters

| Value | How | State |
|---|---|---|
| Zone, Category, Frame type, Site class, Z, I, R, Ah,min | **typed**: the ETABS API cannot read the IS 1893 parameters | you |
| Code | IS 1893:2016 only | fixed |
| Fundamental period T | T = 0.0672 x h^0.75, h = height above ground in m (your Q9 answer) | auto |
| Seismic weight | table `Mass Summary by Story`: the UX mass of the stories above ground x 9806.65 mm/s^2 | auto |
| Sa/g, Ah, Base shear | calculated by the report app from T, Z, I, R, Ah,min and the weight | calc |

Check shown: the seismic weight compared with dead load + 0.25 live load.

### 3.4 Base shear and moments

| Value | How | State |
|---|---|---|
| EQ row: Vx, My | base reaction `Results.BaseReact`, Fx and My of the mapped **EQ-X** case (the one with the largest \|Fx\| if several) | ULS |
| EQ row: Vy, Mx | Fy and Mx of the mapped **EQ-Y** case | ULS |
| Wind row "Gust (ETABS)" | for the **gust X** cases, the one with the largest \|Fx\| gives **Fx** (along) and **Cy** (its \|Fy\|, across); for the **gust Y** cases, the one with the largest \|Fy\| gives **Fy** and **Cx**. Stored in the workbook positions Fx, Fy, Cx, Cy, as magnitudes | SLS |
| Wind row "Static wind (ETABS)" | the same rule for the static wind cases (second wind row, only if static cases are mapped) | SLS |
| Consultant / wind-tunnel row | not from ETABS | you |

Checks shown: EQ and wind rows must not be all zero.

### 3.5 Modal participating mass ratios

First three modes of the mapped modal case: period, UX, UY, RZ (`Results.ModalParticipatingMassRatios`). **Nature** = the
largest of UX (X-trans), UY (Y-trans) and RZ (Torsion). State: ULS. The period difference is calculated by the app.

### 3.6 Gravity load check

| Value | How | State |
|---|---|---|
| DL (D41) | sum of the table `Material List by Object Type` (self weight of all concrete), as in your sample reports where DL equals the mass-index total | auto |
| SDL (D43) | base reaction Fz of the dead case minus that self weight (the dead case holds both the self-weight and super-dead patterns) | auto |
| LL (D44) | base reaction Fz of the live case | auto |

Check shown: SDL must not be negative.

### 3.7 Story drift checks

For each of GWX, GWY, SpecX, SpecY the largest absolute drift in the matching direction over all stories and all cases of that
group (`Results.StoryDrifts`); the label shows the story, for example `GWX (F34)`. Limits: 0.002 for wind and 0.004 for seismic.
The status is computed (drift <= limit) as a **suggestion**. GW rows come from SLS; SPEC rows from ULS.

### 3.8 Structure mass index

Table `Material List by Object Type`, summed per Column, Beam, Wall, Floor. Raft is 0 (not identified; a warning says so). Weights
in kN; volumes (/ 25 kN/m3) and percentages are calculated by the app. Independent of stiffness.

### 3.9 Torsional irregularity

* **Story:** the story with the largest drift over all the drift cases above, as you decided (not the top story).
* **Four points:** on that story, the joints at the extremes of (y - x), (x + y), -(x + y) and (x - y): points 1 to 4 are top-left,
  top-right, bottom-left and bottom-right of the plan, like the figure in your reports. The label is `n (joint id)`.
* **Eight displacement values per point:** U1 and U2 (`Results.JointDispl`) under the governing gust X case and gust Y case (SLS)
  and the SPEC-X and SPEC-Y cases (ULS). The maximum / average ratios are calculated by the app.

### 3.10 Checklist (suggestions only)

| Item | Suggested from | Decided by |
|---|---|---|
| Clear Modes | modes 1 and 2 are not torsion, UX + UY >= 0.6 and RZ <= 0.3 | engineer |
| Time period difference >= 10% | calculated | calc |
| Drift check | all four drifts <= limits | engineer |
| Deflection | top-story Ux and Uy <= H/500 | engineer |
| Torsional irregularity | calculated (ratio < 1.2) | calc |

### 3.11 Chart and story table

Table `Story Max Over Avg Displacements`, column Maximum: Ux of the governing gust X case in direction X, Uy of the governing gust
Y case in direction Y, for every story from ground up, plus a "Base" row of zero when ground is the base. Elevations are relative to
ground. The H/500 line is calculated by the app. State: SLS.

### 3.12 Crack coefficients table (Excel C50:D53)

For a full report the Excel table shows the stiffness modifiers that were actually applied in the two runs (the table you gave).
A quick read, which made no runs, leaves the template's own table untouched.

### 3.13 Pictures

* **ETABS layout:** drawn from the model: the slabs of the torsion story in grey, wall base lines in red, beams in blue, the four
  corner joints as black dots numbered 1 to 4 (drawn with Pillow, no screenshot). It is shown in the app and placed in the Excel file.
* **CAD layout:** not from ETABS; paste or upload it in the app.
* In the Excel file the pictures are placed at positions measured on your original workbook (ETABS plan under the seismic block,
  CAD plan to its left of the chart). I could not open the result in Excel itself here, so check the placement the first time.

---

## 4. The case mapping (Cases... button)

The dialog lists every case (the automatic `~` cases are hidden) with its type, and a role to choose: EQ X / Y, SPEC X / Y,
Gust wind X / Y, Static wind X / Y, or ignore. It also asks for the modal, dead-load and live-load cases, the ground and typical
story (auto by default) and the SLS / ULS modifier table. The first guess uses the case type and the name (a trailing X or Y
gives the direction; names containing GW or GUST are gust wind). It is a guess: **check it**, for example on one of your models it
proposed SX1 / SX2 as static EQ cases and the plain wind cases as static wind. The mapping is saved as
`<model>.report_mapping.json` next to the model and reused next time; **Cases...** edits it.

Before anything runs, the mapping is validated: every mapped case must exist, EQ X and Y must have a case, and modal, dead
and live cases must be set.

---

## 5. The report window

When generation finishes the report opens in your browser (a small web server on your own computer, `127.0.0.1` only,
serves the report app and the data; nothing leaves your machine). The yellow panel at the top lists every automatic check
with OK / Check / Problem, the suggested statuses, what you still have to type, and a table of where each value came from.
Edit anything in Edit Mode, view it in Report Mode, then **Export Excel** or **Print / PDF**.

* Keep the **ETABS Live window open** while you work in the report; closing it stops the little server. Your edits are autosaved
  in the browser, and **Export Excel** is how you finish.
* The app must be built once (`npm run build` in `etabs-report`). If it is not, the data is saved and you are told how.

---

## 6. Known limits and things to check

* Z, I, R, zone, category, frame type, site class, Ah,min, location, storey description, engineer and revision are typed.
* The Excel chart holds 56 story rows; a taller model (for example 61 rows in Dhanukarwadi) loses the lowest stories in the Excel
  chart, and the report says so.
* The workbook has two wind rows and no room for a consultant row once both gust and static rows are filled.
* The corner joints are the extreme joints of the story; if a canopy or cantilever sticks out, check the picture.
* Raft weight is 0 unless you type it.
* A report from a model whose analysis includes nonlinear cases runs them as flagged; nothing in the case definitions is changed.
* After Generate, ETABS Live's FDR lists still refer to the original model; reconnect before using the FDR card.
* Not yet exercised on a real model: the full SLS / ULS re-run (see section 2).
