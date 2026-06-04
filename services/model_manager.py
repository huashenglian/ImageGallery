from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import threading
import time
import urllib.request
from pathlib import Path

from PySide6.QtCore import QSettings

from paths import MODEL_DIR, CACHE_DIR
from services.models_registry import (
    CATEGORY_MAP_IMAGENET,
    ModelRegistryEntry,
    ModelConfig,
    BUILTIN_MODELS,
    get_builtin_by_id,
    auto_validate_model,
    auto_detect_category_map,
    ensure_labels_file,
    scan_installed_models,
    get_enabled_models,
    set_model_enabled,
    delete_model,
    save_model_config,
    load_model_config,
)

_logger = logging.getLogger("model_manager")

_IMAGENET_MEAN = [0.485, 0.456, 0.406]
_IMAGENET_STD = [0.229, 0.224, 0.225]
_CONNECT_TIMEOUT = 8
_READ_TIMEOUT = 15
_TAG_PROBS_DIR = os.path.join(CACHE_DIR, "tag_probs")


class ModelManager:
    _softmax_cache: dict[str, bool | None] = {}
    _active_onnx_paths: dict[str, str] = {}

    def __init__(self):
        self._sessions: dict[str, object] = {}
        self._labels_cache: dict[str, list[str]] = {}
        self._cleanup_temp_files()

    @property
    def is_installed(self) -> bool:
        installed = scan_installed_models()
        return len(installed) > 0

    @staticmethod
    def _cleanup_temp_files() -> None:
        if not os.path.isdir(MODEL_DIR):
            return
        for f in os.listdir(MODEL_DIR):
            if f.endswith(".downloading"):
                try:
                    os.remove(os.path.join(MODEL_DIR, f))
                except OSError:
                    pass

    @property
    def has_enabled_models(self) -> bool:
        return len(get_enabled_models()) > 0

    def download_registry_model(
        self,
        entry: ModelRegistryEntry,
        progress_callback=None,
        cancel_event: threading.Event = None,
        status_callback=None,
    ) -> tuple[bool, str]:
        if cancel_event is None:
            cancel_event = threading.Event()

        try:
            usage = shutil.disk_usage(os.path.dirname(MODEL_DIR) or ".")
            if usage.free < entry.size_mb * 1024 * 1024 + 50 * 1024 * 1024:
                return False, f"磁盘空间不足（需要至少{int(entry.size_mb) + 50}MB）"
        except OSError as e:
            return False, f"无法检查磁盘空间: {e}"

        os.makedirs(MODEL_DIR, exist_ok=True)

        if not entry.download_url:
            return False, "该模型暂不支持在线下载"

        if status_callback:
            status_callback("正在下载模型文件…")

        primary_url = entry.download_url
        mirror_url = entry.mirror_download_url
        success, err = self._download_with_fallback(
            primary_url, mirror_url, entry.onnx_path,
            progress_callback, status_callback, cancel_event, entry.onnx_filename,
        )
        if not success:
            if os.path.isfile(entry.onnx_path):
                os.remove(entry.onnx_path)
            return False, f"模型下载失败: {err}"

        if status_callback:
            status_callback("正在下载标签文件…")

        labels_primary = entry.labels_url
        labels_mirror = entry.mirror_labels_url
        success, err = self._download_with_fallback(
            labels_primary, labels_mirror, entry.labels_path,
            progress_callback, status_callback, cancel_event, entry.labels_filename,
        )
        if not success:
            has_labels, labels = ensure_labels_file(entry.onnx_path, entry.labels_path)
            if not has_labels:
                _logger.warning("标签文件下载失败，已自动生成默认标签")
        else:
            self._convert_if_csv(entry.labels_path)

        if status_callback:
            status_callback("正在验证模型…")

        info = auto_validate_model(entry.onnx_path)
        config = ModelConfig(
            model_id=entry.id,
            enabled=True,
            category_map_type=entry.default_map,
        )
        if info:
            config.input_size = info["input_size"]
            config.input_channels = info["input_channels"]
            config.preprocess_type = info["preprocess_type"]
        save_model_config(config)

        verified = info is not None
        msg = "下载完成" + ("（已验证）" if verified else "（未验证，缺少onnxruntime）")
        return True, msg

    def import_model_files(self, onnx_paths: list[str]) -> tuple[int, list[str]]:
        os.makedirs(MODEL_DIR, exist_ok=True)
        imported = 0
        warnings: list[str] = []

        for src_path in onnx_paths:
            filename = os.path.basename(src_path)
            dst_path = os.path.join(MODEL_DIR, filename)

            info = auto_validate_model(src_path)
            if info is None:
                try:
                    import onnxruntime as ort
                    warnings.append(f"{filename} 不是有效的 ONNX 模型文件，已跳过")
                except ImportError:
                    pass

            try:
                shutil.copy2(src_path, dst_path)
            except Exception as e:
                warnings.append(f"{filename} 复制失败: {e}")
                continue

            model_id = filename[:-5]
            labels_src = src_path.replace(".onnx", ".json")
            labels_dst = os.path.join(MODEL_DIR, f"{model_id}_labels.json")

            if os.path.isfile(labels_src):
                try:
                    shutil.copy2(labels_src, labels_dst)
                except Exception:
                    pass
            else:
                has_labels, labels = ensure_labels_file(dst_path, labels_dst)
                if not has_labels:
                    warnings.append(f"{filename} 的标签文件缺失，已自动生成默认标签")

            category_map = CATEGORY_MAP_IMAGENET
            try:
                with open(labels_dst, "r", encoding="utf-8") as f:
                    lbls = json.load(f) if labels_dst.endswith(".json") else []
                if isinstance(lbls, list) and lbls:
                    category_map = auto_detect_category_map(lbls)
            except Exception:
                pass

            config = ModelConfig(
                model_id=model_id,
                enabled=True,
                category_map_type=category_map,
            )
            if info:
                config.input_size = info["input_size"]
                config.input_channels = info["input_channels"]
                config.preprocess_type = info["preprocess_type"]
            save_model_config(config)
            imported += 1

        return imported, warnings

    def load_model(self, model_id: str) -> tuple[bool, str]:
        if model_id in self._sessions:
            return True, "模型已加载"

        installed = scan_installed_models()
        model_info = None
        for m in installed:
            if m["model_id"] == model_id:
                model_info = m
                break
        if model_info is None:
            return False, f"模型 {model_id} 未安装"

        config = load_model_config(model_id)
        onnx_path = model_info["onnx_path"]

        if model_info.get("int8_only"):
            onnx_path = model_info["int8_path"]
            _logger.info("模型 %s 使用 INT8 版本（FP32 已删除）", model_id)
        elif config.use_int8 and model_info.get("has_int8"):
            int8_path = model_info["int8_path"]
            if os.path.isfile(int8_path):
                onnx_path = int8_path
                _logger.info("模型 %s 使用 INT8 版本", model_id)
            else:
                _logger.warning("模型 %s INT8 文件不存在，回退到 FP32", model_id)
        elif config.use_int8:
            _logger.warning("模型 %s 无 INT8 版本，使用 FP32", model_id)

        if not os.path.isfile(onnx_path):
            return False, f"模型文件不存在: {onnx_path}"

        try:
            import onnxruntime as ort
        except ImportError:
            return False, "onnxruntime未安装，无法加载模型"

        for attempt in range(3):
            try:
                sess_opts = ort.SessionOptions()
                sess_opts.intra_op_num_threads = 4
                sess_opts.inter_op_num_threads = 1
                sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                sess_opts.enable_mem_pattern = True
                self._sessions[model_id] = ort.InferenceSession(onnx_path, sess_options=sess_opts)
                ModelManager._active_onnx_paths[model_id] = onnx_path
                _logger.info("模型 %s 加载成功 (%s)", model_id, "INT8" if onnx_path.endswith("_int8.onnx") else "FP32")
                return True, "模型加载成功"
            except Exception as e:
                _logger.warning("模型 %s 加载失败(第%d次): %s", model_id, attempt + 1, e)
                if attempt < 2:
                    time.sleep(0.5)
        return False, f"模型 {model_id} 加载失败（重试3次）"

    def release_model(self, model_id: str) -> None:
        if model_id in self._sessions:
            del self._sessions[model_id]
            self._labels_cache.pop(model_id, None)
            ModelManager._active_onnx_paths.pop(model_id, None)
            _logger.info("模型 %s 已释放", model_id)

    def redownload_fp32(
        self,
        model_id: str,
        progress_callback=None,
        cancel_event: threading.Event | None = None,
        status_callback=None,
    ) -> tuple[bool, str]:
        if cancel_event is None:
            cancel_event = threading.Event()

        entry = get_builtin_by_id(model_id)
        if entry is None:
            return False, f"模型 {model_id} 不是内置模型，无法自动重新下载"

        fp32_path = entry.onnx_path
        int8_path = fp32_path.replace(".onnx", "_int8.onnx")

        if status_callback:
            status_callback("正在重新下载原始模型…")

        primary_url = entry.download_url
        mirror_url = entry.mirror_download_url
        success, err = self._download_with_fallback(
            primary_url, mirror_url, fp32_path,
            progress_callback, status_callback, cancel_event, entry.onnx_filename,
        )
        if not success:
            if os.path.isfile(fp32_path):
                os.remove(fp32_path)
            return False, f"重新下载失败: {err}"

        if os.path.isfile(int8_path):
            try:
                os.remove(int8_path)
                _logger.info("已删除 INT8 文件: %s", int8_path)
            except OSError as e:
                _logger.warning("删除 INT8 文件失败: %s", e)

        config = load_model_config(model_id)
        config.use_int8 = False
        save_model_config(config)

        if model_id in self._sessions:
            del self._sessions[model_id]

        from services.classifier import clear_unified_cache
        clear_unified_cache()

        fp32_size_mb = os.path.getsize(fp32_path) / (1024 * 1024)
        _logger.info("FP32 模型已恢复: %.1f MB", fp32_size_mb)
        return True, f"原始模型已恢复 ({fp32_size_mb:.1f} MB)"

    def release_all(self) -> None:
        for mid in list(self._sessions.keys()):
            self.release_model(mid)

    def should_release_after_classify(self) -> bool:
        s = QSettings("ImageGallery", "ImageGallery")
        return s.value("classify/release_after_classify", True, type=bool)

    def get_labels(self, model_id: str) -> list[str]:
        return self._get_labels_internal(model_id, expected_count=None)

    def _get_labels_internal(self, model_id: str, expected_count: int | None) -> list[str]:
        if model_id in self._labels_cache:
            cached = self._labels_cache[model_id]
            if not self._labels_need_repair(model_id, cached, expected_count):
                return cached
            self._labels_cache.pop(model_id, None)

        installed = scan_installed_models()
        model_info = None
        for m in installed:
            if m["model_id"] == model_id:
                model_info = m
                break
        if model_info is None:
            return []

        onnx_path = model_info["onnx_path"]
        labels_path = onnx_path.replace(".onnx", "_labels.json")

        labels = self._read_labels_file(labels_path)

        if not labels:
            csv_path = onnx_path.replace(".onnx", "_selected_tags.csv")
            if os.path.isfile(csv_path):
                self._convert_csv_labels(csv_path, labels_path)
                labels = self._read_labels_file(labels_path)

        if self._labels_need_repair(model_id, labels, expected_count):
            reasons = []
            if expected_count is not None and len(labels) != expected_count:
                reasons.append(f"标签数={len(labels)} 与输出维度={expected_count} 不一致")
            if self._labels_look_numeric(model_id, labels):
                reasons.append("标签文件内容看起来是数字 ID 而不是标签名")
            _logger.warning("模型 %s 的标签文件需要修复: %s", model_id, "；".join(reasons))
            repaired = self._repair_builtin_labels(model_id, labels_path)
            if repaired:
                labels = self._read_labels_file(labels_path)

        self._labels_cache[model_id] = labels

        if labels and all(isinstance(lbl, str) and lbl.startswith("class_") for lbl in labels[:10]):
            _logger.warning(
                "模型 %s 的标签全部为自动生成占位符 (class_0, class_1, ...)。"
                "分类结果将全部归入「其他」。请提供正确的标签文件，"
                "或通过「编辑→分类模型配置」重新下载该模型。", model_id
            )

        return labels

    def batch_predict(self, model_id: str, image_paths: list[str],
                      batch_size: int = 8) -> list[dict | None]:
        if model_id not in self._sessions:
            ok, msg = self.load_model(model_id)
            if not ok:
                raise RuntimeError(f"模型加载失败: {msg}")

        import numpy as np

        session = self._sessions[model_id]
        config = load_model_config(model_id)
        input_meta = session.get_inputs()[0]
        output_metas = session.get_outputs()
        output_names = [out.name for out in output_metas]
        input_name = input_meta.name
        selected_index = self._select_output_index(model_id, output_metas)
        selected_name = output_metas[selected_index].name

        labels = self._get_labels_internal(model_id, expected_count=None)

        results: list[dict | None] = [None] * len(image_paths)

        for start in range(0, len(image_paths), batch_size):
            batch_paths = image_paths[start:start + batch_size]
            valid_indices: list[int] = []
            batch_arrays: list[np.ndarray] = []

            for i, path in enumerate(batch_paths):
                try:
                    arr = self._preprocess(path, config, input_meta.shape, model_id)
                    batch_arrays.append(arr)
                    valid_indices.append(start + i)
                except Exception as e:
                    _logger.warning("预处理失败: %s: %s", path, e)

            if not batch_arrays:
                continue

            batch_input = np.concatenate(batch_arrays, axis=0)
            outputs = session.run(output_names, {input_name: batch_input})

            selected_output = np.asarray(outputs[selected_index])

            for j in range(len(valid_indices)):
                if selected_output.ndim >= 2:
                    scores = np.asarray(selected_output[j], dtype=np.float32)
                else:
                    scores = np.asarray(selected_output, dtype=np.float32)

                if self._should_apply_sigmoid(selected_name):
                    scores = 1.0 / (1.0 + np.exp(-scores))
                elif config.preprocess_type == "imagenet":
                    onnx_path = ModelManager._active_onnx_paths.get(model_id, "")
                    has_softmax = self._model_has_softmax_output(onnx_path)
                    if has_softmax is True:
                        pass
                    elif has_softmax is False:
                        # INT8 量化可能压缩 logit 范围，检测并补偿
                        logit_std = float(np.std(scores))
                        if logit_std > 0 and logit_std < 2.0:
                            target_std = 5.0
                            scores = scores * (target_std / logit_std)
                        exp_scores = np.exp(scores - np.max(scores))
                        scores = exp_scores / exp_scores.sum()
                    else:
                        raw_sum = float(np.sum(scores))
                        raw_min = float(np.min(scores))
                        raw_max = float(np.max(scores))
                        if raw_min >= -0.05 and raw_max <= 1.05 and 0.90 <= raw_sum <= 1.10:
                            pass
                        else:
                            exp_scores = np.exp(scores - np.max(scores))
                            scores = exp_scores / exp_scores.sum()

                results[valid_indices[j]] = {
                    "scores": scores,
                    "labels": labels,
                    "output_name": selected_name,
                }

        return results

    def batch_classify_image(self, model_id: str, image_paths: list[str],
                             batch_size: int = 8) -> list[tuple[str, float] | None]:
        import numpy as np

        results_raw = self.batch_predict(model_id, image_paths, batch_size)
        results: list[tuple[str, float] | None] = []
        for item in results_raw:
            if item is None:
                results.append(None)
                continue
            scores = item["scores"]
            labels = item["labels"]
            top_idx = int(np.argmax(scores))
            label = labels[top_idx] if top_idx < len(labels) else f"class_{top_idx}"
            results.append((label, float(scores[top_idx])))
        return results

    def batch_classify_image_top_n(self, model_id: str, image_paths: list[str],
                                   n: int = 20, batch_size: int = 8
                                   ) -> list[list[tuple[str, float]] | None]:
        import numpy as np

        results_raw = self.batch_predict(model_id, image_paths, batch_size)
        results: list[list[tuple[str, float]] | None] = []
        for item in results_raw:
            if item is None:
                results.append(None)
                continue
            scores = item["scores"]
            labels = item["labels"]
            top_idx = np.argsort(scores)[::-1][:n]
            results.append([
                (labels[idx] if idx < len(labels) else f"class_{idx}", float(scores[idx]))
                for idx in top_idx
            ])
        return results

    def classify_image(self, model_id: str, image_path: str) -> tuple[str, float]:
        if model_id not in self._sessions:
            ok, msg = self.load_model(model_id)
            if not ok:
                raise RuntimeError(f"模型加载失败: {msg}")

        import numpy as np

        scores, labels, selected_output_name = self._predict_scores(model_id, image_path)

        top5_idx = np.argsort(scores)[::-1][:5]

        results = []
        for idx in top5_idx:
            label = labels[idx] if idx < len(labels) else f"class_{idx}"
            confidence = float(scores[idx])
            results.append((label, confidence))

        _logger.info(
            "classify_image [%s] selected_output=%s top-5=%s",
            model_id,
            selected_output_name,
            ", ".join(f"{l}={c:.3f}" for l, c in results),
        )

        return results[0][0], results[0][1]

    def classify_image_top_n(self, model_id: str, image_path: str, n: int = 20) -> list[tuple[str, float]]:
        if model_id not in self._sessions:
            ok, msg = self.load_model(model_id)
            if not ok:
                raise RuntimeError(f"模型加载失败: {msg}")

        import numpy as np

        scores, labels, selected_output_name = self._predict_scores(model_id, image_path)

        top_idx = np.argsort(scores)[::-1][:n]

        results = []
        for idx in top_idx:
            label = labels[idx] if idx < len(labels) else f"class_{idx}"
            confidence = float(scores[idx])
            results.append((label, confidence))

        _logger.info(
            "classify_image_top_n [%s] selected_output=%s top-%d: %s",
            model_id, selected_output_name, n,
            ", ".join(f"{l}={c:.3f}" for l, c in results[:10]),
        )

        return results

    def get_full_probs(self, model_id: str, image_path: str) -> tuple[list[str], list[float]]:
        config = load_model_config(model_id)
        use_int8 = config.use_int8
        cache_path = self._tag_probs_path(model_id, image_path, use_int8=use_int8)
        if os.path.isfile(cache_path):
            return self._read_probs_from_disk(cache_path)

        if model_id not in self._sessions:
            ok, msg = self.load_model(model_id)
            if not ok:
                raise RuntimeError(f"模型加载失败: {msg}")

        import numpy as np
        scores, labels, _ = self._predict_scores(model_id, image_path)
        self._save_probs_to_disk(cache_path, labels, scores)
        return labels, [float(s) for s in scores]

    @staticmethod
    def _tag_probs_path(model_id: str, image_path: str, use_int8: bool = False) -> str:
        import hashlib
        suffix = "_int8" if use_int8 else ""
        key = hashlib.md5(f"{model_id}{suffix}:{image_path}".encode()).hexdigest()
        return os.path.join(_TAG_PROBS_DIR, f"{key}.npz")

    @staticmethod
    def _save_probs_to_disk(path: str, labels: list[str], scores) -> None:
        import numpy as np
        os.makedirs(os.path.dirname(path), exist_ok=True)
        np.savez_compressed(path, labels=labels, probs=np.asarray(scores, dtype=np.float32))

    @staticmethod
    def _read_probs_from_disk(path: str) -> tuple[list[str], list[float]]:
        import numpy as np
        data = np.load(path, allow_pickle=False)
        labels = list(data["labels"].astype(str))
        probs = [float(p) for p in data["probs"]]
        return labels, probs

    def clear_tag_probs_cache(self) -> None:
        import glob
        if os.path.isdir(_TAG_PROBS_DIR):
            for f in glob.glob(os.path.join(_TAG_PROBS_DIR, "*.npz")):
                try:
                    os.remove(f)
                except OSError:
                    pass

    def evict_oldest_tag_cache(self, max_count: int) -> None:
        import glob
        if not os.path.isdir(_TAG_PROBS_DIR):
            return
        files = glob.glob(os.path.join(_TAG_PROBS_DIR, "*.npz"))
        if len(files) <= max_count:
            return
        files_with_time = [(f, os.path.getmtime(f)) for f in files]
        files_with_time.sort(key=lambda x: x[1])
        to_delete = len(files) - max_count
        for f, _ in files_with_time[:to_delete]:
            try:
                os.remove(f)
            except OSError:
                pass

    def _preprocess(self, image_path: str, config: ModelConfig, input_shape, model_id: str):
        import numpy as np
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = 178_956_970
        img = Image.open(image_path).convert("RGB")

        channels_first, h, w = self._resolve_input_layout(input_shape, config)
        img = img.resize((h, w), Image.BILINEAR)
        arr = np.array(img, dtype=np.float32) / 255.0

        preprocess_type = config.preprocess_type or "imagenet"
        if model_id == "pixai_tagger_v09":
            preprocess_type = "pixai"
        elif model_id == "wd14_convnext_v2" and preprocess_type == "wd14":
            preprocess_type = "wd14_nhwc"

        if preprocess_type == "imagenet":
            arr = (arr - np.array(_IMAGENET_MEAN)) / np.array(_IMAGENET_STD)

        if channels_first:
            arr = arr.transpose(2, 0, 1)
        arr = np.expand_dims(arr, axis=0)
        _logger.info("Shape after preprocessing: %s", arr.shape)
        _logger.info("Value range after preprocessing: %s - %s", float(arr.min()), float(arr.max()))
        return arr.astype(np.float32)

    def _predict_scores(self, model_id: str, image_path: str):
        import numpy as np

        session = self._sessions[model_id]
        config = load_model_config(model_id)
        input_meta = session.get_inputs()[0]
        input_data = self._preprocess(image_path, config, input_meta.shape, model_id)
        input_name = input_meta.name
        output_metas = session.get_outputs()
        output_names = [out.name for out in output_metas]
        outputs = session.run(output_names, {input_name: input_data})
        _logger.info("Number of ONNX outputs: %d", len(outputs))
        _logger.info(
            "ONNX outputs detail [%s]: %s",
            model_id,
            ", ".join(f"{meta.name} shape={meta.shape}" for meta in output_metas),
        )

        selected_index = self._select_output_index(model_id, output_metas)
        selected_meta = output_metas[selected_index]
        selected_name = selected_meta.name
        selected_output = np.asarray(outputs[selected_index])
        if selected_output.ndim >= 2:
            scores = np.asarray(selected_output[0], dtype=np.float32)
        else:
            scores = np.asarray(selected_output, dtype=np.float32)

        raw_sum = float(np.sum(scores))
        raw_min = float(np.min(scores))
        raw_max = float(np.max(scores))

        _logger.info(
            "[诊断] %s 输出 '%s' (idx=%d/%d): raw_min=%.6f raw_max=%.6f raw_sum=%.6f shape=%s",
            model_id, selected_name, selected_index, len(output_metas),
            raw_min, raw_max, raw_sum,
            scores.shape,
        )

        applied_activation = "none"
        if self._should_apply_sigmoid(selected_name):
            scores = 1.0 / (1.0 + np.exp(-scores))
            applied_activation = "sigmoid"
        elif config.preprocess_type == "imagenet":
            onnx_path = ModelManager._active_onnx_paths.get(model_id, "")
            has_softmax = self._model_has_softmax_output(onnx_path)
            if has_softmax is True:
                _logger.info("[诊断] ONNX 图含 Softmax 节点，跳过激活")
                applied_activation = "skip (graph softmax)"
            elif has_softmax is False:
                # INT8 量化可能压缩 logit 范围，检测并补偿
                logit_std = float(np.std(scores))
                if logit_std > 0 and logit_std < 2.0:
                    target_std = 5.0
                    scores = scores * (target_std / logit_std)
                    _logger.info("[诊断] INT8 logit 缩放: std=%.2f → %.2f", logit_std, target_std)
                exp_scores = np.exp(scores - np.max(scores))
                scores = exp_scores / exp_scores.sum()
                applied_activation = "softmax"
            else:
                if raw_min >= -0.05 and raw_max <= 1.05 and 0.90 <= raw_sum <= 1.10:
                    _logger.info("[诊断] 启发式检测：模型输出已含 softmax，跳过二次激活")
                    applied_activation = "skip (heuristic)"
                else:
                    exp_scores = np.exp(scores - np.max(scores))
                    scores = exp_scores / exp_scores.sum()
                    applied_activation = "softmax"

        _logger.info(
            "[诊断] %s 激活=%s: after_max=%.6f top5=%s",
            model_id, applied_activation, float(np.max(scores)),
            [float(v) for v in np.sort(scores)[-5:][::-1]],
        )

        _logger.info("Selected ONNX output [%s]: %s shape=%s", model_id, selected_name, selected_output.shape)
        _logger.info("Probability max: %s min: %s", float(scores.max()), float(scores.min()))

        if model_id == "pixai_tagger_v09":
            prediction_index = self._find_output_index(output_metas, "prediction")
            if prediction_index is not None and prediction_index != selected_index:
                prediction_output = np.asarray(outputs[prediction_index])
                prediction_scores = (
                    np.asarray(prediction_output[0], dtype=np.float32)
                    if prediction_output.ndim >= 2
                    else np.asarray(prediction_output, dtype=np.float32)
                )
                max_diff = float(np.max(np.abs(prediction_scores - scores)))
                _logger.info("PixAI debug: max(abs(prediction - sigmoid(logits)))=%s", max_diff)

        labels = self._get_labels_internal(model_id, expected_count=len(scores))
        _logger.info("Number of labels: %d", len(labels))
        _logger.info("First 5 labels: %s", labels[:5])
        if model_id == "pixai_tagger_v09" and labels:
            onegirl_idx = 0 if labels[0] == "1girl" else -1
            if onegirl_idx >= 0:
                _logger.info("PixAI debug: 1girl index=%d prob=%.4f", onegirl_idx, float(scores[onegirl_idx]))
            _logger.info(
                "PixAI debug: first 5 probabilities=%s",
                [float(v) for v in scores[:5]],
            )

        if len(labels) != len(scores):
            raise RuntimeError(
                f"模型 {model_id} 标签数({len(labels)}) 与输出维度({len(scores)})不一致"
            )

        return scores, labels, selected_name

    @staticmethod
    def _resolve_input_layout(input_shape, config: ModelConfig) -> tuple[bool, int, int]:
        dims = list(input_shape or [])
        if len(dims) >= 4:
            if dims[1] == 3 and isinstance(dims[2], int) and isinstance(dims[3], int):
                return True, dims[2], dims[3]
            if dims[3] == 3 and isinstance(dims[1], int) and isinstance(dims[2], int):
                return False, dims[1], dims[2]
        h, w = config.input_size
        return True, h, w

    @staticmethod
    def _select_output_index(model_id: str, output_metas) -> int:
        if model_id == "pixai_tagger_v09":
            logits_index = ModelManager._find_output_index(output_metas, "logits")
            if logits_index is not None:
                return logits_index
        priorities = (
            lambda name: name == "prediction",
            lambda name: "sigmoid" in name,
            lambda name: name == "logits",
        )
        lowered = [meta.name.lower() for meta in output_metas]
        for matcher in priorities:
            for idx, name in enumerate(lowered):
                if matcher(name):
                    return idx
        return 0

    @staticmethod
    def _find_output_index(output_metas, exact_name: str) -> int | None:
        exact_name = exact_name.lower()
        for idx, meta in enumerate(output_metas):
            if meta.name.lower() == exact_name:
                return idx
        return None

    @staticmethod
    def _should_apply_sigmoid(output_name: str) -> bool:
        lowered = output_name.lower()
        return lowered == "logits" or (("logit" in lowered) and ("sigmoid" not in lowered))

    @classmethod
    def _model_has_softmax_output(cls, onnx_path: str) -> bool | None:
        """检查 ONNX 模型图中直接产出输出节点的算子是否为 Softmax。"""
        if not onnx_path or not os.path.isfile(onnx_path):
            return None
        if onnx_path in cls._softmax_cache:
            return cls._softmax_cache[onnx_path]
        try:
            import onnx
            model = onnx.load(onnx_path, load_external_data=False)
            output_names = {o.name for o in model.graph.output}
            for node in reversed(model.graph.node):
                for out_name in node.output:
                    if out_name in output_names:
                        result = node.op_type == "Softmax"
                        cls._softmax_cache[onnx_path] = result
                        del model
                        return result
            del model
        except Exception:
            pass
        cls._softmax_cache[onnx_path] = None
        return None

    @staticmethod
    def _read_labels_file(labels_path: str) -> list[str]:
        labels: list[str] = []
        if not os.path.isfile(labels_path):
            return labels
        try:
            with open(labels_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content.startswith("["):
                loaded = json.loads(content)
                if isinstance(loaded, list):
                    labels = [str(item) for item in loaded]
            else:
                ModelManager._convert_if_csv(labels_path)
                with open(labels_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, list):
                    labels = [str(item) for item in loaded]
        except (OSError, json.JSONDecodeError):
            return []
        return labels

    @staticmethod
    def _labels_look_numeric(model_id: str, labels: list[str]) -> bool:
        if not labels:
            return False
        sample = labels[: min(20, len(labels))]
        if model_id != "pixai_tagger_v09":
            return False
        return all(isinstance(lbl, str) and lbl.isdigit() for lbl in sample)

    def _labels_need_repair(
        self,
        model_id: str,
        labels: list[str],
        expected_count: int | None,
    ) -> bool:
        if not labels:
            return True
        if expected_count is not None and len(labels) != expected_count:
            return True
        return self._labels_look_numeric(model_id, labels)

    def _repair_builtin_labels(self, model_id: str, labels_path: str) -> bool:
        entry = get_builtin_by_id(model_id)
        if entry is None or not entry.labels_url:
            return False
        cancel_event = threading.Event()
        if os.path.isfile(labels_path):
            try:
                os.remove(labels_path)
            except OSError:
                pass
        success, err = self._download_with_fallback(
            entry.labels_url,
            entry.mirror_labels_url,
            labels_path,
            None,
            None,
            cancel_event,
            entry.labels_filename,
        )
        if not success:
            _logger.warning("重新下载标签文件失败 [%s]: %s", model_id, err)
            return False
        self._convert_if_csv(labels_path)
        self._labels_cache.pop(model_id, None)
        _logger.info("已重新生成模型 %s 的标签文件: %s", model_id, labels_path)
        return True

    def _download_file(self, url: str, dest: str, progress_callback, status_callback,
                       cancel_event: threading.Event, filename: str,
                       using_mirror: bool) -> tuple[bool, str]:
        if not url:
            return False, "下载地址为空"
        if status_callback:
            server = "镜像服务器" if using_mirror else "主服务器"
            status_callback(f"正在连接{server}…")
        tmp_dest = dest + ".downloading"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            old_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(_READ_TIMEOUT)
            try:
                resp = urllib.request.urlopen(req, timeout=_CONNECT_TIMEOUT)
            finally:
                socket.setdefaulttimeout(old_timeout)
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 65536
            start_time = time.monotonic()
            last_cb_time = start_time
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if status_callback:
                status_callback(f"正在下载 {filename}…")
            if progress_callback:
                progress_callback(filename, 0, total, 0, using_mirror)
            with open(tmp_dest, "wb") as f:
                while True:
                    if cancel_event.is_set():
                        f.close()
                        if os.path.isfile(tmp_dest):
                            os.remove(tmp_dest)
                        return False, "已取消"
                    try:
                        chunk = resp.read(chunk_size)
                    except socket.timeout:
                        if os.path.isfile(tmp_dest):
                            os.remove(tmp_dest)
                        return False, "读取数据超时"
                    except Exception as e:
                        if os.path.isfile(tmp_dest):
                            os.remove(tmp_dest)
                        return False, f"读取数据失败: {e}"
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = time.monotonic()
                    if progress_callback and (now - last_cb_time >= 0.1):
                        elapsed = now - start_time
                        speed = downloaded / elapsed if elapsed > 0 else 0
                        progress_callback(filename, downloaded, total, speed, using_mirror)
                        last_cb_time = now
            if progress_callback:
                elapsed = time.monotonic() - start_time
                speed = downloaded / elapsed if elapsed > 0 else 0
                progress_callback(filename, downloaded, total, speed, using_mirror)
            if os.path.isfile(dest):
                os.remove(dest)
            os.rename(tmp_dest, dest)
            return True, ""
        except socket.timeout:
            if os.path.isfile(tmp_dest):
                try:
                    os.remove(tmp_dest)
                except OSError:
                    pass
            return False, "连接超时"
        except Exception as e:
            if os.path.isfile(tmp_dest):
                try:
                    os.remove(tmp_dest)
                except OSError:
                    pass
            return False, str(e)

    def _download_with_fallback(self, primary_url: str, mirror_url: str, dest: str,
                                progress_callback, status_callback,
                                cancel_event: threading.Event,
                                filename: str) -> tuple[bool, str]:
        success, err = self._download_file(
            primary_url, dest, progress_callback, status_callback,
            cancel_event, filename, False,
        )
        if success:
            return True, ""
        if cancel_event.is_set():
            return False, "已取消"
        if os.path.isfile(dest):
            try:
                os.remove(dest)
            except OSError:
                pass
        if mirror_url:
            _logger.warning("主URL下载失败(%s)，尝试镜像: %s", err, mirror_url)
            success, err = self._download_file(
                mirror_url, dest, progress_callback, status_callback,
                cancel_event, filename, True,
            )
            if success:
                return True, ""
        return False, f"下载失败: {err}"

    @staticmethod
    def _convert_csv_labels(csv_path: str, json_path: str) -> None:
        try:
            labels = ModelManager._read_csv_labels(csv_path)
        except Exception as e:
            _logger.warning("CSV标签转换失败: %s", e)
            return
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(labels, f, indent=2, ensure_ascii=False)
            _logger.info("已将CSV标签转换为JSON: %s (%d tags)", json_path, len(labels))
        except OSError as e:
            _logger.warning("JSON标签保存失败: %s", e)

    @staticmethod
    def _convert_if_csv(path: str) -> None:
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read(512).strip()
        except OSError:
            return
        if content.startswith("[") or content.startswith("{"):
            return
        try:
            labels = ModelManager._read_csv_labels(path)
        except Exception as e:
            _logger.warning("CSV标签转换失败: %s", e)
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(labels, f, indent=2, ensure_ascii=False)
            _logger.info("已将CSV标签转换为JSON: %s (%d tags)", path, len(labels))
        except OSError as e:
            _logger.warning("JSON标签保存失败: %s", e)

    @staticmethod
    def _read_csv_labels(csv_path: str) -> list[str]:
        import csv as csv_mod

        labels: list[str] = []
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv_mod.reader(f)
            header = next(reader, None)
            name_index = None
            if header:
                normalized = [col.strip().lower() for col in header]
                if "name" in normalized:
                    name_index = normalized.index("name")
            for row in reader:
                if not row:
                    continue
                if name_index is not None and name_index < len(row):
                    labels.append(row[name_index])
                elif len(row) > 1:
                    labels.append(row[1])
                else:
                    labels.append(row[0])
        return labels
