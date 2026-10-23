"""Independent copper connectivity check.

Rebuilds pad/track/via connectivity from geometry only. It does not trust the
router's own success bookkeeping: a broken track must fail even when the net
list still claims the pads are connected.
"""

from __future__ import annotations

from dataclasses import dataclass

from mota.domain.geometry import (
    pad_contains, pad_segment_distance, point_segment_distance,
)
from mota.domain.models import BoardIR
from mota.domain.units import NM_PER_MM
from mota.domain.geometry import UnionFind

TOL_MM = 0.001


@dataclass
class NetConnectivity:
    net_id: str
    expected_pads: int
    groups: int
    unconnected_pads: list[str]
    connected: bool


def copper_groups(board: BoardIR) -> tuple[UnionFind, list[NetConnectivity]]:
    uf = UnionFind()
    pads = [p for p in board.pads if p.net_id]
    for pad in pads:
        uf.add(pad.id)
    for track in board.tracks:
        uf.add(track.id)
    for via in board.vias:
        uf.add(via.id)

    # Pads that share the same physical pin are the same copper node.
    pin_key: dict[tuple[str, str], str] = {}
    for pad in pads:
        key = (pad.component_id, pad.number)
        if key in pin_key:
            uf.union(pad.id, pin_key[key])
        else:
            pin_key[key] = pad.id

    by_net_tracks: dict[str, list] = {}
    for track in board.tracks:
        by_net_tracks.setdefault(track.net_id, []).append(track)

    for net_id, tracks in by_net_tracks.items():
        for index, a in enumerate(tracks):
            for b in tracks[index + 1:]:
                if a.layer != b.layer:
                    continue
                if _tracks_touch(a, b):
                    uf.union(a.id, b.id)
        for track in tracks:
            for pad in pads:
                if pad.net_id != net_id:
                    continue
                if _track_touches_pad(track, pad):
                    uf.union(track.id, pad.id)
            for via in board.vias:
                if via.net_id != net_id:
                    continue
                if _track_touches_point(track, via.x_nm, via.y_nm):
                    uf.union(track.id, via.id)
            for hole in board.holes:
                # pads/tracks must not enter an NPTH drill
                pass
    for via in board.vias:
        for pad in pads:
            if pad.net_id == via.net_id and pad_contains(pad, via.x_nm, via.y_nm):
                uf.union(via.id, pad.id)

    results: list[NetConnectivity] = []
    nets = sorted({p.net_id for p in pads})
    for net_id in nets:
        members = [p for p in pads if p.net_id == net_id]
        roots = {uf.find(p.id) for p in members}
        unconnected: list[str] = []
        if len(roots) > 1:
            sizes: dict[str, int] = {}
            for pad in members:
                sizes[uf.find(pad.id)] = sizes.get(uf.find(pad.id), 0) + 1
            main = max(sizes, key=sizes.get)
            unconnected = [p.id for p in members if uf.find(p.id) != main]
        results.append(NetConnectivity(net_id=net_id, expected_pads=len(members),
                                       groups=len(roots), unconnected_pads=unconnected,
                                       connected=len(roots) == 1))
    return uf, results


def _track_touches_pad(track, pad) -> bool:
    for segment in track.segments():
        if pad_segment_distance(pad, segment) <= TOL_MM:
            return True
        if any(pad_contains(pad, x, y, tolerance_nm=1) for x, y in track.points):
            return True
    return False


def _track_touches_point(track, x_nm: int, y_nm: int) -> bool:
    for x1, y1, x2, y2 in track.segments():
        distance = point_segment_distance(x_nm / NM_PER_MM, y_nm / NM_PER_MM,
                                          x1 / NM_PER_MM, y1 / NM_PER_MM,
                                          x2 / NM_PER_MM, y2 / NM_PER_MM)
        if distance <= track.width_nm / 2 / NM_PER_MM + TOL_MM:
            return True
    return False


def _tracks_touch(a, b) -> bool:
    from mota.domain.geometry import segment_segment_distance
    limit = (a.width_nm + b.width_nm) / 2 / NM_PER_MM + TOL_MM
    for seg_a in a.segments():
        for seg_b in b.segments():
            if segment_segment_distance(
                    [v / NM_PER_MM for v in seg_a],
                    [v / NM_PER_MM for v in seg_b]) <= limit:
                return True
    return False
