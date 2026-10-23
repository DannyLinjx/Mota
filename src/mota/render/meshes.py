"""Server-side 3D mesh generation in millimetres with authoritative matrices.

World frame: X right, Y up, Z toward the board top. Board top is Z=0 and the
bottom surface is Z=-thickness. Matrices are serialized row-major and multiply
column vectors: p_world = M * [x, y, z, 1]^T. The UI applies the matrix once and
never re-applies KiCad-side transforms.
"""

from __future__ import annotations

import math
from typing import Sequence

from mota.domain.models import BoardIR, CircuitIR


def mat_identity() -> list[float]:
    return [1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0]


def mat_translate(x: float, y: float, z: float) -> list[float]:
    return [1.0, 0.0, 0.0, x,
            0.0, 1.0, 0.0, y,
            0.0, 0.0, 1.0, z,
            0.0, 0.0, 0.0, 1.0]


def mat_rotate_z(degrees: float) -> list[float]:
    rad = math.radians(degrees)
    c, s = math.cos(rad), math.sin(rad)
    return [c, -s, 0.0, 0.0,
            s, c, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0]


def mat_mirror_x() -> list[float]:
    return [-1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0]


def mat_mul(a: Sequence[float], b: Sequence[float]) -> list[float]:
    out = [0.0] * 16
    for row in range(4):
        for col in range(4):
            out[row * 4 + col] = sum(a[row * 4 + k] * b[k * 4 + col] for k in range(4))
    return out


def box_mesh(w: float, h: float, d: float, *, z0: float = 0.0) -> tuple[list[list[float]], list[list[int]]]:
    """Axis-aligned box centred in XY with z from z0 to z0+d."""
    x, y = w / 2, h / 2
    vertices = [
        [-x, -y, z0], [x, -y, z0], [x, y, z0], [-x, y, z0],
        [-x, -y, z0 + d], [x, -y, z0 + d], [x, y, z0 + d], [-x, y, z0 + d],
    ]
    faces = [
        [0, 2, 1], [0, 3, 2],          # bottom
        [4, 5, 6], [4, 6, 7],          # top
        [0, 1, 5], [0, 5, 4],
        [1, 2, 6], [1, 6, 5],
        [2, 3, 7], [2, 7, 6],
        [3, 0, 4], [3, 4, 7],
    ]
    return vertices, faces


def cylinder_mesh(radius: float, z0: float, z1: float, segments: int = 20
                  ) -> tuple[list[list[float]], list[list[int]]]:
    vertices: list[list[float]] = []
    for z in (z0, z1):
        for index in range(segments):
            angle = 2 * math.pi * index / segments
            vertices.append([radius * math.cos(angle), radius * math.sin(angle), z])
    faces: list[list[int]] = []
    for index in range(segments):
        nxt = (index + 1) % segments
        faces.append([index, nxt, segments + nxt])
        faces.append([index, segments + nxt, segments + index])
    bottom_center = len(vertices)
    vertices.append([0.0, 0.0, z0])
    top_center = len(vertices)
    vertices.append([0.0, 0.0, z1])
    for index in range(segments):
        nxt = (index + 1) % segments
        faces.append([bottom_center, nxt, index])
        faces.append([top_center, segments + index, segments + nxt])
    return vertices, faces


def track_box(seg: tuple[int, int, int, int], width_mm: float, z0: float,
              thickness: float = 0.035) -> tuple[list[list[float]], list[list[int]], list[float]]:
    x1, y1, x2, y2 = (v / 1e6 for v in seg)
    length = math.hypot(x2 - x1, y2 - y1)
    if length < 1e-9:
        length = 0.001
    angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
    vertices, faces = box_mesh(length + width_mm, width_mm, thickness, z0=z0)
    matrix = mat_mul(mat_translate((x1 + x2) / 2, (y1 + y2) / 2, 0.0),
                     mat_rotate_z(angle))
    return vertices, faces, matrix


def board_mesh(board: BoardIR) -> tuple[list[list[float]], list[list[int]]]:
    return box_mesh(board.width_nm / 1e6, board.height_nm / 1e6,
                    board.thickness_nm / 1e6, z0=-board.thickness_nm / 1e6)


def component_world_matrix(x_mm: float, y_mm: float, rotation: int, side: str,
                           height_mm: float) -> list[float]:
    matrix = mat_translate(x_mm, y_mm, 0.0)
    matrix = mat_mul(matrix, mat_rotate_z(rotation))
    if side == "bottom":
        matrix = mat_mul(matrix, mat_mirror_x())
    return matrix
