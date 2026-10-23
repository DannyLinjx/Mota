"""SceneService: authoritative 2D/3D scene DTOs generated from one design version."""

from __future__ import annotations

import math

from mota.circuit.parts import resolve_footprint, resolve_symbol
from mota.domain.models import (
    BoardIR, CircuitIR, DesignVersion, SceneObject, SceneSnapshot, SchematicIR,
)
from mota.errors import NotFound
from mota.render import meshes

COLORS = {
    "background": "#0B1015",
    "schematic_wire": "#B8D8AD",
    "schematic_symbol": "#E3C7A1",
    "board_outline": "#D7E2EA",
    "f_cu": "#D68C6A",
    "b_cu": "#69A8E8",
    "ratsnest": "#C9B46E",
    "board": "#173E35",
    "silk": "#E0ECE7",
    "text": "#E7EDF2",
    "muted": "#748895",
    "selected": "#F4D35E",
    "draft": "#86BEE8",
    "info": "#79B8F2",
    "warning": "#F1C36D",
    "hole": "#0B1015",
    "copper_top": "#D68C6A",
    "copper_bottom": "#69A8E8",
}


def schematic_scene(project_id: str, version: DesignVersion, circuit: CircuitIR,
                    schematic: SchematicIR) -> SceneSnapshot:
    objects: list[SceneObject] = []
    page_w = schematic.page_w_nm / 1e6
    page_h = schematic.page_h_nm / 1e6
    objects.append(SceneObject(id="page", kind="rect", layer="page", x=0, y=0,
                               w=page_w, h=page_h, fill="#0E151B",
                               color="#2B3B47", width=0.3, selectable=False))
    # title block
    objects.append(SceneObject(id="title_block", kind="rect", layer="frame",
                               x=page_w - 88, y=2, w=86, h=20, color="#2B3B47",
                               width=0.2, selectable=False))
    objects.append(SceneObject(id="title_text", kind="text", layer="frame",
                               x=page_w - 84, y=6, text=schematic.title,
                               font_size=3.2, color=COLORS["text"], selectable=False))
    objects.append(SceneObject(id="title_meta", kind="text", layer="frame",
                               x=page_w - 84, y=12,
                               text=f"{schematic.page_w_nm // 1_000_000} x "
                                    f"{schematic.page_h_nm // 1_000_000} mm  |  "
                                    f"snap {schematic.snap_nm / 1e6:.2f} mm",
                               font_size=2.2, color=COLORS["muted"], selectable=False))

    net_names = {n.id: n.name for n in circuit.nets}
    for sym in schematic.symbols:
        try:
            symbol_def = resolve_symbol(sym.symbol_id)
        except NotFound:
            symbol_def = None
        x, y = sym.x_nm / 1e6, sym.y_nm / 1e6
        w, h = sym.body_w_nm / 1e6, sym.body_h_nm / 1e6
        objects.append(SceneObject(
            id=f"symbol_{sym.component_id}", kind="rect", layer="symbol",
            x=x - w / 2, y=y - h / 2, w=w, h=h, color=COLORS["schematic_symbol"],
            fill="#141D25", width=0.25, component_id=sym.component_id,
            ref=sym.reference))
        ref_w = max(4.0, len(sym.reference) * 1.1)
        objects.append(SceneObject(
            id=f"ref_{sym.component_id}", kind="text", layer="reference",
            x=x - ref_w / 2, y=y + h / 2 + 0.8, text=sym.reference,
            font_size=2.4, color=COLORS["text"], component_id=sym.component_id,
            ref=sym.reference))
        objects.append(SceneObject(
            id=f"value_{sym.component_id}", kind="text", layer="value",
            x=x - max(4.0, len(sym.value) * 0.9) / 2, y=y - h / 2 - 2.4,
            text=sym.value, font_size=2.0, color=COLORS["muted"],
            component_id=sym.component_id, ref=sym.reference))
        if symbol_def:
            for pin in symbol_def.pins:
                anchor = sym.pin_anchors.get(pin.number)
                if not anchor:
                    continue
                ax, ay = anchor[0] / 1e6, anchor[1] / 1e6
                if pin.direction == "left":
                    bx, by = ax + pin.length_nm / 1e6, ay
                elif pin.direction == "right":
                    bx, by = ax - pin.length_nm / 1e6, ay
                elif pin.direction == "up":
                    bx, by = ax, ay - pin.length_nm / 1e6
                else:
                    bx, by = ax, ay + pin.length_nm / 1e6
                objects.append(SceneObject(
                    id=f"pin_{sym.component_id}_{pin.number}", kind="line",
                    layer="pin", points=[[ax, ay], [bx, by]],
                    color=COLORS["schematic_symbol"], width=0.2,
                    component_id=sym.component_id,
                    meta={"pin": pin.number, "pin_name": pin.name,
                          "direction": pin.direction}))
                objects.append(SceneObject(
                    id=f"pin_number_{sym.component_id}_{pin.number}", kind="text",
                    layer="pin_number", x=bx - 0.8 if pin.direction == "left" else bx + 0.4,
                    y=by + 0.5, text=pin.number, font_size=1.7,
                    color=COLORS["muted"], component_id=sym.component_id,
                    selectable=False, meta={"pin": pin.number}))

    for wire in schematic.wires:
        if wire.net_id == "__nc__":
            continue
        objects.append(SceneObject(
            id=wire.id, kind="polyline", layer="wire",
            points=[[p[0] / 1e6, p[1] / 1e6] for p in wire.points],
            color=COLORS["schematic_wire"], width=wire.width_nm / 1e6,
            net_id=wire.net_id, meta={"net_name": net_names.get(wire.net_id, "")}))
    for junction in schematic.junctions:
        objects.append(SceneObject(
            id=f"junction_{junction.x_nm}_{junction.y_nm}_{junction.net_id}",
            kind="circle", layer="junction", x=junction.x_nm / 1e6,
            y=junction.y_nm / 1e6, r=0.45, fill=COLORS["schematic_wire"],
            color=COLORS["schematic_wire"], net_id=junction.net_id))
    for label in schematic.labels:
        text_width = len(label.text) * 2.2 * 0.58
        x = label.x_nm / 1e6 - (text_width if label.justify == "right" else 0.0)
        objects.append(SceneObject(
            id=f"label_{label.net_id}_{label.x_nm}_{label.y_nm}", kind="text",
            layer="label", x=x, y=label.y_nm / 1e6,
            text=label.text, font_size=2.2, color=COLORS["info"],
            net_id=label.net_id,
            meta={"rotation": label.rotation_deg, "justify": label.justify}))
    for x_nm, y_nm in schematic.no_connects:
        x, y = x_nm / 1e6, y_nm / 1e6
        objects.append(SceneObject(id=f"nc_a_{x_nm}_{y_nm}", kind="line",
                                   layer="no_connect", points=[[x - 0.7, y - 0.7], [x + 0.7, y + 0.7]],
                                   color="#F07E8A", width=0.25, selectable=False))
        objects.append(SceneObject(id=f"nc_b_{x_nm}_{y_nm}", kind="line",
                                   layer="no_connect", points=[[x - 0.7, y + 0.7], [x + 0.7, y - 0.7]],
                                   color="#F07E8A", width=0.25, selectable=False))

    return SceneSnapshot(
        project_id=project_id, design_version=version, design_version_key=version.key,
        scene_kind="schematic", objects=objects,
        bounds={"x": 0, "y": 0, "width": page_w, "height": page_h},
        coordinate_system={"units": "mm", "x": "right", "y": "up",
                           "origin": "schematic page top-left is handled by the view"})


