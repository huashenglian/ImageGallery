from __future__ import annotations

import json
import logging
import os
import random
import time

from paths import MODEL_DIR, CACHE_DIR
from services.models_registry import (
    ModelConfig,
    load_model_config,
    save_model_config,
    scan_installed_models,
)

_logger = logging.getLogger("quantize_service")


def _collect_calibration_images(count: int = 300) -> list[str]:
    cache_path = os.path.join(CACHE_DIR, "gallery_cache.json.gz")
    images: list[str] = []

    if os.path.isfile(cache_path):
        import gzip

        try:
            with gzip.open(cache_path, "rb") as f:
                data = json.loads(f.read().decode("utf-8"))
            store = data.get("store", data)
            folders = store.get("folders", {})
            for rec in folders.values():
                imgs = rec.get("images", [])
                images.extend(imgs)
        except Exception:
            pass

    if images:
        random.shuffle(images)
        selected = images[:count]
        existing = [p for p in selected if os.path.isfile(p)]
        _logger.info("校准图片: %d/%d 张可用", len(existing), count)
        return existing

    _logger.info("未找到图库缓存，将使用随机数据校准")
    return []


def _preprocess_for_calibration(
    image_path: str, input_size: tuple[int, int], channels_first: bool = True
):
    import numpy as np
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = 178_956_970
    try:
        img = Image.open(image_path).convert("RGB")
        img = img.resize(input_size, Image.BILINEAR)
        arr = np.array(img, dtype=np.float32) / 255.0
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        arr = (arr - mean) / std
        if channels_first:
            arr = arr.transpose(2, 0, 1)
        arr = np.expand_dims(arr, axis=0)
        return arr.astype(np.float32)
    except Exception:
        return None


class _CalibrationDataReader:
    def __init__(
        self,
        image_paths: list[str],
        input_name: str,
        input_size: tuple[int, int],
        channels_first: bool = True,
        batch_size: int = 1,
    ):
        self._paths = image_paths
        self._input_name = input_name
        self._input_size = input_size
        self._channels_first = channels_first
        self._batch_size = batch_size
        self._index = 0

    def get_next(self):
        if self._index >= len(self._paths):
            return None

        batch_paths = self._paths[self._index : self._index + self._batch_size]
        self._index += self._batch_size

        arrays = []
        for p in batch_paths:
            arr = _preprocess_for_calibration(p, self._input_size, self._channels_first)
            if arr is not None:
                arrays.append(arr)

        if not arrays:
            return None

        import numpy as np

        batch = np.concatenate(arrays, axis=0)
        return {self._input_name: batch}

    def rewind(self):
        self._index = 0


class _RandomCalibrationDataReader:
    def __init__(
        self,
        input_name: str,
        input_size: tuple[int, int],
        channels_first: bool = True,
        count: int = 200,
    ):
        self._input_name = input_name
        self._input_size = input_size
        self._channels_first = channels_first
        self._count = count
        self._index = 0

    def get_next(self):
        if self._index >= self._count:
            return None
        self._index += 1

        import numpy as np

        h, w = self._input_size
        if self._channels_first:
            arr = np.random.randn(1, 3, h, w).astype(np.float32)
        else:
            arr = np.random.randn(1, h, w, 3).astype(np.float32)
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        if self._channels_first:
            arr = (arr[0].transpose(1, 2, 0) / 255.0 - mean) / std
            arr = arr.transpose(2, 0, 1)[np.newaxis]
        else:
            arr = (arr / 255.0 - mean) / std
        return {self._input_name: arr.astype(np.float32)}

    def rewind(self):
        self._index = 0


