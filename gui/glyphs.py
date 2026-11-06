"""퀵 칩 그림 — 확장과 같은 extension/editor/glyphs.json 의 선·원·경로 데이터를 SVG 로 만들어 QtSvg 로 그린다.

모양 하나: {box: [w, h], w: 선 굵기, parts: [...], clip?: {y: [위, 아래], parts}}
  part: {l: [x, y, ...]} 꺾은선 · {c: [cx, cy, r]} 원 · {d: "..."} 경로 · {r: [x, y, w, h]} 사각형, 옵션 fill·faint·w
Qt SVG 는 clipPath 를 그리지 않아서 clip 부분은 그림판의 자르기 사각형 안에 따로 그린다.
파일이 없거나 읽을 수 없으면 그림 없이(글자 칩으로) 동작한다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

PATH = Path(__file__).resolve().parent.parent / "extension" / "editor" / "glyphs.json"
GROUPS = ("framing", "view", "gaze", "pose")
FAINT = 0.35
RATIO = 2               # 화면 배율이 커도 흐리지 않게 두 배로 그려 둔다


@lru_cache(maxsize=None)
def _data(path: Path = PATH) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def spec(group: str, tag: str) -> Optional[Mapping[str, Any]]:
    """퀵 그룹(framing·view·gaze·pose)의 태그 그림 데이터. 없으면 None."""
    specs = _data().get(group) if group in GROUPS else None
    found = specs.get(tag) if isinstance(specs, dict) else None
    return found if isinstance(found, dict) else None


def _num(value: float) -> str:
    return f"{value:g}"


def _part(part: Mapping[str, Any], width: float) -> str:
    if "l" in part:
        node = f'<polyline points="{" ".join(_num(v) for v in part["l"])}"'
    elif "c" in part:
        cx, cy, r = part["c"]
        node = f'<circle cx="{_num(cx)}" cy="{_num(cy)}" r="{_num(r)}"'
    elif "d" in part:
        node = f'<path d="{part["d"]}"'
    else:
        x, y, w, h = part["r"]
        node = f'<rect x="{_num(x)}" y="{_num(y)}" width="{_num(w)}" height="{_num(h)}" rx="1"'
    if part.get("fill"):
        node += ' fill="currentColor" stroke="none"'
    if part.get("w") and part["w"] != width:
        node += f' stroke-width="{_num(part["w"])}"'
    if part.get("faint"):
        node += f' opacity="{FAINT}"'
    return node + "/>"


def svg(box: Sequence[float], width: float, parts: Sequence[Mapping[str, Any]], color: str) -> str:
    """부분 목록 → SVG 글. currentColor 는 color 로 칠한다(확장에서는 칩 글자색)."""
    w, h = box
    body = "".join(_part(p, width) for p in parts)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_num(w)} {_num(h)}">'
            f'<g fill="none" stroke="currentColor" stroke-width="{_num(width)}" stroke-linecap="round" '
            f'stroke-linejoin="round">{body}</g></svg>').replace("currentColor", color)


def size(shape: Mapping[str, Any], height: int, gap: int = 0) -> Tuple[int, int]:
    """높이 height px 일 때의 (폭, 높이). gap 은 그림 오른쪽의 빈 폭(칩 글자와 띄우는 자리)."""
    w, h = shape["box"]
    return max(1, round(height * w / h)) + gap, height


def pixmap(shape: Mapping[str, Any], color: str, height: int, gap: int = 0) -> QPixmap:
    w, h = shape["box"]
    width_px, height_px = size(shape, height, gap)
    pix = QPixmap(width_px * RATIO, height_px * RATIO)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    target = QRectF(0, 0, (width_px - gap) * RATIO, height_px * RATIO)
    QSvgRenderer(QByteArray(svg(shape["box"], shape["w"], shape["parts"], color).encode())).render(painter, target)
    clip = shape.get("clip")
    if clip:
        top, bottom = clip["y"]
        scale = target.height() / h
        painter.setClipRect(QRectF(0, top * scale, target.width(), (bottom - top) * scale))
        QSvgRenderer(QByteArray(svg(shape["box"], shape["w"], clip["parts"], color).encode())).render(painter, target)
    painter.end()
    pix.setDevicePixelRatio(RATIO)
    return pix


def icon(shape: Mapping[str, Any], colors: Mapping[tuple, str], height: int, gap: int = 0) -> QIcon:
    """colors: {(QIcon.Mode, QIcon.State): 색}. 눌린 칩(On)·흐린 칩(Disabled)은 글자색이 달라서 따로 그린다."""
    result = QIcon()
    for (mode, state), color in colors.items():
        result.addPixmap(pixmap(shape, color, height, gap), mode, state)
    return result
