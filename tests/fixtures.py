"""테스트용 작은 팩 — 실제 data/*.json 설정과 작은 원본·overlay 로 빌드한다."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bmk_tag_organizer_core as core  # noqa: E402
from tools import builder, tagdb  # noqa: E402
from tools.tagdb import SourceTag  # noqa: E402

# (이름, type, post_count, 별칭)
SOURCE = [
    ("1girl", 0, 9000, ("sole_female",)), ("solo", 0, 8000, ()), ("2girls", 0, 3000, ()),
    ("1boy", 0, 2900, ()), ("multiple_views", 0, 1000, ()), ("reference_sheet", 0, 500, ("character_sheet",)),
    ("long_hair", 0, 7000, ("longhair",)), ("very_long_hair", 0, 3000, ()), ("twintails", 0, 2500, ()),
    ("floating_hair", 0, 900, ()), ("aqua_hair", 0, 800, ()), ("black_hair", 0, 2400, ()),
    ("aqua_eyes", 0, 700, ()), ("blue_eyes", 0, 2600, ()), ("closed_eyes", 0, 2000, ()),
    ("one_eye_closed", 0, 1000, ("wink",)),
    ("grey_shirt", 0, 300, ()), ("black_skirt", 0, 600, ()), ("shirt", 0, 3000, ()), ("white_shirt", 0, 2000, ()),
    ("skirt", 0, 2900, ()), ("long_skirt", 0, 400, ()), ("vertical-striped_skirt", 0, 100, ()),
    ("striped_skirt", 0, 200, ()), ("blue_skirt", 0, 300, ()), ("hairband", 0, 1500, ()),
    ("white_hairband", 0, 200, ()), ("detached_sleeves", 0, 900, ()),
    ("standing", 0, 2000, ()), ("looking_at_viewer", 0, 5000, ()), ("looking_back", 0, 800, ()),
    ("looking_to_the_side", 0, 700, ()), ("sideways_glance", 0, 200, ()), ("looking_ahead", 0, 190, ()),
    ("looking_down", 0, 600, ()), ("looking_up", 0, 500, ()), ("looking_afar", 0, 150, ()),
    ("facing_viewer", 0, 300, ()), ("facing_away", 0, 180, ()), ("facing_back", 0, 40, ()),
    ("facing_to_the_side", 0, 90, ()), ("facing_ahead", 0, 70, ()), ("facing_down", 0, 60, ()), ("facing_up", 0, 50, ()),
    ("from_side", 0, 320, ()), ("straight-on", 0, 130, ()), ("profile", 0, 310, ()),
    ("head_out_of_frame", 0, 95, ()), ("lower_body", 0, 85, ()),
    ("on_bench", 0, 50, ()), ("holding", 0, 2500, ()), ("holding_leaf", 0, 30, ()),
    ("smile", 0, 4000, ()), ("blush", 0, 3900, ()), ("closed_mouth", 0, 1800, ()),
    ("collarbone", 0, 1700, ()), ("navel", 0, 1600, ()), ("cleavage", 0, 1500, ()), ("nipples", 0, 1200, ()),
    ("sex", 0, 1100, ()), ("shoes", 0, 1300, ()), ("soles", 0, 150, ()), ("toenails", 0, 100, ()),
    ("thighhighs", 0, 1400, ()), ("thighs", 0, 1000, ()),
    ("upper_body", 0, 1200, ()), ("cowboy_shot", 0, 860, ()), ("full_body", 0, 1100, ()),
    ("portrait", 0, 140, ()), ("from_behind", 0, 330, ("back_view",)), ("from_above", 0, 140, ()),
    ("bird's_eye_view", 0, 4, ()), ("depth_of_field", 0, 120, ()), ("blurry_background", 0, 180, ()),
    ("mirror", 0, 40, ()),
    ("leaf", 0, 400, ()), ("autumn_leaves", 0, 200, ()), ("maple_leaf", 0, 100, ()), ("ginkgo_leaf", 0, 20, ()),
    ("branch", 0, 150, ()), ("bench", 0, 100, ()), ("park_bench", 0, 30, ()), ("tree", 0, 900, ()),
    ("dappled_sunlight", 0, 150, ()), ("reflection", 0, 160, ()), ("falling_leaves", 0, 120, ()),
    ("park", 0, 60, ()), ("autumn", 0, 110, ()), ("nature", 0, 600, ()), ("day", 0, 1300, ()),
    ("simple_background", 0, 2900, ()), ("star_(symbol)", 0, 900, ()), ("^_^", 0, 150, ()), (">_<", 0, 90, ()),
    ("unclassified_thing", 0, 10, ()),
    ("hatsune_miku", 4, 140, ()), ("sagisawa_fumika", 4, 20, ()), ("vocaloid", 3, 200, ()),
    ("wlop", 1, 50, ()), ("ruu_(tksymkw)", 1, 40, ()), ("highres", 5, 8000, ()),
]

# 이름 → (primary, rating, regions, identity)
OVERLAY = {
    "1girl": ("subject/count", "", "", ""), "solo": ("subject/count", "", "", ""),
    "2girls": ("subject/count", "", "", ""), "1boy": ("subject/count", "", "", ""),
    "multiple_views": ("composition/format", "g", "", ""), "reference_sheet": ("composition/format", "g", "", ""),
    "long_hair": ("body/hair", "g", "head", "1"), "very_long_hair": ("body/hair", "g", "head", "1"),
    "twintails": ("body/hair", "g", "head", "1"), "floating_hair": ("body/hair", "g", "head", "0"),
    "aqua_hair": ("body/hair", "g", "head", "1"), "black_hair": ("body/hair", "g", "head", "1"),
    "aqua_eyes": ("body/eyes", "g", "eyes", "1"), "blue_eyes": ("body/eyes", "g", "eyes", "1"),
    "closed_eyes": ("action/expression", "g", "eyes", "0"),
    "one_eye_closed": ("action/expression", "g", "eyes", "0"),
    "grey_shirt": ("clothes/top", "g", "front_torso|back", ""), "black_skirt": ("clothes/bottom", "g", "hips|thighs", ""),
    "shirt": ("clothes/top", "g", "front_torso|back", ""), "white_shirt": ("clothes/top", "g", "front_torso|back", ""),
    "skirt": ("clothes/bottom", "g", "hips|thighs", ""), "long_skirt": ("clothes/bottom", "g", "hips|thighs", ""),
    "vertical-striped_skirt": ("clothes/bottom", "g", "hips|thighs", ""),
    "striped_skirt": ("clothes/bottom", "g", "hips|thighs", ""), "blue_skirt": ("clothes/bottom", "g", "hips|thighs", ""),
    "hairband": ("clothes/hair_acc", "g", "head", ""), "white_hairband": ("clothes/hair_acc", "g", "head", ""),
    "detached_sleeves": ("clothes/handwear", "g", "arms", ""),
    "standing": ("action/posture", "g", "", ""), "looking_at_viewer": ("action/gaze", "g", "", ""),
    "looking_back": ("action/gaze", "g", "", ""), "on_bench": ("action/posture", "g", "", ""),
    "looking_to_the_side": ("action/gaze", "g", "", ""), "sideways_glance": ("action/gaze", "g", "", ""),
    "looking_ahead": ("action/gaze", "g", "", ""), "looking_down": ("action/gaze", "g", "", ""),
    "looking_up": ("action/gaze", "g", "", ""), "looking_afar": ("action/gaze", "g", "", ""),
    "facing_viewer": ("action/gaze", "g", "", ""), "facing_away": ("action/gaze", "g", "", ""),
    "facing_back": ("action/gaze", "g", "", ""), "facing_to_the_side": ("action/gaze", "g", "", ""),
    "facing_ahead": ("action/gaze", "g", "", ""), "facing_down": ("action/gaze", "g", "", ""),
    "facing_up": ("action/gaze", "g", "", ""),
    "from_side": ("composition/angle", "g", "", ""), "straight-on": ("composition/angle", "g", "", ""),
    "profile": ("composition/angle", "g", "", ""),
    "head_out_of_frame": ("composition/framing", "g", "", ""), "lower_body": ("composition/framing", "g", "", ""),
    "holding": ("action/holding", "g", "hands", ""), "holding_leaf": ("action/holding", "g", "hands", ""),
    "smile": ("action/expression", "g", "face", ""), "blush": ("action/expression", "g", "face", ""),
    "closed_mouth": ("action/expression", "g", "face", ""),
    "collarbone": ("body/torso", "g", "front_torso", ""), "navel": ("body/torso", "g", "front_torso", ""),
    "cleavage": ("body/torso", "s", "front_torso", ""), "nipples": ("body/torso", "q", "front_torso", ""),
    "sex": ("action/sexual", "e", "", ""),
    "shoes": ("clothes/footwear", "g", "feet", ""), "soles": ("body/limbs", "g", "feet", ""),
    "toenails": ("body/limbs", "g", "feet", ""), "thighhighs": ("clothes/legwear", "g", "thighs|lower_legs", ""),
    "thighs": ("body/lower", "g", "thighs", ""),
    "upper_body": ("composition/framing", "g", "", ""), "cowboy_shot": ("composition/framing", "g", "", ""),
    "full_body": ("composition/framing", "g", "", ""), "portrait": ("composition/framing", "g", "", ""),
    "from_behind": ("composition/angle", "g", "", ""), "from_above": ("composition/angle", "g", "", ""),
    "bird's_eye_view": ("composition/angle", "g", "", ""),
    "depth_of_field": ("effect/lens", "", "", ""), "blurry_background": ("effect/lens", "", "", ""),
    "mirror": ("object/prop", "g", "", ""),
    "leaf": ("object/nature", "g", "", ""), "autumn_leaves": ("object/nature", "g", "", ""),
    "maple_leaf": ("object/nature", "g", "", ""), "ginkgo_leaf": ("object/nature", "g", "", ""),
    "branch": ("object/nature", "g", "", ""), "bench": ("background/scenery", "", "", ""),
    "park_bench": ("background/scenery", "", "", ""), "tree": ("background/scenery", "", "", ""),
    "dappled_sunlight": ("effect/lighting", "", "", ""), "reflection": ("effect/lighting", "", "", ""),
    "falling_leaves": ("effect/atmosphere", "", "", ""),
    "park": ("background/location", "", "", ""), "autumn": ("background/time", "", "", ""),
    "nature": ("background/location", "", "", ""), "day": ("background/time", "", "", ""),
    "simple_background": ("background/bg_type", "", "", ""),
    "star_(symbol)": ("object/symbol", "g", "", ""), "^_^": ("action/expression", "g", "face", ""),
    ">_<": ("action/expression", "g", "face", ""),
    "highres": ("meta/quality", "", "", ""),
}

EXTRA = [   # 확장 DB 행(tagdb.read_extra 처럼 db 칸이 붙음)
    {"db": "model", "name": "masterpiece", "primary": "meta/quality", "status": "reviewed", "models": "nai|illustrious"},
    {"db": "model", "name": "score_9", "primary": "meta/quality", "status": "reviewed", "models": "pony"},
]


def source_tags():
    # 실제 read_source_csv 처럼 post_count 내림차순
    return sorted((SourceTag(n, t, c, a) for n, t, c, a in SOURCE), key=lambda s: -s.count)


def overlay_rows(extra=None):
    rows = {}
    for name, (primary, rating, regions, identity) in OVERLAY.items():
        rows[name] = {"name": name, "source": "danbooru", "primary": primary, "rating": rating,
                      "regions": regions, "identity": identity, "status": "reviewed", "secondary": "",
                      "by": "test", "confidence": "", "note": "", "missing_since": ""}
    rows.update(extra or {})
    return rows


def build(overlay=None, config=None):
    pack, warnings = builder.build_pack(
        source_tags(), {"file": "fixture.csv", "date": "2026-10-01", "rows": len(SOURCE)},
        overlay if overlay is not None else overlay_rows(), EXTRA, config or tagdb.load_config(),
    )
    return pack, warnings


_PACK = None


def pack() -> core.TagPack:
    global _PACK
    if _PACK is None:
        _PACK = core.TagPack(build()[0])
    return _PACK