def pcb2d_scene(project_id: str, version: DesignVersion, circuit: CircuitIR,
                board: BoardIR) -> SceneSnapshot:
    objects: list[SceneObject] = []
    width = board.width_nm / 1e6
    height = board.height_nm / 1e6
    objects.append(SceneObject(
        id="board", kind="polygon", layer="Edge.Cuts",
        points=[[p[0] / 1e6, p[1] / 1e6] for p in board.outline],
        fill=COLORS["board"], color=COLORS["board_outline"], width=0.25,
        selectable=False))
    for hole in board.holes:
        objects.append(SceneObject(
            id=hole.id, kind="circle", layer="NPTH", x=hole.x_nm / 1e6,
            y=hole.y_nm / 1e6, r=hole.drill_nm / 2 / 1e6, fill=COLORS["hole"],
            color=COLORS["silk"], width=0.2, selectable=False,
            meta={"plated": hole.plated, "drill_mm": hole.drill_nm / 1e6}))

    for fp in board.footprints:
        c = fp.courtyard
        objects.append(SceneObject(
            id=f"courtyard_{fp.component_id}", kind="rect", layer="Courtyard",
            x=c.x_nm / 1e6, y=c.y_nm / 1e6, w=c.w_nm / 1e6, h=c.h_nm / 1e6,
            color="#2B3B47", fill=None, width=0.12, dashed=True,
            component_id=fp.component_id, ref=fp.reference, selectable=False))
        # silk body: inset courtyard by 0.3 mm
        objects.append(SceneObject(
            id=f"silk_{fp.component_id}", kind="rect", layer="Silkscreen",
            x=c.x_nm / 1e6 + 0.35, y=c.y_nm / 1e6 + 0.35,
            w=max(0.2, c.w_nm / 1e6 - 0.7), h=max(0.2, c.h_nm / 1e6 - 0.7),
            color=COLORS["silk"], fill=None, width=0.16,
            component_id=fp.component_id, ref=fp.reference))
        objects.append(SceneObject(
            id=f"ref_pcb_{fp.component_id}", kind="text", layer="Silkscreen",
            x=c.x_nm / 1e6, y=c.y_nm / 1e6 + c.h_nm / 1e6 + 0.6,
            text=fp.reference, font_size=1.5, color=COLORS["silk"],
            component_id=fp.component_id, ref=fp.reference))

    for pad in board.pads:
        objects.append(SceneObject(
            id=pad.id, kind="pad", layer="F.Cu" if "F.Cu" in pad.layers else "B.Cu",
            x=pad.x_nm / 1e6, y=pad.y_nm / 1e6, w=pad.w_nm / 1e6,
            h=pad.h_nm / 1e6, r=pad.drill_nm / 2 / 1e6 if pad.drill_nm else 0.0,
            color=COLORS["f_cu"], fill=COLORS["f_cu"],
            component_id=pad.component_id, net_id=pad.net_id,
            meta={"number": pad.number, "shape": pad.shape,
                  "drill_mm": pad.drill_nm / 1e6, "pad_type": pad.pad_type}))
        if pad.drill_nm:
            objects.append(SceneObject(
                id=f"{pad.id}_drill", kind="circle", layer="Drill",
                x=pad.x_nm / 1e6, y=pad.y_nm / 1e6,
                r=pad.drill_nm / 2 / 1e6, fill=COLORS["hole"], color=COLORS["hole"],
                component_id=pad.component_id, selectable=False))

    for track in board.tracks:
        objects.append(SceneObject(
            id=track.id, kind="polyline", layer=track.layer,
            points=[[p[0] / 1e6, p[1] / 1e6] for p in track.points],
            color=COLORS["f_cu"] if track.layer == "F.Cu" else COLORS["b_cu"],
            width=track.width_nm / 1e6, net_id=track.net_id,
            meta={"width_mm": track.width_nm / 1e6}))
    for via in board.vias:
        objects.append(SceneObject(
            id=via.id, kind="circle", layer="Via", x=via.x_nm / 1e6,
            y=via.y_nm / 1e6, r=via.outer_nm / 2 / 1e6,
            fill=COLORS["f_cu"], color=COLORS["silk"], width=0.1,
            net_id=via.net_id, meta={"drill_mm": via.drill_nm / 1e6}))
        objects.append(SceneObject(
            id=f"{via.id}_drill", kind="circle", layer="Drill",
            x=via.x_nm / 1e6, y=via.y_nm / 1e6, r=via.drill_nm / 2 / 1e6,
            fill=COLORS["hole"], color=COLORS["hole"], selectable=False))
    for line in board.ratsnest:
        objects.append(SceneObject(
            id=f"ratsnest_{line.net_id}_{line.x1_nm}_{line.y1_nm}",
            kind="line", layer="Ratsnest",
            points=[[line.x1_nm / 1e6, line.y1_nm / 1e6],
                    [line.x2_nm / 1e6, line.y2_nm / 1e6]],
            color=COLORS["ratsnest"], width=0.12, dashed=True,
            net_id=line.net_id, selectable=False))

    return SceneSnapshot(
        project_id=project_id, design_version=version, design_version_key=version.key,
        scene_kind="pcb2d", objects=objects,
        bounds={"x": 0, "y": 0, "width": width, "height": height},
        coordinate_system={"units": "mm", "x": "right", "y": "up",
                           "z": "up; top surface Z=0, bottom Z=-thickness",
                           "datum": "board lower-left corner (0,0)"})


