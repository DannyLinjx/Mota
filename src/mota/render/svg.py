"""SVG rendering from scene DTOs (real geometry, millimetre viewBox)."""

from __future__ import annotations

import html

from mota.domain.models import SceneObject, SceneSnapshot


def _fmt(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _escape(text: str) -> str:
    return html.escape(text, quote=True)


def scene_to_svg(scene: SceneSnapshot, *, background: str = "#0B1015",
                 include_text: bool = True) -> str:
    width = float(scene.bounds.get("width", 100))
    height = float(scene.bounds.get("height", 100))
    scene3d = scene.scene_kind == "pcb3d"
    if scene3d:
        width = float(scene.bounds.get("width", 100)) + 20
        height = float(scene.bounds.get("height", 100)) + 20
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}mm" height="{height}mm" '
        f'viewBox="0 0 {_fmt(width)} {_fmt(height)}" '
        f'font-family="-apple-system, Segoe UI, Noto Sans, sans-serif">',
        f'<rect x="0" y="0" width="{_fmt(width)}" height="{_fmt(height)}" fill="{background}"/>',
    ]
    if scene3d:
        parts.append(_isometric(scene, width, height))
    else:
        parts.append(f'<g transform="translate(0,{_fmt(height)}) scale(1,-1)">')
        for obj in scene.objects:
            parts.append(_object(obj, flip=True))
        parts.append("</g>")
        if include_text:
            for obj in scene.objects:
                if obj.kind == "text":
                    parts.append(_text(html_text=obj.text or "", x=obj.x or 0,
                                       y=height - (obj.y or 0),
                                       size=obj.font_size or 2,
                                       color=obj.color or "#E7EDF2"))
    parts.append("</svg>")
    return "\n".join(parts)


def _object(obj: SceneObject, flip: bool = False) -> str:
    color = obj.color or "#E7EDF2"
    fill = obj.fill if obj.fill is not None else "none"
    width = obj.width or 0.15
    dash = ' stroke-dasharray="0.6 0.4"' if obj.dashed else ""
    if obj.kind == "rect":
        return (f'<rect x="{_fmt(obj.x or 0)}" y="{_fmt(obj.y or 0)}" '
                f'width="{_fmt(obj.w or 0)}" height="{_fmt(obj.h or 0)}" '
                f'fill="{fill}" stroke="{color}" stroke-width="{_fmt(width)}"{dash}/>')
    if obj.kind == "polygon":
        pts = " ".join(f"{_fmt(p[0])},{_fmt(p[1])}" for p in obj.points)
        return (f'<polygon points="{pts}" fill="{fill}" stroke="{color}" '
                f'stroke-width="{_fmt(width)}"{dash}/>')
    if obj.kind == "polyline":
        pts = " ".join(f"{_fmt(p[0])},{_fmt(p[1])}" for p in obj.points)
        return (f'<polyline points="{pts}" fill="none" stroke="{color}" '
                f'stroke-width="{_fmt(width)}" stroke-linecap="round" '
                f'stroke-linejoin="round"{dash}/>')
    if obj.kind == "line":
        return (f'<line x1="{_fmt(obj.points[0][0])}" y1="{_fmt(obj.points[0][1])}" '
                f'x2="{_fmt(obj.points[1][0])}" y2="{_fmt(obj.points[1][1])}" '
                f'stroke="{color}" stroke-width="{_fmt(width)}"{dash}/>')
    if obj.kind == "circle":
        return (f'<circle cx="{_fmt(obj.x or 0)}" cy="{_fmt(obj.y or 0)}" '
                f'r="{_fmt(obj.r or 0)}" fill="{fill}" stroke="{color}" '
                f'stroke-width="{_fmt(width)}"{dash}/>')
    if obj.kind == "pad":
        shape = (obj.meta or {}).get("shape", "rect")
        if shape == "circle":
            return (f'<circle cx="{_fmt(obj.x or 0)}" cy="{_fmt(obj.y or 0)}" '
                    f'r="{_fmt((obj.w or 0) / 2)}" fill="{fill}" stroke="{color}"/>')
        x = (obj.x or 0) - (obj.w or 0) / 2
        y = (obj.y or 0) - (obj.h or 0) / 2
        return (f'<rect x="{_fmt(x)}" y="{_fmt(y)}" width="{_fmt(obj.w or 0)}" '
                f'height="{_fmt(obj.h or 0)}" rx="{_fmt((obj.meta or {}).get("round", 0))}" '
                f'fill="{fill}" stroke="{color}"/>')
    return ""


