from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Tuple

from models.gallery import Gallery


@dataclass
class DisplayImage:
    path: str
    display_name: str
    source_folder: str = ""
    resolution: tuple[int, int] | None = None
    format: str | None = None
    created_time: float | None = None
    modified_time: float | None = None


def count_direct_sub_galleries(gallery: Gallery) -> int:
    return len(gallery.sub_galleries)


def count_sub_galleries_recursive(gallery: Gallery) -> int:
    total = len(gallery.sub_galleries)
    for sub in gallery.sub_galleries:
        total += count_sub_galleries_recursive(sub)
    return total


def count_images_recursive(gallery: Gallery) -> int:
    total = len(gallery.images)
    for sub in gallery.sub_galleries:
        total += count_images_recursive(sub)
    return total


def count_images_recursive(gallery: Gallery) -> int:
    total = len(gallery.images)
    for sub in gallery.sub_galleries:
        total += count_images_recursive(sub)
    return total


def collect_all_images(gallery: Gallery) -> List[str]:
    all_images = list(gallery.images)
    for sub in gallery.sub_galleries:
        all_images.extend(collect_all_images(sub))
    return all_images


def _max_depth(gallery: Gallery) -> int:
    if not gallery.sub_galleries:
        return 0
    return 1 + max(_max_depth(sub) for sub in gallery.sub_galleries)


def effective_flatten_depth(flatten_depth: int, root: Gallery) -> int:
    if flatten_depth < 0:
        return -1
    md = _max_depth(root)
    if flatten_depth >= md:
        return -1
    return flatten_depth


def flatten_depth_label(flatten_depth: int, root: Gallery) -> str:
    if flatten_depth < 0:
        return "层级模式（完整层级）"
    if flatten_depth == 0:
        return "全部展平（所有图片直接显示在根目录）"
    md = _max_depth(root)
    if flatten_depth >= md:
        return f"等同于层级模式（depth={flatten_depth} ≥ 最大层级{md}）"
    return f"保留前 {flatten_depth} 层（第{flatten_depth + 1}层起展平）"


def build_gallery_view_rows(
    gallery: Gallery,
    stack_depth: int,
    flatten_depth: int,
    root: Gallery,
) -> List[Tuple[str, Any]]:
    if flatten_depth < 0:
        return _hierarchical_rows(gallery)

    if stack_depth >= flatten_depth:
        return _flattened_rows(gallery, stack_depth, flatten_depth)

    return _hierarchical_rows(gallery)


def _hierarchical_rows(gallery: Gallery) -> List[Tuple[str, Any]]:
    rows: List[Tuple[str, Any]] = []
    for sub in gallery.sub_galleries:
        rows.append(("sub", sub))
    for img_path in gallery.images:
        rows.append(("img", DisplayImage(path=img_path, display_name=Path(img_path).name)))
    return rows


def _flattened_rows(
    gallery: Gallery,
    current_depth: int,
    target_depth: int,
) -> List[Tuple[str, Any]]:
    rows: List[Tuple[str, Any]] = []
    collected = collect_images_with_source(gallery, current_depth, target_depth)
    for item in collected:
        disp_name = Path(item["path"]).name
        rows.append(("img", DisplayImage(
            path=item["path"],
            display_name=disp_name,
            source_folder=item.get("source_folder", ""),
        )))
    return rows


def collect_images_with_source(
    gallery: Gallery,
    current_depth: int,
    target_depth: int,
) -> List[dict]:
    results: List[dict] = []

    for img_path in gallery.images:
        results.append({"path": img_path, "source_folder": ""})

    if not gallery.sub_galleries:
        return results

    if target_depth == -1:
        return results

    if current_depth >= target_depth:
        for sub in gallery.sub_galleries:
            sub_images = collect_all_images(sub)
            for img_path in sub_images:
                results.append({"path": img_path, "source_folder": sub.name})
    else:
        for sub in gallery.sub_galleries:
            sub_results = collect_images_with_source(
                sub, current_depth + 1, target_depth
            )
            for item in sub_results:
                if item["source_folder"]:
                    item["source_folder"] = sub.name + "/" + item["source_folder"]
                else:
                    item["source_folder"] = sub.name
                results.append(item)

    return results