def pcb3d_scene(project_id: str, version: DesignVersion, circuit: CircuitIR,
                board: BoardIR) -> SceneSnapshot:
    objects: list[SceneObject] = []
    thickness = board.thickness_nm / 1e6
    verts, faces = meshes.board_mesh(board)
    objects.append(SceneObject(
        id="board", kind="mesh", layer="Board", vertices=verts, faces=faces,
        world_matrix=meshes.mat_identity(), color="#2A6153",
        component_id=None, selectable=False,
        meta={"thickness_mm": thickness}))
    for hole in board.holes:
        verts, faces = meshes.cylinder_mesh(hole.drill_nm / 2 / 1e6, -thickness - 0.01, 0.01)
        matrix = meshes.mat_translate(hole.x_nm / 1e6, hole.y_nm / 1e6, 0.0)
        objects.append(SceneObject(
            id=hole.id, kind="mesh", layer="Hole", vertices=verts, faces=faces,
            world_matrix=matrix, color="#05090C", selectable=False,
            meta={"drill_mm": hole.drill_nm / 1e6, "through": True,
                  "note": "Rendered as a through cylinder; board mesh is not boolean-cut."}))
    for pad in board.pads:
        side = "top"
        fp = next((f for f in board.footprints if f.component_id == pad.component_id), None)
        if fp:
            side = fp.side
        if pad.shape == "circle":
            z0, z1 = (0.0, 0.035) if side == "top" else (-0.035, 0.0)
            verts, faces = meshes.cylinder_mesh(pad.w_nm / 2 / 1e6, z0, z1, 14)
        else:
            verts, faces = meshes.box_mesh(pad.w_nm / 1e6, pad.h_nm / 1e6, 0.035,
                                            z0=0.0 if side == "top" else -0.035)
        matrix = meshes.mat_translate(pad.x_nm / 1e6, pad.y_nm / 1e6, 0.0)
        objects.append(SceneObject(
            id=f"3d_{pad.id}", kind="mesh", layer="Copper",
            vertices=verts, faces=faces, world_matrix=matrix,
            color=COLORS["copper_top"] if side == "top" else COLORS["copper_bottom"],
            component_id=pad.component_id, net_id=pad.net_id, selectable=False))
    for track in board.tracks:
        z0 = 0.0 if track.layer == "F.Cu" else -0.035
        for index, seg in enumerate(track.segments()):
            verts, faces, matrix = meshes.track_box(seg, track.width_nm / 1e6, z0)
            objects.append(SceneObject(
                id=f"3d_{track.id}_{index}", kind="mesh", layer="Copper",
                vertices=verts, faces=faces, world_matrix=matrix,
                color=COLORS["copper_top"] if track.layer == "F.Cu" else COLORS["copper_bottom"],
                net_id=track.net_id, selectable=False))
    for via in board.vias:
        verts, faces = meshes.cylinder_mesh(via.outer_nm / 2 / 1e6, -thickness, 0.035, 14)
        matrix = meshes.mat_translate(via.x_nm / 1e6, via.y_nm / 1e6, 0.0)
        objects.append(SceneObject(
            id=f"3d_{via.id}", kind="mesh", layer="Via", vertices=verts, faces=faces,
            world_matrix=matrix, color="#C0C8CE", net_id=via.net_id, selectable=False))

    missing_models: list[dict[str, str]] = []
    for fp in board.footprints:
        comp = next((c for c in circuit.components if c.id == fp.component_id), None)
        try:
            footprint = resolve_footprint(fp.footprint_id)
        except NotFound:
            footprint = None
        height = fp.body_height_mm or (footprint.body_height_mm if footprint else 1.0) or 1.0
        model = footprint.model if footprint else None
        if model is None or model.kind == "none":
            fp_fidelity = "missing"
            missing_models.append({"component_id": fp.component_id,
                                   "reference": fp.reference,
                                   "reason": "No 3D model asset for this footprint."})
        else:
            fp_fidelity = model.fidelity
            if fp_fidelity != "exact_asset":
                missing_models.append({"component_id": fp.component_id,
                                       "reference": fp.reference,
                                       "reason": "Approximate model (simplified body)."})
        z0 = 0.0 if fp.side == "top" else -height
        cy = fp.y_nm / 1e6
        cx = fp.x_nm / 1e6
        if model and model.kind == "cylinder":
            w = max(fp.courtyard.w_nm, fp.courtyard.h_nm) / 1e6 * 0.55
            verts, faces = meshes.cylinder_mesh(w / 2, z0, z0 + height, 20)
        else:
            w = max(0.6, fp.courtyard.w_nm / 1e6 - 1.0)
            h = max(0.6, fp.courtyard.h_nm / 1e6 - 1.0)
            verts, faces = meshes.box_mesh(w, h, height, z0=z0)
        matrix = meshes.component_world_matrix(cx, cy, fp.rotation_deg, fp.side, height)
        objects.append(SceneObject(
            id=f"3d_body_{fp.component_id}", kind="mesh", layer="Component",
            vertices=verts, faces=faces, world_matrix=matrix,
            color="#6E988C" if fp.side == "top" else "#4F7A86",
            component_id=fp.component_id, ref=fp.reference, height_mm=height,
            fidelity=fp_fidelity, selectable=True,
            meta={"side": fp.side, "rotation_deg": fp.rotation_deg,
                  "model_id": model.model_id if model else None}))
        objects.append(SceneObject(
            id=f"3d_ref_{fp.component_id}", kind="text", layer="Silkscreen",
            x=cx, y=cy, w=0, h=0, text=fp.reference, font_size=1.6,
            color=COLORS["silk"], component_id=fp.component_id, selectable=False,
            world_matrix=meshes.mat_translate(cx, cy, z0 + height + 0.05)))

    return SceneSnapshot(
        project_id=project_id, design_version=version, design_version_key=version.key,
        scene_kind="pcb3d", objects=objects,
        bounds={"x": 0, "y": 0, "width": board.width_nm / 1e6,
                "height": board.height_nm / 1e6,
                "z": -thickness},
        coordinate_system={"units": "mm", "handedness": "right",
                           "x": "right", "y": "up", "z": "toward board top",
                           "top_surface_z": 0.0, "bottom_surface_z": -thickness,
                           "matrix": "row-major, p_world = M * [x,y,z,1]^T",
                           "datum": "board lower-left corner"},
        missing_models=missing_models)


def make_scene(project_id: str, version: DesignVersion, view: str,
               circuit: CircuitIR, schematic: SchematicIR, board: BoardIR) -> SceneSnapshot:
    if view == "schematic":
        return schematic_scene(project_id, version, circuit, schematic)
    if view == "pcb2d":
        return pcb2d_scene(project_id, version, circuit, board)
    if view == "pcb3d":
        return pcb3d_scene(project_id, version, circuit, board)
    raise ValueError(f"unknown view {view}")
