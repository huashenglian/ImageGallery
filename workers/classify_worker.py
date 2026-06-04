from __future__ import annotations

import logging
import os
import threading
import time

from PySide6.QtCore import QThread, Signal

from services.cache_manager import CacheManager
from services.classify_cache import ClassifyCache
from services.classifier import CONFIDENCE_THRESHOLD, UnifiedCategoryBuilder, classify_multi_label_set
from services.model_manager import ModelManager
from services.models_registry import get_enabled_models, get_fallback_level, load_model_config
from paths import is_excluded_scan_path

_logger = logging.getLogger("classify_worker")

_MULTI_LABEL_THRESHOLD = 1000


class ClassifyWorker(QThread):
    progress = Signal(int, int, str)
    category_found = Signal(str, str, float)
    finished_ok = Signal(int, int)
    stopped = Signal(int, int)
    failed = Signal(str)

    def __init__(self, model_mgr: ModelManager, cache: ClassifyCache,
                 cache_mgr: CacheManager, parent=None):
        super().__init__(parent)
        self._model_mgr = model_mgr
        self._cache = cache
        self._cache_mgr = cache_mgr
        self._paused = False
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._cancel_requested = False
        self._active_model_ids: list[str] = []
        self._model_configs: dict = {}
        self._builder: UnifiedCategoryBuilder | None = None
        self._initial_model_configs: dict = {}
        self._last_refresh_time = 0.0
        self._models_released: bool = False

    def pause(self) -> None:
        self._paused = True
        self._pause_event.clear()

    def resume(self) -> None:
        self._paused = False
        self._last_refresh_time = 0.0
        self._pause_event.set()

    @property
    def is_paused(self) -> bool:
        return self._paused

    def request_stop(self) -> None:
        self._cancel_requested = True
        self._pause_event.set()

    def run(self) -> None:
        all_images = self._collect_all_images()
        if not all_images:
            self.finished_ok.emit(0, 0)
            return

        unclassified = [p for p in all_images if self._cache.get(p) is None]
        if not unclassified:
            self.finished_ok.emit(0, 0)
            return

        enabled = get_enabled_models()
        if not enabled:
            self.failed.emit("没有已启用的分类模型，请先在「编辑→分类模型配置」中下载或启用模型。")
            return

        self._init_active_models(enabled)

        if self._model_configs:
            first_config = next(iter(self._model_configs.values()))
            self._cache.set_preset_name(first_config.preset_name or first_config.category_map_type)

        total_all = len(all_images)
        already_classified_count = total_all - len(unclassified)
        classified = 0
        skipped = 0

        self._cache.mark_incomplete()

        for i, path in enumerate(unclassified, 1):
            if self._cancel_requested:
                break

            while self._paused and not self._cancel_requested:
                if not self._models_released:
                    self._release_active_models()
                    self._models_released = True
                self._pause_event.wait(timeout=0.5)

            if self._cancel_requested:
                break

            if self._models_released:
                self._reload_active_models()
                self._models_released = False
                if self._cancel_requested:
                    break

            self._refresh_active_models()

            if not self._active_model_ids:
                _logger.warning("所有模型已被禁用，分类中止")
                break

            best_category = "其他"
            best_confidence = 0.0
            best_label = ""
            best_model_id = ""
            fallback_used = False
            all_ok = True

            for idx, mid in enumerate(self._active_model_ids):
                config = self._model_configs[mid]
                result = self._classify_with_unified_mapping(mid, path, config, self._builder)
                if result is None:
                    all_ok = False
                    continue
                category, label, confidence = result
                if category != "其他":
                    best_category = category
                    best_confidence = confidence
                    best_label = label
                    best_model_id = mid
                    fallback_used = idx > 0
                    break

            _logger.info(
                "[诊断] 最终决策: path=%s category=%s label=%s conf=%.4f model=%s fallback=%s",
                os.path.basename(path), best_category, best_label, best_confidence,
                best_model_id, fallback_used,
            )

            if not all_ok and best_category == "其他":
                _logger.warning("图片分类回退为「其他」: path=%s，原因=全部模型失败或未返回结果", path)
                skipped += 1

            self._cache.update({
                path: {
                    "category": best_category,
                    "label": best_label,
                    "confidence": best_confidence,
                    "model_id": best_model_id,
                    "fallback_used": fallback_used,
                    "classified_at": time.time(),
                }
            })
            classified += 1

            self._cache.set_progress(already_classified_count + i, total_all)
            self._cache.maybe_save()
            self.category_found.emit(path, best_category, best_confidence)
            self.progress.emit(already_classified_count + i, total_all, path)

        if self._cancel_requested or not self._active_model_ids:
            self._cache.mark_incomplete()
            self._cache.flush()
            if self._model_mgr.should_release_after_classify():
                self._model_mgr.release_all()
            self.stopped.emit(classified, skipped)
            return

        self._cache.mark_complete(skipped=skipped)
        self._cache.flush()

        if self._model_mgr.should_release_after_classify():
            self._model_mgr.release_all()

        self.finished_ok.emit(classified, skipped)

    def _release_active_models(self) -> None:
        for mid in self._active_model_ids:
            self._model_mgr.release_model(mid)
        _logger.info("暂停分类，已释放 %d 个模型", len(self._active_model_ids))

    def _reload_active_models(self) -> None:
        enabled = get_enabled_models()
        model_ids = [m["model_id"] for m in enabled]

        failed_ids = []
        for mid in self._active_model_ids:
            ok, msg = self._model_mgr.load_model(mid)
            if not ok:
                _logger.warning("恢复分类，模型 %s 重新加载失败: %s", mid, msg)
                failed_ids.append(mid)

        if failed_ids:
            for mid in failed_ids:
                self._active_model_ids.remove(mid)
                self._model_configs.pop(mid, None)
            if not self._active_model_ids:
                self.failed.emit("恢复分类失败：所有模型重新加载失败")
                self._cancel_requested = True
                return

        self._builder = UnifiedCategoryBuilder(enabled, model_ids)
        self._builder.build()
        _logger.info("恢复分类，已重新加载 %d 个模型", len(self._active_model_ids))

    def _init_active_models(self, enabled: list[dict]) -> None:
        fallback_level = get_fallback_level()
        model_ids = [m["model_id"] for m in enabled]

        if fallback_level == -1:
            self._active_model_ids = list(model_ids)
        elif fallback_level == 0:
            self._active_model_ids = model_ids[:1]
        else:
            self._active_model_ids = model_ids[:fallback_level + 1]

        self._model_configs = {mid: load_model_config(mid) for mid in self._active_model_ids}
        self._initial_model_configs = dict(self._model_configs)

        model_label_counts = {}
        for mid in self._active_model_ids:
            labels = self._model_mgr.get_labels(mid)
            model_label_counts[mid] = len(labels)

        for mid in self._active_model_ids:
            cfg = self._model_configs[mid]
            is_ml = model_label_counts[mid] > _MULTI_LABEL_THRESHOLD
            _logger.info(
                "model=%s is_multi_label=%s map_type=%s threshold=%.2f labels=%d",
                mid, is_ml, cfg.category_map_type,
                cfg.confidence_threshold if cfg.confidence_threshold > 0 else CONFIDENCE_THRESHOLD,
                model_label_counts[mid],
            )

        for mid in self._active_model_ids:
            ok, msg = self._model_mgr.load_model(mid)
            if not ok:
                self.failed.emit(f"模型 {mid} 加载失败: {msg}")
                return

        self._builder = UnifiedCategoryBuilder(enabled, model_ids)
        self._builder.build()

    def _refresh_active_models(self) -> None:
        now = time.monotonic()
        if now - self._last_refresh_time < 5.0:
            return
        self._last_refresh_time = now

        enabled = get_enabled_models()
        if not enabled:
            return

        fallback_level = get_fallback_level()
        model_ids = [m["model_id"] for m in enabled]

        if fallback_level == -1:
            new_active_ids = list(model_ids)
        elif fallback_level == 0:
            new_active_ids = model_ids[:1]
        else:
            new_active_ids = model_ids[:fallback_level + 1]

        if new_active_ids == self._active_model_ids:
            return

        _logger.info(
            "启用模型变更: %s -> %s",
            self._active_model_ids, new_active_ids,
        )

        new_configs = {}
        for mid in new_active_ids:
            if mid in self._initial_model_configs:
                new_configs[mid] = self._initial_model_configs[mid]
            else:
                new_configs[mid] = load_model_config(mid)
                self._initial_model_configs[mid] = new_configs[mid]

        failed_ids = []
        for mid in new_active_ids:
            if mid not in self._active_model_ids:
                ok, msg = self._model_mgr.load_model(mid)
                if not ok:
                    _logger.warning("新模型 %s 加载失败: %s", mid, msg)
                    failed_ids.append(mid)

        for mid in self._active_model_ids:
            if mid not in new_active_ids:
                self._model_mgr.release_model(mid)

        for mid in failed_ids:
            new_active_ids.remove(mid)
            new_configs.pop(mid, None)

        self._active_model_ids = new_active_ids
        self._model_configs = new_configs

        if self._active_model_ids:
            self._builder = UnifiedCategoryBuilder(enabled, model_ids)
            self._builder.build()

    def _classify_single_label(self, model_id: str, path: str, config) -> tuple[str, float] | None:
        for attempt in range(2):
            try:
                label, confidence = self._model_mgr.classify_image(model_id, path)
                _logger.debug("single [%s] label=%s conf=%.3f", model_id, label, confidence)
                return label, confidence
            except Exception as e:
                _logger.warning(
                    "single_label [%s] classify failed: path=%s attempt=%d err=%s",
                    model_id, path, attempt + 1, e,
                )
                if attempt == 0:
                    continue
                return None
        return None

    def _classify_multi_label(self, model_id: str, path: str, config) -> tuple[str, str, float] | None:
        try:
            top_results = self._model_mgr.classify_image_top_n(model_id, path, n=30)
        except Exception as e:
            _logger.warning("multi_label [%s] classify failed: path=%s err=%s", model_id, path, e)
            return None
        if not top_results:
            return None

        threshold = config.confidence_threshold if config.confidence_threshold > 0 else CONFIDENCE_THRESHOLD
        map_type = config.category_map_type

        _logger.info(
            "multi_label [%s] top-30: %s (map_type=%s, threshold=%.2f)",
            model_id,
            ", ".join(f"{l}={c:.3f}" for l, c in top_results[:30]),
            map_type, threshold,
        )
        if model_id == "pixai_tagger_v09":
            onegirl_conf = next((conf for label, conf in top_results if label == "1girl"), None)
            if onegirl_conf is None:
                _logger.info("multi_label [%s] 1girl 未进入 top-30 (threshold=%.2f)", model_id, threshold)
            else:
                passed = onegirl_conf >= threshold
                _logger.info(
                    "multi_label [%s] 1girl conf=%.4f threshold=%.2f passed=%s",
                    model_id, onegirl_conf, threshold, passed,
                )

        category, confidence, label = classify_multi_label_set(top_results, map_type, threshold)

        _logger.info(
            "multi_label [%s] result: category=%s label=%s conf=%.3f",
            model_id, category, label, confidence,
        )
        return category, label, confidence

    def _classify_with_unified_mapping(self, model_id: str, path: str, config,
                                       builder: UnifiedCategoryBuilder) -> tuple[str, str, float] | None:
        labels_count = len(self._model_mgr.get_labels(model_id))
        is_multi_label = labels_count > _MULTI_LABEL_THRESHOLD

        if is_multi_label:
            result = self._classify_multi_label(model_id, path, config)
            if result is None:
                return None
            category, label, confidence = result
            unified_category = builder.classify_with_unified_mapping(label)
            return unified_category, label, confidence
        else:
            result = self._classify_single_label(model_id, path, config)
            if result is None:
                return None
            label, confidence = result
            threshold = config.confidence_threshold if config.confidence_threshold > 0 else CONFIDENCE_THRESHOLD
            if confidence < threshold:
                return "其他", label, confidence
            unified_category = builder.classify_with_unified_mapping(label)
            return unified_category, label, confidence

    def _collect_all_images(self) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []

        entries = self._cache_mgr.list_entries()
        for entry in entries:
            if entry.store is None:
                store = self._cache_mgr.load_entry_store(entry)
                if store is None:
                    continue
            else:
                store = entry.store

            for folder_data in store.folders.values():
                images = folder_data.get("images", [])
                for img_path in images:
                    if img_path not in seen and not is_excluded_scan_path(img_path):
                        seen.add(img_path)
                        result.append(img_path)

        result.sort(key=str.lower)
        return result
