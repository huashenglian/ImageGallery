from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import List

from paths import MODEL_DIR

_logger = logging.getLogger("category_presets")

PRESETS_DIR = os.path.join(MODEL_DIR, "presets")
DEFAULT_PRESET_NAMES = {"ImageNet 通用分类", "WD14 动漫标签", "通用（无映射）"}

IMAGENET_PRESET_CATEGORIES = [
    ("人物", "person people face man woman child boy girl portrait selfie human crowd audience"),
    ("风景", "landscape mountain beach sunset sunrise sky cloud ocean lake river forest tree flower garden snow waterfall canyon valley island coast"),
    ("动物", "cat dog bird animal pet wildlife fish horse cow sheep elephant lion tiger bear rabbit squirrel butterfly insect snake frog turtle whale dolphin"),
    ("食物", "food meal dish fruit vegetable drink dessert cake bread pizza burger salad rice pasta soup coffee wine beer juice ice cream chocolate"),
    ("建筑", "building architecture house church castle bridge tower skyscraper stadium museum library temple palace lighthouse city street"),
    ("车辆", "car vehicle bike bicycle motorcycle bus train truck airplane boat ship helicopter taxi ambulance fire engine"),
    ("截图/文字", "text screenshot document menu web page book sign keyboard computer screen monitor"),
    ("其他", ""),
]

DANBOORU_PRESET_CATEGORIES = [
    ("人物", "1girl 2girls 1boy 2boys solo multiple_girls girl boy female male woman man loli shota"),
    ("风景", "scenery landscape sky cloud sunset night outdoors nature water sea ocean forest mountain"),
    ("动物", "cat dog animal bird rabbit horse fish cat_ears tail animal_ears"),
    ("食物", "food drink fruit dessert cake tea coffee bento rice noodle"),
    ("建筑", "building architecture house city street indoors room classroom school temple castle"),
    ("车辆", "car vehicle train bus bicycle motorcycle airplane boat ship"),
    ("截图/文字", "text signature watermark logo english_text japanese_text chinese_text"),
    ("其他", ""),
]

WD14_PRESET_CATEGORIES = [
    ("女性", "1girl girl female woman"),
    ("男性", "1boy boy male man"),
    ("风景", "no_humans scenery landscape sky cloud sunset night outdoors nature water sea ocean forest mountain flower_tree"),
    ("多人", "2girls 2boys 3girls 4girls 5girls 6+girls 3boys 4boys 5boys 6+boys multiple_girls multiple_boys group couple"),
    ("文档/文字", "text signature watermark logo english_text japanese_text chinese_text commentary"),
    ("其他", ""),
]


@dataclass
class CategoryPreset:
    name: str
    is_default: bool = False
    categories: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "is_default": self.is_default,
            "categories": self.categories,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CategoryPreset:
        return cls(
            name=d.get("name", ""),
            is_default=d.get("is_default", False),
            categories=d.get("categories", []),
        )


def _ensure_presets_dir() -> None:
    os.makedirs(PRESETS_DIR, exist_ok=True)


def _preset_path(name: str) -> str:
    safe = name.replace("/", "_").replace("\\", "_").replace(" ", "_")
    return os.path.join(PRESETS_DIR, f"{safe}.json")


def get_default_preset(name: str) -> CategoryPreset | None:
    if name == "ImageNet 通用分类":
        cats = [{"name": c[0], "keywords": c[1].split() if c[1] else []} for c in IMAGENET_PRESET_CATEGORIES]
        return CategoryPreset(name=name, is_default=True, categories=cats)
    elif name == "Danbooru 动漫标签":
        cats = [{"name": c[0], "keywords": c[1].split() if c[1] else []} for c in DANBOORU_PRESET_CATEGORIES]
        return CategoryPreset(name=name, is_default=True, categories=cats)
    elif name == "WD14 动漫标签":
        cats = [{"name": c[0], "keywords": c[1].split() if c[1] else []} for c in WD14_PRESET_CATEGORIES]
        return CategoryPreset(name=name, is_default=True, categories=cats)
    elif name == "通用（无映射）":
        return CategoryPreset(name=name, is_default=True, categories=[])
    return None


def load_preset(name: str) -> CategoryPreset | None:
    default = get_default_preset(name)
    if default:
        return default

    _ensure_presets_dir()
    path = _preset_path(name)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        preset = CategoryPreset.from_dict(data)
        preset.is_default = False
        return preset
    except (OSError, json.JSONDecodeError) as e:
        _logger.warning("Failed to load preset %s: %s", name, e)
        return None


def save_preset(preset: CategoryPreset) -> tuple[bool, str]:
    if preset.is_default:
        return False, "无法保存默认预设"

    if preset.name in DEFAULT_PRESET_NAMES:
        return False, "无法覆盖默认预设"

    _ensure_presets_dir()
    path = _preset_path(preset.name)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(preset.to_dict(), f, indent=2, ensure_ascii=False)
        return True, ""
    except OSError as e:
        return False, f"保存失败: {e}"


def delete_preset(name: str) -> bool:
    if name in DEFAULT_PRESET_NAMES:
        return False
    _ensure_presets_dir()
    path = _preset_path(name)
    if os.path.isfile(path):
        try:
            os.remove(path)
            return True
        except OSError:
            return False
    return False


def list_presets() -> list[str]:
    result = ["ImageNet 通用分类", "WD14 动漫标签", "通用（无映射）"]

    _ensure_presets_dir()
    if os.path.isdir(PRESETS_DIR):
        for f in sorted(os.listdir(PRESETS_DIR)):
            if f.endswith(".json"):
                try:
                    with open(os.path.join(PRESETS_DIR, f), "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    name = data.get("name", "")
                    if name and name not in DEFAULT_PRESET_NAMES:
                        result.append(name)
                except (OSError, json.JSONDecodeError):
                    pass

    return result


def preset_to_mapping(preset: CategoryPreset) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for cat in preset.categories:
        name = cat["name"]
        keywords = cat.get("keywords", [])
        if isinstance(keywords, str):
            keywords = keywords.split()
        mapping[name] = list(keywords)
    if "其他" not in mapping:
        mapping["其他"] = []
    return mapping


def preset_category_names(preset: CategoryPreset) -> list[str]:
    if not preset or not preset.categories:
        return ["人物", "风景", "动物", "食物", "建筑", "车辆", "截图/文字", "其他"]
    return [cat["name"] for cat in preset.categories]
