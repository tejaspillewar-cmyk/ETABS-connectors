"""
Draws the plan of one story with the four torsion-check corner joints marked 1-4,
as the "ETABS layout" snip of the design review report. Returns a PNG data URL
(the report app stores images that way). Read-only.
"""
import base64
import io

from PIL import Image, ImageDraw, ImageFont

import report_extractor as rx

SLAB_FILL, SLAB_EDGE = (228, 228, 228), (150, 150, 150)
WALL, BEAM, MARK = (214, 48, 49), (74, 111, 181), (0, 0, 0)


def _font(size):
    for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_plan(SM, geom, story, size=900):
    """PNG data URL of `story` in plan with its 4 corner joints numbered, or None if there is nothing to draw."""
    coords = geom["coords"]
    ptype, _ = rx.area_property_types(SM)
    slabs, walls, beams = [], [], []

    on = SM.AreaObj.GetNameListOnStory(story, 0, [])
    for o in (on[1] if on[0] else ()):
        kind = ptype.get(SM.AreaObj.GetProperty(o, "")[0], "other")
        pts = [coords(p) for p in SM.AreaObj.GetPoints(o, 0, [])[1]]
        if kind == "slab":
            slabs.append([(p[0], p[1]) for p in pts])
        elif kind == "wall":
            lowest = min(p[2] for p in pts)
            low = [(p[0], p[1]) for p in pts if abs(p[2] - lowest) < 1.0]
            if len(low) >= 2:
                a, b = max(((u, v) for u in low for v in low),
                           key=lambda uv: (uv[0][0] - uv[1][0]) ** 2 + (uv[0][1] - uv[1][1]) ** 2)
                walls.append((a, b))
    fr = SM.FrameObj.GetNameListOnStory(story, 0, [])
    for o in (fr[1] if fr[0] else ()):
        p = SM.FrameObj.GetPoints(o, "", "")
        a, b = coords(p[0]), coords(p[1])
        if abs(a[0] - b[0]) > 1.0 or abs(a[1] - b[1]) > 1.0:
            beams.append(((a[0], a[1]), (b[0], b[1])))

    corners = geom["corners"].get(story) or []
    xs = [x for poly in slabs for x, _ in poly] + [x for w in walls for x, _ in w] + [c[1][0] for c in corners]
    ys = [y for poly in slabs for _, y in poly] + [y for w in walls for _, y in w] + [c[1][1] for c in corners]
    if not xs:
        return None
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    margin = 70
    scale = (size - 2 * margin) / max(x1 - x0, y1 - y0, 1.0)
    w_px = int((x1 - x0) * scale) + 2 * margin
    h_px = int((y1 - y0) * scale) + 2 * margin + 30

    def px(p):
        return (margin + (p[0] - x0) * scale, margin + (y1 - p[1]) * scale)

    img = Image.new("RGB", (w_px, h_px), "white")
    d = ImageDraw.Draw(img)
    for poly in slabs:
        d.polygon([px(p) for p in poly], fill=SLAB_FILL, outline=SLAB_EDGE)
    for a, b in beams:
        d.line([px(a), px(b)], fill=BEAM, width=1)
    for a, b in walls:
        d.line([px(a), px(b)], fill=WALL, width=3)
    f = _font(30)
    for i, (jn, c) in enumerate(corners):
        cx, cy = px(c)
        d.ellipse([cx - 9, cy - 9, cx + 9, cy + 9], fill=MARK)
        # number placed outside the plan: toward the corner's side
        dx = -1 if i in (0, 2) else 1
        dy = -1 if i in (0, 1) else 1
        d.text((cx + dx * 26 - 8, cy + dy * 26 - 14), str(i + 1), fill=MARK, font=f)
    d.text((margin, h_px - 34), f"{story}: torsion check points 1-4", fill=(90, 90, 90), font=_font(18))
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