def quantize_model(
    model_id: str,
    progress_callback=None,
    delete_fp32: bool = True,
    cancel_check=None,
) -> tuple[bool, str]:
    if progress_callback is None:
        progress_callback = lambda *a: None

    progress_callback("prepare", 0, f"准备量化模型 {model_id}…")

    try:
        import onnx
        from onnxruntime.quantization import (
            CalibrationMethod,
            QuantType,
            quantize_static,
        )
    except ImportError:
        return False, "需要安装 onnx 和 onnxruntime（pip install onnx onnxruntime）"

    installed = scan_installed_models()
    model_info = None
    for m in installed:
        if m["model_id"] == model_id:
            model_info = m
            break

    if model_info is None:
        return False, f"模型 {model_id} 未安装"

    fp32_path = model_info["onnx_path"]
    int8_path = model_info.get("int8_path") or fp32_path.replace(".onnx", "_int8.onnx")

    if model_info.get("int8_only"):
        return True, "模型已处于 INT8 量化状态"

    if not os.path.isfile(fp32_path):
        return False, f"FP32 模型文件不存在: {fp32_path}"

    fp32_size_mb = os.path.getsize(fp32_path) / (1024 * 1024)
    _logger.info("FP32 模型: %s (%.1f MB)", fp32_path, fp32_size_mb)

    if os.path.isfile(int8_path):
        int8_size_mb = os.path.getsize(int8_path) / (1024 * 1024)
        _logger.info("INT8 模型已存在: %.1f MB，将覆盖", int8_size_mb)
        try:
            os.remove(int8_path)
        except OSError as e:
            return False, f"无法删除已有 INT8 文件: {e}"

    usage = __import__("shutil").disk_usage(os.path.dirname(fp32_path) or ".")
    estimated_int8_mb = fp32_size_mb * 0.35
    if usage.free < estimated_int8_mb * 1024 * 1024 + 50 * 1024 * 1024:
        return False, f"磁盘空间不足（量化约需 {estimated_int8_mb:.0f}MB）"

    if cancel_check and cancel_check():
        return False, "量化已取消"

    config = load_model_config(model_id)
    input_size = config.input_size if config.input_size else (224, 224)

    progress_callback("prepare", 10, "加载 ONNX 模型获取输入信息…")
    try:
        model = onnx.load(fp32_path)
    except Exception as e:
        return False, f"加载 ONNX 模型失败: {e}"

    input_meta = model.graph.input[0]
    input_name = input_meta.name

    dims = [d.dim_value for d in input_meta.type.tensor_type.shape.dim]
    channels_first = True
    if len(dims) >= 4:
        if dims[3] == "3" or (isinstance(dims[3], int) and dims[3] == 3):
            channels_first = False

    # 排除最后全连接层不量化，保留 logit 精度
    nodes_to_exclude = []
    for node in reversed(model.graph.node):
        if node.op_type in ("Gemm", "MatMul"):
            nodes_to_exclude.append(node.name)
            _logger.info("排除最后全连接层不量化: %s (%s)", node.name, node.op_type)
            break

    del model

    if cancel_check and cancel_check():
        return False, "量化已取消"

    progress_callback("calibrate", 20, "收集校准图片…")
    cal_images = _collect_calibration_images(300)

    if cal_images:
        dr = _CalibrationDataReader(
            cal_images, input_name, input_size, channels_first
        )
        _logger.info("使用 %d 张真实图片校准", len(cal_images))
    else:
        dr = _RandomCalibrationDataReader(input_name, input_size, channels_first, count=200)
        _logger.info("使用随机数据校准")

    if cancel_check and cancel_check():
        return False, "量化已取消"

    progress_callback("quantize", 30, "正在量化（可能需要几分钟）…")
    t0 = time.perf_counter()

    try:
        quantize_static(
            fp32_path,
            int8_path,
            dr,
            calibrate_method=CalibrationMethod.MinMax,
            weight_type=QuantType.QInt8,
            activation_type=QuantType.QUInt8,
            per_channel=True,
            extra_options={"ActivationSymmetric": False},
            nodes_to_exclude=nodes_to_exclude,
        )
    except Exception as e:
        _logger.error("量化失败: %s", e)
        if os.path.isfile(int8_path):
            try:
                os.remove(int8_path)
            except OSError:
                pass
        return False, f"量化失败: {e}"

    elapsed = time.perf_counter() - t0

    if not os.path.isfile(int8_path):
        return False, "量化完成但 INT8 文件未生成"

    int8_size_mb = os.path.getsize(int8_path) / (1024 * 1024)
    reduction = (1 - int8_size_mb / fp32_size_mb) * 100

    if cancel_check and cancel_check():
        _logger.info("量化完成但用户已取消，删除 INT8 文件保留 FP32")
        try:
            os.remove(int8_path)
        except OSError:
            pass
        return False, "量化已取消"

    progress_callback("cleanup", 90, "清理临时文件…")

    config.use_int8 = True
    save_model_config(config)

    if delete_fp32:
        try:
            os.remove(fp32_path)
            _logger.info("已删除 FP32 文件: %s", fp32_path)
        except OSError as e:
            _logger.warning("删除 FP32 文件失败: %s（INT8 已生成，不影响使用）", e)

    from services.classifier import clear_unified_cache
    clear_unified_cache()

    progress_callback("cleanup", 100, "量化完成")

    _logger.info(
        "量化完成: %.1f MB → %.1f MB (缩减 %.1f%%), 耗时 %.1f 秒",
        fp32_size_mb, int8_size_mb, reduction, elapsed,
    )

    return True, f"量化完成 ({fp32_size_mb:.1f} → {int8_size_mb:.1f} MB，缩减 {reduction:.0f}%)"
