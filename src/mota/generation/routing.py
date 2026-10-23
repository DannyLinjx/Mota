"""Two-layer grid router with exact pad exits and a shared time budget.

The router is a search helper, not the authority on correctness: independent
geometric and connectivity validators re-check every produced track. Failure is
reported, never hidden by shrinking rules.
"""

from __future__ import annotations

import heapq
import math
import os
import time
from array import array
from dataclasses import dataclass, field
from typing import Callable

from mota.domain.geometry import pad_contains
from mota.domain.models import BoardIR, RatsnestLine, Track, Via
from mota.domain.units import NM_PER_MM, mm


def route_budget_seconds() -> float:
    """Wall-clock routing budget; operators can raise it for slow machines."""
    try:
        return float(os.environ.get("MOTA_ROUTE_BUDGET_SECONDS", "30"))
    except ValueError:
        return 30.0


@dataclass
class RouteConstraints:
    grid_nm: int = mm(0.127)
    clearance_nm: int = mm(0.25)
    signal_width_nm: int = mm(0.30)
    power_width_nm: int = mm(0.60)
    edge_nm: int = mm(0.50)
    hole_clearance_nm: int = mm(0.25)
    via_outer_nm: int = mm(0.80)
    via_drill_nm: int = mm(0.40)
    budget_seconds: float = field(default_factory=route_budget_seconds)

    @classmethod
    def from_profile(cls, profile) -> "RouteConstraints":
        return cls(grid_nm=profile.grid_nm, clearance_nm=profile.clearance_nm,
                   signal_width_nm=profile.signal_width_nm,
                   power_width_nm=profile.power_width_nm,
                   edge_nm=profile.edge_nm,
                   hole_clearance_nm=profile.hole_clearance_nm,
                   via_outer_nm=profile.via_outer_nm,
                   via_drill_nm=profile.via_drill_nm,
                   budget_seconds=profile.budget_seconds)


class Cancelled(Exception):
    pass


