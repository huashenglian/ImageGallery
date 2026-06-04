from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional, Set

from paths import THUMBNAIL_DIR

_logger = logging.getLogger("thumbnail_cache")

_wic_available: bool | None = None


def _try_wic():
    global _wic_available
    if _wic_available is None:
        try:
            from cpp_ext.thumbnail import thumb_generate_batch
            _wic_available = thumb_generate_batch is not None
        except Exception:
            _wic_available = False
        if _wic_available:
            _logger.info("WIC 缩略图扩展已加载")
        else:
            _logger.info("WIC 缩略图扩展不可用，使用 PIL 回退")
    return _wic_available


class ThumbnailCache:
    def __init__(self, max_edge: int = 256, cache_dir: Optional[Path] = None) -> None:
        self.max_edge = max_edge
        self.cache_dir = cache_dir or Path(THUMBNAIL_DIR)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _key_part(self, source_path: str) -> str:
        normalized = str(Path(source_path).resolve())
        digest = hashlib.sha256(normalized.encode("utf-8", errors="surrogateescape")).hexdigest()
        return f"{digest}_{self.max_edge}x{self.max_edge}"

    def cache_path_for(self, source_path: str) -> Path:
        return self.cache_dir / f"{self._key_part(source_path)}.jpg"

    def ensure_thumbnail(self, source_path: str) -> Path:
        out = self.cache_path_for(source_path)
        if out.is_file():
            return out
        # 尝试 WIC C++ 扩展
        if _try_wic():
            try:
                return self._ensure_thumbnail_wic(source_path, out)
            except Exception as exc:
                _logger.debug("WIC 生成失败，回退 PIL: %s", exc)
        # 回退到 PIL
        return self._ensure_thumbnail_pil(source_path, out)

    def _ensure_thumbnail_wic(self, source_path: str, out: Path) -> Path:
        from cpp_ext.thumbnail import thumb_generate_batch
        results = thumb_generate_batch(
            [(source_path, str(out))],
            max_edge=self.max_edge,
            jpeg_quality=85,
        )
        if results and results[0].success:
            return Path(results[0].output_path)
        err = results[0].error if results else "unknown"
        raise RuntimeError(f"WIC: {err}")

    def _ensure_thumbnail_pil(self, source_path: str, out: Path) -> Path:
        from PIL import Image, ImageOps

        Image.MAX_IMAGE_PIXELS = 178_956_970
        src = Path(source_path)
        with Image.open(src) as im:
            im.seek(0)
            try:
                im = ImageOps.exif_transpose(im)
            except Exception:
                pass
            im = im.convert("RGBA") if im.mode in ("P", "RGBA") else im.convert("RGB")
            im.thumbnail((self.max_edge, self.max_edge), Image.Resampling.LANCZOS)
            if im.mode == "RGBA":
                background = Image.new("RGB", im.size, (255, 255, 255))
                background.paste(im, mask=im.split()[3])
                im = background
            elif im.mode != "RGB":
                im = im.convert("RGB")
            out.parent.mkdir(parents=True, exist_ok=True)
            im.save(out, "JPEG", quality=85, optimize=True)
        return out

    def ensure_thumbnails_batch(self, source_paths: list[str]) -> dict[str, Path]:
        """批量生成缩略图，优先使用 WIC 扩展。返回 {source_path: cache_path}。"""
        results_map: dict[str, Path] = {}
        pending_wic: list[tuple[str, str]] = []
        pending_pil: list[str] = []

        for sp in source_paths:
            out = self.cache_path_for(sp)
            if out.is_file():
                results_map[sp] = out
            elif _try_wic():
                pending_wic.append((sp, str(out)))
            else:
                pending_pil.append(sp)

        # WIC 批量处理
        if pending_wic:
            try:
                from cpp_ext.thumbnail import thumb_generate_batch
                wic_results = thumb_generate_batch(
                    pending_wic, max_edge=self.max_edge, jpeg_quality=85,
                )
                for (sp, _), r in zip(pending_wic, wic_results):
                    if r.success:
                        results_map[sp] = Path(r.output_path)
                    else:
                        pending_pil.append(sp)
            except Exception as exc:
                _logger.debug("WIC 批量生成失败，全部回退 PIL: %s", exc)
                pending_pil.extend(sp for sp, _ in pending_wic)

        # PIL 回退
        for sp in pending_pil:
            out = self.cache_path_for(sp)
            try:
                results_map[sp] = self._ensure_thumbnail_pil(sp, out)
            except Exception:
                pass

        return results_map

    def collect_valid_keys(self, image_paths: list[str]) -> Set[str]:
        valid: Set[str] = set()
        for p in image_paths:
            normalized = str(Path(p).resolve())
            digest = hashlib.sha256(normalized.encode("utf-8", errors="surrogateescape")).hexdigest()
            valid.add(f"{digest}_")
        return valid

    def purge_orphans(self, valid_key_prefixes: Set[str]) -> int:
        if not self.cache_dir.is_dir():
            return 0
        removed = 0
        for f in list(self.cache_dir.iterdir()):
            if not f.is_file() or f.suffix.lower() != ".jpg":
                continue
            name = f.stem
            underscore_idx = name.rfind("_")
            if underscore_idx < 0:
                continue
            prefix = name[:underscore_idx + 1]
            if prefix not in valid_key_prefixes:
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    def purge_other_sizes(self, current_size_tag: str) -> int:
        if not self.cache_dir.is_dir():
            return 0
        removed = 0
        for f in list(self.cache_dir.iterdir()):
            if not f.is_file() or f.suffix.lower() != ".jpg":
                continue
            if not f.stem.endswith(current_size_tag):
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed
