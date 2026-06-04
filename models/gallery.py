from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional


IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif",
    ".webp", ".ico", ".svg", ".avif", ".heic", ".heif",
}

IMAGE_SUFFIX_TUPLE = tuple(sorted(IMAGE_EXTENSIONS))


class Gallery:
    def __init__(self, path: str = "", name: str = "") -> None:
        self.path: str = path
        self.name: str = name or (Path(path).name if path else "")
        self.images: List[str] = []
        self.sub_galleries: List[Gallery] = []

    def __repr__(self) -> str:
        return f"Gallery(path={self.path!r}, name={self.name!r}, images={len(self.images)}, subs={len(self.sub_galleries)})"


def build_gallery_tree_from_cache(
    roots: List[str],
    folder_images_map: Dict[str, List[str]],
    folder_children: Dict[str, List[str]],
) -> Gallery:
    if not roots:
        return Gallery(path="", name="图库")

    if len(roots) == 1:
        root_path = roots[0]
        root = _build_subtree(root_path, folder_images_map, folder_children)
        return root

    root = Gallery(path="", name="图库")
    for rp in roots:
        sub = _build_subtree(rp, folder_images_map, folder_children)
        if sub.images or sub.sub_galleries:
            root.sub_galleries.append(sub)
    return root


def _build_subtree(
    folder_path: str,
    folder_images_map: Dict[str, List[str]],
    folder_children: Dict[str, List[str]],
) -> Gallery:
    gallery = Gallery(
        path=folder_path,
        name=Path(folder_path).name if folder_path else "图库",
    )
    gallery.images = list(folder_images_map.get(folder_path, []))

    children = folder_children.get(folder_path, [])
    for child_path in children:
        sub = _build_subtree(child_path, folder_images_map, folder_children)
        if sub.images or sub.sub_galleries:
            gallery.sub_galleries.append(sub)

    return gallery