def _text(html_text: str, x: float, y: float, size: float, color: str) -> str:
    return (f'<text x="{_fmt(x)}" y="{_fmt(y)}" font-size="{_fmt(size)}" '
            f'fill="{color}">{_escape(html_text)}</text>')


def _isometric(scene: SceneSnapshot, width: float, height: float) -> str:
    """Simple axonometric projection of the board 3D scene for SVG export."""
    thickness = abs(float(scene.bounds.get("z", -1.6)))
    board_w = float(scene.bounds.get("width", 100))
    board_h = float(scene.bounds.get("height", 100))
    scale = min((width - 24) / (board_w + board_h * 0.5),
                (height - 20) / (board_h * 0.55 + thickness + 18)) * 0.9
    ox, oy = 12, height - 10

    def project(x: float, y: float, z: float) -> tuple[float, float]:
        px = ox + (x + y * 0.45) * scale
        py = oy - (y * 0.55 + z + thickness) * scale
        return px, py

    out = [f'<g stroke-linejoin="round">']
    corners = [(0, 0, 0), (board_w, 0, 0), (board_w, board_h, 0), (0, board_h, 0)]
    bottom = [(x, y, -thickness) for x, y, _ in corners]
    top_pts = " ".join(f"{_fmt(a)},{_fmt(b)}" for a, b in
                       (project(x, y, z) for x, y, z in corners))
    out.append(f'<polygon points="{top_pts}" fill="#173E35" stroke="#D7E2EA" stroke-width="0.3"/>')
    for (x, y, _), (x2, y2, _) in zip(corners, corners[1:] + corners[:1]):
        p1 = project(x, y, 0)
        p2 = project(x2, y2, 0)
        p3 = project(x2, y2, -thickness)
        pts = " ".join(f"{_fmt(a)},{_fmt(b)}" for a, b in (p1, p2, p3))
        out.append(f'<polyline points="{pts}" fill="none" stroke="#2B3B47" stroke-width="0.25"/>')
    for track in scene.objects:
        if track.kind == "mesh" and track.layer == "Copper":
            points = track.vertices
            if not points:
                continue
            matrix = track.world_matrix or []
            transformed = []
            for vx, vy, vz in points:
                if len(matrix) == 16:
                    wx = matrix[0] * vx + matrix[1] * vy + matrix[2] * vz + matrix[3]
                    wy = matrix[4] * vx + matrix[5] * vy + matrix[6] * vz + matrix[7]
                    wz = matrix[8] * vx + matrix[9] * vy + matrix[10] * vz + matrix[11]
                else:
                    wx, wy, wz = vx, vy, vz
                transformed.append(project(wx, wy, wz))
            xs = [p[0] for p in transformed]
            ys = [p[1] for p in transformed]
            out.append(f'<polygon points="{" ".join(f"{_fmt(a)},{_fmt(b)}" for a, b in transformed)}" '
                       f'fill="{track.color or "#D68C6A"}" opacity="0.9" stroke="none"/>')
    for body in scene.objects:
        if body.kind == "mesh" and body.layer == "Component":
            matrix = body.world_matrix or []
            if len(matrix) != 16 or not body.vertices:
                continue
            pts = []
            for vx, vy, vz in body.vertices:
                wx = matrix[0] * vx + matrix[1] * vy + matrix[2] * vz + matrix[3]
                wy = matrix[4] * vx + matrix[5] * vy + matrix[6] * vz + matrix[7]
                wz = matrix[8] * vx + matrix[9] * vy + matrix[10] * vz + matrix[11]
                pts.append(project(wx, wy, wz))
            hull = _convex_hull(pts)
            out.append(f'<polygon points="{" ".join(f"{_fmt(a)},{_fmt(b)}" for a, b in hull)}" '
                       f'fill="#3C5A52" stroke="#E0ECE7" stroke-width="0.15"/>')
    out.append("</g>")
    return "\n".join(out)


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pts = sorted(set((round(x, 4), round(y, 4)) for x, y in points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]