class Grid:
    def __init__(self, width_nm: int, height_nm: int, step_nm: int) -> None:
        self.step = step_nm
        self.nx = max(2, int(width_nm // step_nm) + 1)
        self.ny = max(2, int(height_nm // step_nm) + 1)

    def node(self, layer: int, ix: int, iy: int) -> int:
        return (layer * self.ny + iy) * self.nx + ix

    def decode(self, node: int) -> tuple[int, int, int]:
        layer, rem = divmod(node, self.nx * self.ny)
        iy, ix = divmod(rem, self.nx)
        return layer, ix, iy

    def pos(self, ix: int, iy: int) -> tuple[int, int]:
        return ix * self.step, iy * self.step

    def nearest(self, x_nm: int, y_nm: int) -> tuple[int, int]:
        return (max(0, min(self.nx - 1, round(x_nm / self.step))),
                max(0, min(self.ny - 1, round(y_nm / self.step))))


def _pad_layers(pad) -> tuple[int, ...]:
    layers = set(pad.layers or [])
    out: list[int] = []
    if "F.Cu" in layers or pad.pad_type in ("tht", "npth"):
        out.append(0)
    if "B.Cu" in layers or pad.pad_type in ("tht", "npth"):
        out.append(1)
    return tuple(out or (0,))


def _pad_rect_mm(pad) -> tuple[float, float, float, float]:
    x = pad.x_nm / NM_PER_MM
    y = pad.y_nm / NM_PER_MM
    w = pad.w_nm / NM_PER_MM
    h = pad.h_nm / NM_PER_MM
    return x - w / 2, y - h / 2, x + w / 2, y + h / 2


def route_board(board: BoardIR, constraints: RouteConstraints | None = None,
                net_classes: dict[str, str] | None = None,
                cancel: Callable[[], bool] | None = None,
                time_budget: float | None = None) -> tuple[BoardIR, list[str]]:
    c = constraints or RouteConstraints()
    if time_budget is not None:
        c.budget_seconds = time_budget
    classes = net_classes or {}

    def net_class(net_id: str) -> str:
        known = classes.get(net_id)
        if known:
            return known
        name = net_id.lower()
        if "gnd" in name:
            return "ground"
        if "vcc" in name or name.startswith("net_+") or "vdd" in name:
            return "power"
        return "signal"

    grid = Grid(board.width_nm, board.height_nm, c.grid_nm)
    deadline = time.monotonic() + c.budget_seconds
    step_mm = grid.step / NM_PER_MM

    width_by_net: dict[str, int] = {}
    for pad in board.pads:
        if not pad.net_id:
            continue
        width_by_net.setdefault(
            pad.net_id,
            c.power_width_nm if net_class(pad.net_id) in ("power", "ground")
            else c.signal_width_nm)

    pads_by_net: dict[str, list] = {}
    for pad in board.pads:
        if pad.net_id:
            pads_by_net.setdefault(pad.net_id, []).append(pad)

    centers = {fp.component_id: (fp.x_nm, fp.y_nm) for fp in board.footprints}
    escape_nm = mm(0.5)
    escape_len: dict[str, int] = {}

    def escape_point(pad, length_nm: int | None = None) -> tuple[int, int]:
        """Short lead from an SMD pad outward, clear of the pin row."""
        if pad.pad_type != "smd" or pad.component_id not in centers:
            return pad.x_nm, pad.y_nm
        lead = escape_len.get(pad.id, length_nm if length_nm is not None else escape_nm)
        cx, cy = centers[pad.component_id]
        dx = pad.x_nm - cx
        dy = pad.y_nm - cy
        if abs(dx) >= abs(dy):
            sign = 1 if dx >= 0 else -1
            return pad.x_nm + sign * (pad.w_nm // 2 + lead), pad.y_nm
        sign = 1 if dy >= 0 else -1
        return pad.x_nm, pad.y_nm + sign * (pad.h_nm // 2 + lead)

    tracks: list[Track] = list(board.tracks)
    vias: list[Via] = list(board.vias)
    unrouted: list[str] = []

    def _max_seq(rows, prefix: str) -> int:
        highest = 0
        for row in rows:
            try:
                highest = max(highest, int(row.id.rsplit("_", 1)[-1]))
            except (ValueError, AttributeError):
                continue
        return highest

    track_seq = _max_seq(tracks, "track")
    via_seq = _max_seq(vias, "via")

    pads_by_component: dict[str, list] = {}
    for pad in board.pads:
        if pad.pad_type == "smd" and pad.net_id:
            pads_by_component.setdefault(pad.component_id, []).append(pad)

    def net_priority(net_id: str) -> tuple[int, int, str]:
        # Signal, then supplies, then ground. Signals own the fine-pitch exit
        # lanes first; ground is the last dense net and is reported honestly
        # when the remaining slots in the budget cannot take it.
        rank = {"signal": 0, "power": 1, "ground": 2}.get(net_class(net_id), 0)
        hard = 0 if net_id in fine_pitch_nets else 1
        return (hard, rank, -len(pads_by_net[net_id]), net_id)

    # Pads of a 0.5 mm pitch package have exactly one legal exit lane. Nets that
    # touch them must claim that lane while the board is still empty.
    fine_pitch_nets: set[str] = set()
    for component_id, rows in pads_by_component.items():
        if len(rows) < 8:
            continue
        nearest = min(min((math.hypot(pad.x_nm - other.x_nm, pad.y_nm - other.y_nm)
                           for other in rows if other is not pad), default=1 << 60)
                      for pad in rows)
        if nearest <= mm(0.8):
            fine_pitch_nets.update(pad.net_id for pad in rows if pad.net_id)

    order = sorted(pads_by_net, key=net_priority)
    if os.environ.get("MOTA_ROUTE_ONLY"):
        order = [name for name in order if name in os.environ["MOTA_ROUTE_ONLY"].split(",")]

    # Fan-out: reserve a short outward lead on every fine-pitch pad before any
    # long route runs. Without this the escape lanes of a 0.5 mm LQFP are taken
    # by whichever net happens to be routed first.
    from mota.validation.geometry import check_clearance as _check_clearance
    for component_id, pads in pads_by_component.items():
        if len(pads) < 8:
            continue
        pitches = []
        for pad in pads:
            nearest = min((math.hypot(pad.x_nm - other.x_nm, pad.y_nm - other.y_nm)
                           for other in pads if other is not pad), default=1 << 60)
            pitches.append(nearest)
        if min(pitches, default=1 << 60) > mm(0.8):
            continue
        for pad in sorted(pads, key=lambda row: (row.y_nm, row.x_nm)):
            for lead in (mm(3.0), mm(2.0), mm(1.2), mm(0.5), mm(0.25)):
                tip = escape_point(pad, lead)
                if tip == (pad.x_nm, pad.y_nm):
                    break
                stub = Track(id=f"track_{track_seq + 1:04d}", net_id=pad.net_id,
                             layer="F.Cu",
                             width_nm=c.power_width_nm
                             if net_class(pad.net_id) in ("power", "ground")
                             else c.signal_width_nm,
                             points=[[pad.x_nm, pad.y_nm], [tip[0], tip[1]]])
                candidate = board.model_copy(update={"tracks": tracks + [stub]})
                new_ids = {stub.id}
                if any(any(object_id in new_ids for object_id in violation.object_ids)
                       for violation in _check_clearance(candidate,
                                                         c.clearance_nm / NM_PER_MM)):
                    continue
                tracks.append(stub)
                escape_len[pad.id] = lead
                track_seq += 1
                break

    def blocked_cells(width_nm: int, owner_net: str) -> tuple[set[int], set[int]]:
        sets = (set(), set())
        margin_mm = (c.clearance_nm + width_nm / 2) / NM_PER_MM
        cell = step_mm

        def mark_all(cx: float, cy: float, radius: float) -> None:
            x0 = max(0, int((cx - radius) / cell))
            x1 = min(grid.nx - 1, int((cx + radius) / cell) + 1)
            y0 = max(0, int((cy - radius) / cell))
            y1 = min(grid.ny - 1, int((cy + radius) / cell) + 1)
            for ix in range(x0, x1 + 1):
                px = ix * cell
                for iy in range(y0, y1 + 1):
                    py = iy * cell
                    if (px - cx) ** 2 + (py - cy) ** 2 <= (radius + cell * 0.5) ** 2:
                        node0 = grid.node(0, ix, iy)
                        node1 = grid.node(1, ix, iy)
                        sets[0].add(node0)
                        sets[1].add(node1)

        def mark_segment(x1: float, y1: float, x2: float, y2: float, radius: float,
                         layers: tuple[int, ...]) -> None:
            length = math.hypot(x2 - x1, y2 - y1)
            samples = max(1, int(length / (cell * 0.5)) + 1)
            for index in range(samples + 1):
                t = index / samples
                cx = x1 + (x2 - x1) * t
                cy = y1 + (y2 - y1) * t
                x0 = max(0, int((cx - radius) / cell))
                x1i = min(grid.nx - 1, int((cx + radius) / cell) + 1)
                y0 = max(0, int((cy - radius) / cell))
                y1i = min(grid.ny - 1, int((cy + radius) / cell) + 1)
                for ix in range(x0, x1i + 1):
                    px = ix * cell
                    for iy in range(y0, y1i + 1):
                        py = iy * cell
                        if (px - cx) ** 2 + (py - cy) ** 2 <= (radius + cell * 0.5) ** 2:
                            for layer in layers:
                                sets[layer].add(grid.node(layer, ix, iy))

        def mark_rect(x0: float, y0: float, x1: float, y1: float,
                      layers: tuple[int, ...]) -> None:
            ix0 = max(0, int(x0 / cell))
            ix1 = min(grid.nx - 1, int(x1 / cell) + 1)
            iy0 = max(0, int(y0 / cell))
            iy1 = min(grid.ny - 1, int(y1 / cell) + 1)
            for ix in range(ix0, ix1 + 1):
                px = ix * cell
                if px < x0 - cell * 0.5 or px > x1 + cell * 0.5:
                    continue
                for iy in range(iy0, iy1 + 1):
                    py = iy * cell
                    if py < y0 - cell * 0.5 or py > y1 + cell * 0.5:
                        continue
                    for layer in layers:
                        sets[layer].add(grid.node(layer, ix, iy))

        for pad in board.pads:
            if pad.net_id == owner_net:
                continue
            x0, y0, x1, y1 = _pad_rect_mm(pad)
            mark_rect(x0 - margin_mm, y0 - margin_mm,
                      x1 + margin_mm, y1 + margin_mm, _pad_layers(pad))
        for hole in board.holes:
            mark_all(hole.x_nm / NM_PER_MM, hole.y_nm / NM_PER_MM,
                     (hole.drill_nm / 2 + c.hole_clearance_nm + width_nm / 2) / NM_PER_MM)
        for track in tracks:
            if track.net_id == owner_net:
                continue
            layer = 0 if track.layer == "F.Cu" else 1
            radius = (track.width_nm / 2 + c.clearance_nm + width_nm / 2) / NM_PER_MM
            for seg in track.segments():
                mark_segment(seg[0] / NM_PER_MM, seg[1] / NM_PER_MM,
                             seg[2] / NM_PER_MM, seg[3] / NM_PER_MM, radius, (layer,))
        for via in vias:
            if via.net_id == owner_net:
                continue
            mark_all(via.x_nm / NM_PER_MM, via.y_nm / NM_PER_MM,
                     (via.outer_nm / 2 + c.clearance_nm + width_nm / 2) / NM_PER_MM)
        edge_cells = int(((c.edge_nm + width_nm / 2) / NM_PER_MM) / cell) + 1
        for ix in range(grid.nx):
            for iy in list(range(min(edge_cells, grid.ny))) + list(
                    range(max(0, grid.ny - edge_cells), grid.ny)):
                sets[0].add(grid.node(0, ix, iy))
                sets[1].add(grid.node(1, ix, iy))
        for iy in range(grid.ny):
            for ix in list(range(min(edge_cells, grid.nx))) + list(
                    range(max(0, grid.nx - edge_cells), grid.nx)):
                sets[0].add(grid.node(0, ix, iy))
                sets[1].add(grid.node(1, ix, iy))
        return sets[0], sets[1]

    def pad_nodes(pad) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        ix_center, iy_center = grid.nearest(pad.x_nm, pad.y_nm)
        radius_cells = int(max(pad.w_nm, pad.h_nm) / 2 / grid.step) + 1
        for ix in range(max(0, ix_center - radius_cells), min(grid.nx, ix_center + radius_cells + 1)):
            for iy in range(max(0, iy_center - radius_cells), min(grid.ny, iy_center + radius_cells + 1)):
                if pad_contains(pad, ix * grid.step, iy * grid.step):
                    out.append((ix, iy))
        if not out:
            out.append((ix_center, iy_center))
        out.sort(key=lambda p: (p[0] * grid.step - pad.x_nm) ** 2
                 + (p[1] * grid.step - pad.y_nm) ** 2)
        return out

    def escape_nodes(pad, blocked0: set[int], blocked1: set[int]) -> list[int]:
        """Grid nodes at a pad's short outward lead, clear of the pin row."""
        x, y = escape_point(pad)
        ix0, iy0 = grid.nearest(x, y)
        blocked = (blocked0, blocked1)
        for radius in range(0, 6):
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    if max(abs(dx), abs(dy)) != radius:
                        continue
                    ix = max(0, min(grid.nx - 1, ix0 + dx))
                    iy = max(0, min(grid.ny - 1, iy0 + dy))
                    nodes = [grid.node(layer, ix, iy) for layer in _pad_layers(pad)]
                    if all(node not in blocked[layer] for node, layer
                           in zip(nodes, _pad_layers(pad))):
                        return nodes
        return []

    search_id = [0]
    _size = grid.nx * grid.ny * 2
    g_cost = array("i", bytes(4 * _size))
    parent = array("i", bytes(4 * _size))
    stamp = array("i", bytes(4 * _size))

    def astar(start_nodes: list[int], goal_nodes: list[tuple[int, int]],
              blocked0: set[int], blocked1: set[int], owner_net: str,
              search_deadline: float,
              bounds: tuple[int, int, int, int] | None = None) -> list[int] | None:
        blocked = (blocked0, blocked1)
        gx1 = min(cell[0] for cell in goal_nodes)
        gx2 = max(cell[0] for cell in goal_nodes)
        gy1 = min(cell[1] for cell in goal_nodes)
        gy2 = max(cell[1] for cell in goal_nodes)
        goal_xy = set(goal_nodes)
        start_set = set(start_nodes)
        if bounds is None:
            bx0, by0, bx1, by1 = 0, 0, grid.nx - 1, grid.ny - 1
        else:
            bx0, by0, bx1, by1 = bounds

        def h(ix: int, iy: int) -> int:
            dx = gx1 - ix if ix < gx1 else (ix - gx2 if ix > gx2 else 0)
            dy = gy1 - iy if iy < gy1 else (iy - gy2 if iy > gy2 else 0)
            # Weighted heuristic: trades a little path length for far fewer
            # expansions, which is what a Python grid search needs. Every
            # result is still re-checked geometrically before it is kept.
            return 2 * (dx + dy)

        search_id[0] += 1
        sid = search_id[0]
        heap: list[tuple[int, int, int, int, int]] = []
        for node in start_nodes:
            _layer, ix, iy = grid.decode(node)
            g_cost[node] = 1
            parent[node] = -1
            stamp[node] = sid
            heapq.heappush(heap, (h(ix, iy), node, ix, iy, 0))
        expansions = 0
        while heap:
            _f, node, ix, iy, cost = heapq.heappop(heap)
            if stamp[node] != sid or g_cost[node] != cost + 1:
                continue
            layer = 0 if node < grid.nx * grid.ny else 1
            if (ix, iy) in goal_xy:
                path = [node]
                while parent[node] != -1:
                    node = parent[node]
                    path.append(node)
                path.reverse()
                return path
            expansions += 1
            if expansions % 4096 == 0 and (time.monotonic() > search_deadline
                                           or time.monotonic() > deadline):
                raise TimeoutError("routing slice exhausted")
            blocked_layer = blocked[layer]
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = ix + dx, iy + dy
                if nx < bx0 or ny < by0 or nx > bx1 or ny > by1:
                    continue
                nn = grid.node(layer, nx, ny)
                if nn in blocked_layer and nn not in start_set:
                    continue
                if stamp[nn] == sid and g_cost[nn] <= cost + 2:
                    continue
                g_cost[nn] = cost + 2
                parent[nn] = node
                stamp[nn] = sid
                heapq.heappush(heap, (cost + 1 + h(nx, ny), nn, nx, ny, cost + 1))
            if ix < bx0 or iy < by0 or ix > bx1 or iy > by1:
                continue
            other = 1 - layer
            nn = grid.node(other, ix, iy)
            if nn not in blocked[other]:
                wx, wy = grid.pos(ix, iy)
                if not via_allowed(wx, wy, owner_net):
                    continue
                ng = cost + 8
                if stamp[nn] == sid and g_cost[nn] <= ng + 1:
                    continue
                g_cost[nn] = ng + 1
                parent[nn] = node
                stamp[nn] = sid
                heapq.heappush(heap, (ng + h(ix, iy), nn, ix, iy, ng))
        return None

    def via_allowed(x_nm: int, y_nm: int, owner_net: str) -> bool:
        """Exact geometric check for via copper against other nets and holes."""
        outer_r = c.via_outer_nm / 2 / NM_PER_MM
        px, py = x_nm / NM_PER_MM, y_nm / NM_PER_MM
        edge = c.edge_nm / NM_PER_MM + outer_r
        if px < edge or py < edge or px > board.width_nm / NM_PER_MM - edge \
                or py > board.height_nm / NM_PER_MM - edge:
            return False
        from types import SimpleNamespace
        from mota.domain.geometry import pad_distance, point_segment_distance

        probe = SimpleNamespace(id="?via", component_id="?", number="?",
                                net_id=owner_net, x_nm=x_nm, y_nm=y_nm,
                                w_nm=c.via_outer_nm, h_nm=c.via_outer_nm,
                                shape="circle", layers=["F.Cu", "B.Cu"])
        clearance = c.clearance_nm / NM_PER_MM
        for pad in board.pads:
            if pad.net_id == owner_net:
                continue
            if pad_distance(pad, probe) + 1e-9 < clearance:
                return False
        for track in tracks:
            if track.net_id == owner_net:
                continue
            for seg in track.segments():
                distance = point_segment_distance(px, py, seg[0] / NM_PER_MM,
                                                  seg[1] / NM_PER_MM,
                                                  seg[2] / NM_PER_MM,
                                                  seg[3] / NM_PER_MM) \
                    - track.width_nm / 2 / NM_PER_MM
                if distance - outer_r + 1e-9 < clearance:
                    return False
        for other in vias:
            if other.net_id == owner_net:
                continue
            distance = math.hypot(px - other.x_nm / NM_PER_MM,
                                  py - other.y_nm / NM_PER_MM) \
                - other.outer_nm / 2 / NM_PER_MM
            if distance - outer_r + 1e-9 < clearance:
                return False
        for hole in board.holes:
            distance = math.hypot(px - hole.x_nm / NM_PER_MM,
                                  py - hole.y_nm / NM_PER_MM) \
                - hole.drill_nm / 2 / NM_PER_MM
            if distance - outer_r + 1e-9 < c.hole_clearance_nm / NM_PER_MM:
                return False
        return True

    # Round-robin over pending sub-connections. Each search gets its own time
    # slice so one large net (usually GND) cannot consume the whole budget, and
    # partial copper from earlier successes is kept.
    state: dict[str, list] = {net: [pads[0]] for net, pads in pads_by_net.items()
                              if len(pads) > 1}
    pending: dict[str, list] = {net: list(pads[1:]) for net, pads in pads_by_net.items()
                                if len(pads) > 1}
    failures: dict[str, str] = {}
    blocked_cache: dict[tuple[str, int], tuple[set[int], set[int]]] = {}
    timing = {"mask": 0.0, "search": 0.0, "mask_calls": 0, "searches": 0}

    def anchor_for(node: int, anchors: list):
        _layer, ix, iy = grid.decode(node)
        x, y = grid.pos(ix, iy)
        for pad in anchors:
            if (abs(x - pad.x_nm) <= pad.w_nm / 2 + grid.step
                    and abs(y - pad.y_nm) <= pad.h_nm / 2 + grid.step):
                return pad
        return min(anchors, key=lambda pad: (pad.x_nm - x) ** 2 + (pad.y_nm - y) ** 2)

    for _round in range(400):
        progressed = False
        attempted = 0
        pending_total = sum(len(rows) for rows in pending.values()) or 1
        # Eight passes per round in priority order. A big net such as GND still
        # gets one slot per pass and can keep its legal copper between rounds;
        # no single net can monopolise a pass.
        for net_id in order * 8:
            if not pending.get(net_id):
                continue
            attempted += 1
            if cancel and cancel():
                raise Cancelled()
            if time.monotonic() > deadline:
                break
            width = width_by_net[net_id]
            key = (net_id, width)
            if key not in blocked_cache:
                _t0 = time.monotonic()
                blocked_cache[key] = blocked_cells(width, net_id)
                timing["mask"] += time.monotonic() - _t0
                timing["mask_calls"] += 1
            blocked0, blocked1 = blocked_cache[key]
            anchors = state[net_id]
            target = min(pending[net_id],
                         key=lambda pad: min((pad.x_nm - anchor.x_nm) ** 2
                                             + (pad.y_nm - anchor.y_nm) ** 2
                                             for anchor in anchors))
            remaining = max(0.1, deadline - time.monotonic())
            slice_s = min(6.0, max(0.4, remaining / pending_total))
            accepted = False
            for use_escape in (True, False):
                starts: list[int] = []
                node_pad: dict[int, object] = {}
                node_is_escape: dict[int, bool] = {}
                for pad in anchors:
                    nodes = (escape_nodes(pad, blocked0, blocked1)
                             if use_escape and pad.pad_type == "smd" else [])
                    if nodes:
                        for node in nodes:
                            node_pad[node] = pad
                            node_is_escape[node] = True
                        starts.extend(nodes)
                        continue
                    cells = pad_nodes(pad)
                    for layer in _pad_layers(pad):
                        for ix, iy in cells:
                            node = grid.node(layer, ix, iy)
                            node_pad.setdefault(node, pad)
                            node_is_escape.setdefault(node, False)
                            starts.append(node)
                escape_goal = (escape_nodes(target, blocked0, blocked1)
                               if use_escape and target.pad_type == "smd" else [])
                goal_escape_cells = {(grid.decode(node)[1], grid.decode(node)[2])
                                     for node in escape_goal}
                goal_cells = list(goal_escape_cells) + list(pad_nodes(target))
                margin_cells = int(mm(12.0) / grid.step)
                all_cells = [(grid.decode(node)[1], grid.decode(node)[2])
                             for node in starts] + list(goal_cells)
                bounds = (max(0, min(c[0] for c in all_cells) - margin_cells),
                          max(0, min(c[1] for c in all_cells) - margin_cells),
                          min(grid.nx - 1, max(c[0] for c in all_cells) + margin_cells),
                          min(grid.ny - 1, max(c[1] for c in all_cells) + margin_cells))
                def commit(path_nodes: list[int]) -> bool:
                    nonlocal track_seq, via_seq, progressed, accepted
                    start_node = path_nodes[0]
                    start_pad_sel = node_pad.get(start_node) or anchor_for(start_node, anchors)
                    start_esc = node_is_escape.get(start_node, False)
                    end_esc = (grid.decode(path_nodes[-1])[1],
                               grid.decode(path_nodes[-1])[2]) in goal_escape_cells
                    new_tracks, new_vias = _build_tracks(
                        path_nodes, start_pad_sel, target, net_id, width, grid,
                        track_seq, via_seq,
                        start_point=(escape_point(start_pad_sel)
                                     if start_esc and start_pad_sel.pad_type == "smd" else None),
                        end_point=(escape_point(target)
                                   if end_esc and target.pad_type == "smd" else None),
                        via_outer_nm=c.via_outer_nm, via_drill_nm=c.via_drill_nm)
                    from mota.validation.geometry import check_clearance
                    candidate = board.model_copy(update={
                        "tracks": tracks + new_tracks, "vias": vias + new_vias})
                    new_ids = {row.id for row in new_tracks} | {row.id for row in new_vias}
                    illegal = [violation for violation in
                               check_clearance(candidate, c.clearance_nm / NM_PER_MM)
                               if any(object_id in new_ids
                                      for object_id in violation.object_ids)]
                    if illegal:
                        if os.environ.get("MOTA_ROUTE_DEBUG"):
                            first = illegal[0]
                            owner = {row.id: row.net_id for row in tracks + new_tracks}
                            print(f"reject {net_id} escape={use_escape} "
                                  f"{first.message} ids="
                                  f"{[(oid, owner.get(oid)) for oid in first.object_ids]}",
                                  flush=True)
                        return False
                    tracks.extend(new_tracks)
                    vias.extend(new_vias)
                    track_seq += len(new_tracks)
                    via_seq += len(new_vias)
                    state[net_id].append(target)
                    pending[net_id].remove(target)
                    failures.pop(net_id, None)
                    blocked_cache.clear()
                    progressed = True
                    accepted = True
                    return True

                _t0 = time.monotonic()
                hit = False
                if starts and goal_cells and time.monotonic() < deadline:
                    near_start = min(starts, key=lambda node: min(
                        (grid.decode(node)[1] - cell[0]) ** 2
                        + (grid.decode(node)[2] - cell[1]) ** 2 for cell in goal_cells))
                    near_goal = min(goal_cells, key=lambda cell: (
                        grid.decode(near_start)[1] - cell[0]) ** 2
                        + (grid.decode(near_start)[2] - cell[1]) ** 2)
                    span = abs(grid.decode(near_start)[1] - near_goal[0]) \
                        + abs(grid.decode(near_start)[2] - near_goal[1])
                    if span <= int(mm(30.0) / grid.step):
                        for route_order in ("hv", "vh"):
                            hit = commit(_direct_path(near_start, near_goal, grid,
                                                      route_order))
                            if hit:
                                break
                    if not hit and time.monotonic() < deadline:
                        for route_order in ("hv", "vh"):
                            hit = commit(_direct_path(
                                near_start, near_goal, grid, route_order,
                                haul_layer=1 - grid.decode(near_start)[0]))
                            if hit:
                                break
                if hit:
                    break
                try:
                    path = astar(starts, goal_cells, blocked0, blocked1, net_id,
                                 time.monotonic() + slice_s, bounds=bounds)
                    timing["search"] += time.monotonic() - _t0
                    timing["searches"] += 1
                except TimeoutError:
                    timing["search"] += time.monotonic() - _t0
                    timing["searches"] += 1
                    failures[net_id] = "search_timeout"
                    continue
                if not path:
                    failures[net_id] = "blocked_by_rules"
                    if os.environ.get("MOTA_ROUTE_NET") == net_id \
                            or os.environ.get("MOTA_ROUTE_NET") == "*":
                        print(f"no path {net_id} starts={len(starts)} "
                              f"blocked_starts={sum(1 for n in starts if n in (blocked0, blocked1)[grid.decode(n)[0]])} "
                              f"goals={len(goal_cells)} "
                              f"blocked_goals={[c for c in goal_cells if grid.node(0, *c) in blocked0][:3]} "
                              f"bounds={bounds} target=({target.x_nm/1e6:.2f},{target.y_nm/1e6:.2f})",
                              flush=True)
                    continue
                if os.environ.get("MOTA_ROUTE_DEBUG"):
                    blocked = (blocked0, blocked1)
                    bad = [node for node in path[1:]
                           if node in blocked[grid.decode(node)[0]]]
                    if bad:
                        print(f"path {net_id} crosses {len(bad)} blocked nodes; "
                              f"first={grid.decode(bad[0])}", flush=True)
                if commit(path):
                    break
                failures[net_id] = "geometry_conflict"
            if not accepted:
                continue
        if os.environ.get("MOTA_ROUTE_DEBUG"):
            print(f"round {_round} progressed={progressed} attempted={attempted} "
                  f"order={len(order)} pending={len(pending)}/"
                  f"{sum(len(v) for v in pending.values())} "
                  f"t={time.monotonic() - (deadline - c.budget_seconds):.1f}s",
                  flush=True)
        if not progressed or time.monotonic() > deadline:
            break

    unrouted = sorted(net for net, rows in pending.items() if rows)
    if os.environ.get("MOTA_ROUTE_DEBUG"):
        print(f"timing mask={timing['mask']:.1f}s/{timing['mask_calls']} "
              f"search={timing['search']:.1f}s/{timing['searches']} "
              f"pending={ {k: len(v) for k, v in pending.items() if v} } "
              f"failures={failures}", flush=True)

    ratsnest: list[RatsnestLine] = []
    for net_id in unrouted:
        pads = pads_by_net.get(net_id, [])
        for a, b in zip(pads, pads[1:]):
            ratsnest.append(RatsnestLine(net_id=net_id, x1_nm=a.x_nm, y1_nm=a.y_nm,
                                         x2_nm=b.x_nm, y2_nm=b.y_nm))
    board = board.model_copy(update={
        "tracks": tracks, "vias": vias, "ratsnest": ratsnest,
        "unrouted_nets": unrouted})
    return board, unrouted


def _direct_path(start_node: int, goal_cell: tuple[int, int], grid: Grid,
                 order: str = "hv", haul_layer: int | None = None) -> list[int]:
    """Two-segment candidate; the DRC decides whether it is legal.

    With ``haul_layer`` the long leg runs on that layer and two vias bring it
    back to the pads' layer, which is how a mostly empty bottom copper layer
    is actually usable for a two-layer board.
    """
    layer, ix0, iy0 = grid.decode(start_node)
    ix1, iy1 = goal_cell
    run_layer = layer if haul_layer is None else haul_layer

    def walk(a: int, b: int) -> list[int]:
        if a == b:
            return []
        step = 1 if b > a else -1
        return list(range(a + step, b + step, step))

    nodes = [grid.node(layer, ix0, iy0)]
    if haul_layer is not None:
        nodes.append(grid.node(run_layer, ix0, iy0))
    if order == "hv":
        nodes += [grid.node(run_layer, ix, iy0) for ix in walk(ix0, ix1)]
        nodes += [grid.node(run_layer, ix1, iy) for iy in walk(iy0, iy1)]
    else:
        nodes += [grid.node(run_layer, ix0, iy) for iy in walk(iy0, iy1)]
        nodes += [grid.node(run_layer, ix, iy1) for ix in walk(ix0, ix1)]
    if haul_layer is not None:
        nodes.append(grid.node(layer, ix1, iy1))
    return nodes


def _build_tracks(path: list[int], start_pad, target_pad, net_id: str, width: int,
                  grid: Grid, track_seq: int, via_seq: int,
                  *, start_point: tuple[int, int] | None = None,
                  end_point: tuple[int, int] | None = None,
                  via_outer_nm: int | None = None,
                  via_drill_nm: int | None = None
                  ) -> tuple[list[Track], list[Via]]:
    points_by_layer: list[tuple[int, list[list[int]]]] = []
    current_layer = grid.decode(path[0])[0]
    start_xy = list(start_point or (start_pad.x_nm, start_pad.y_nm))
    end_xy = list(end_point or (target_pad.x_nm, target_pad.y_nm))
    chunk: list[list[int]] = [[start_pad.x_nm, start_pad.y_nm]]
    if start_xy != chunk[0]:
        chunk.append(start_xy)
    last_xy = start_xy
    via_points: list[tuple[int, int]] = []
    for node in path:
        layer, ix, iy = grid.decode(node)
        x, y = grid.pos(ix, iy)
        if layer != current_layer:
            via_points.append((x, y))
            chunk.append([x, y])
            points_by_layer.append((current_layer, _merge_collinear(chunk)))
            chunk = [[x, y]]
            current_layer = layer
            last_xy = [x, y]
            continue
        if [x, y] != last_xy:
            chunk.append([x, y])
            last_xy = [x, y]
    if end_xy != last_xy:
        chunk.append(end_xy)
        last_xy = end_xy
    pad_center = [target_pad.x_nm, target_pad.y_nm]
    if pad_center != last_xy:
        chunk.append(pad_center)
    chunk = _merge_collinear(chunk)
    if len(chunk) >= 2:
        points_by_layer.append((current_layer, chunk))

    tracks: list[Track] = []
    for index, (layer, points) in enumerate(points_by_layer, start=1):
        tracks.append(Track(id=f"track_{track_seq + index:04d}", net_id=net_id,
                            layer="F.Cu" if layer == 0 else "B.Cu",
                            width_nm=width, points=points))
    vias = [Via(id=f"via_{via_seq + index:04d}", net_id=net_id, x_nm=x, y_nm=y,
                **({"outer_nm": via_outer_nm} if via_outer_nm else {}),
                **({"drill_nm": via_drill_nm} if via_drill_nm else {}))
            for index, (x, y) in enumerate(via_points, start=1)]
    return tracks, vias


def _merge_collinear(points: list[list[int]]) -> list[list[int]]:
    if len(points) <= 2:
        return points
    out = [points[0]]
    for point in points[1:]:
        if point == out[-1]:
            continue
        if len(out) >= 2:
            ax, ay = out[-2]
            bx, by = out[-1]
            cx, cy = point
            if (ax == bx == cx) or (ay == by == cy):
                out[-1] = point
                continue
        out.append(point)
    return out
