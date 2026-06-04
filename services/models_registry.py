from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QSettings

from paths import MODEL_DIR

_logger = logging.getLogger("models_registry")

CATEGORY_MAP_IMAGENET = "imagenet"
CATEGORY_MAP_DANBOORU = "danbooru"
CATEGORY_MAP_WD14 = "WD14 动漫标签"
CATEGORY_MAP_GENERIC = "generic"
PREPROCESS_IMAGENET = "imagenet"
PREPROCESS_WD14_NHWC = "wd14_nhwc"
PREPROCESS_PIXAI = "pixai"


@dataclass
class ModelRegistryEntry:
    id: str
    name: str
    description: str
    classes: int
    size_mb: float
    download_url: str
    labels_url: str
    mirror_download_url: str = ""
    mirror_labels_url: str = ""
    default_map: str = CATEGORY_MAP_IMAGENET
    onnx_filename: str = ""
    labels_filename: str = ""

    def __post_init__(self):
        if not self.onnx_filename:
            self.onnx_filename = f"{self.id}.onnx"
        if not self.labels_filename:
            self.labels_filename = f"{self.id}_labels.json"

    @property
    def onnx_path(self) -> str:
        return os.path.join(MODEL_DIR, self.onnx_filename)

    @property
    def labels_path(self) -> str:
        return os.path.join(MODEL_DIR, self.labels_filename)

    @property
    def is_installed(self) -> bool:
        return os.path.isfile(self.onnx_path)

    @property
    def config_path(self) -> str:
        return os.path.join(MODEL_DIR, f"{self.id}_config.json")


BUILTIN_MODELS: list[ModelRegistryEntry] = [
    ModelRegistryEntry(
        id="mobilenet_v3_small",
        name="MobileNet-V3-Small",
        description="轻量通用分类",
        classes=1000,
        size_mb=12,
        download_url="https://hf-mirror.com/onnxmodelzoo/mobilenet_v3_small_Opset18/resolve/main/mobilenet_v3_small_Opset18.onnx",
        mirror_download_url="https://huggingface.co/onnxmodelzoo/mobilenet_v3_small_Opset18/resolve/main/mobilenet_v3_small_Opset18.onnx",
        labels_url="https://cdn.jsdelivr.net/gh/anishathalye/imagenet-simple-labels@master/imagenet-simple-labels.json",
        mirror_labels_url="https://raw.githubusercontent.com/anishathalye/imagenet-simple-labels/master/imagenet-simple-labels.json",
        default_map=CATEGORY_MAP_IMAGENET,
        onnx_filename="mobilenet_v3_small.onnx",
        labels_filename="mobilenet_v3_small_labels.json",
    ),
    ModelRegistryEntry(
        id="mobilenet_v3_large",
        name="MobileNet-V3-Large",
        description="通用图片分类",
        classes=1000,
        size_mb=20.9,
        download_url="https://hf-mirror.com/onnxmodelzoo/mobilenet_v3_large_Opset18/resolve/main/mobilenet_v3_large_Opset18.onnx",
        mirror_download_url="https://huggingface.co/onnxmodelzoo/mobilenet_v3_large_Opset18/resolve/main/mobilenet_v3_large_Opset18.onnx",
        labels_url="https://cdn.jsdelivr.net/gh/anishathalye/imagenet-simple-labels@master/imagenet-simple-labels.json",
        mirror_labels_url="https://raw.githubusercontent.com/anishathalye/imagenet-simple-labels/master/imagenet-simple-labels.json",
        default_map=CATEGORY_MAP_IMAGENET,
        onnx_filename="mobilenet_v3_large.onnx",
        labels_filename="mobilenet_v3_large_labels.json",
    ),
    ModelRegistryEntry(
        id="pixai_tagger_v09",
        name="pixai-tagger-v0.9",
        description="动漫标签识别 (PixAI)",
        classes=13461,
        size_mb=350,
        download_url="https://hf-mirror.com/deepghs/pixai-tagger-v0.9-onnx/resolve/main/model.onnx",
        mirror_download_url="https://huggingface.co/deepghs/pixai-tagger-v0.9-onnx/resolve/main/model.onnx",
        labels_url="https://hf-mirror.com/deepghs/pixai-tagger-v0.9-onnx/resolve/main/selected_tags.csv",
        mirror_labels_url="https://huggingface.co/deepghs/pixai-tagger-v0.9-onnx/resolve/main/selected_tags.csv",
        default_map=CATEGORY_MAP_WD14,
        onnx_filename="pixai_tagger_v09.onnx",
        labels_filename="pixai_tagger_v09_labels.json",
    ),
]


def get_builtin_by_id(model_id: str) -> ModelRegistryEntry | None:
    for m in BUILTIN_MODELS:
        if m.id == model_id:
            return m
    return None


def get_builtin_by_onnx(onnx_filename: str) -> ModelRegistryEntry | None:
    for m in BUILTIN_MODELS:
        if m.onnx_filename == onnx_filename:
            return m
    return None


@dataclass
class ModelConfig:
    model_id: str = ""
    enabled: bool = True
    confidence_threshold: float = 0.2
    category_map_type: str = CATEGORY_MAP_IMAGENET
    preset_name: str = "ImageNet 通用分类"
    input_size: tuple[int, int] = (224, 224)
    input_channels: int = 3
    preprocess_type: str = "imagenet"
    custom_labels_path: str = ""
    use_int8: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "enabled": self.enabled,
            "confidence_threshold": self.confidence_threshold,
            "category_map_type": self.category_map_type,
            "preset_name": self.preset_name,
            "input_size": list(self.input_size),
            "input_channels": self.input_channels,
            "preprocess_type": self.preprocess_type,
            "custom_labels_path": self.custom_labels_path,
            "use_int8": self.use_int8,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ModelConfig:
        return cls(
            model_id=d.get("model_id", ""),
            enabled=d.get("enabled", True),
            confidence_threshold=d.get("confidence_threshold", 0.3),
            category_map_type=d.get("category_map_type", CATEGORY_MAP_IMAGENET),
            preset_name=d.get("preset_name", "ImageNet 通用分类"),
            input_size=tuple(d.get("input_size", [224, 224])),
            input_channels=d.get("input_channels", 3),
            preprocess_type=d.get("preprocess_type", "imagenet"),
            custom_labels_path=d.get("custom_labels_path", ""),
            use_int8=d.get("use_int8", False),
        )


def load_model_config(model_id: str) -> ModelConfig:
    for m in BUILTIN_MODELS:
        if m.id == model_id:
            cfg_path = m.config_path
            if os.path.isfile(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return ModelConfig.from_dict(data)
                except (OSError, json.JSONDecodeError):
                    pass
            return ModelConfig(
                model_id=model_id,
                category_map_type=m.default_map,
                preprocess_type=_builtin_preprocess_type(model_id),
            )
    cfg_path = os.path.join(MODEL_DIR, f"{model_id}_config.json")
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return ModelConfig.from_dict(data)
        except (OSError, json.JSONDecodeError):
            pass
    return ModelConfig(model_id=model_id)


def save_model_config(config: ModelConfig) -> None:
    os.makedirs(MODEL_DIR, exist_ok=True)
    cfg_path = os.path.join(MODEL_DIR, f"{config.model_id}_config.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(config.to_dict(), f, indent=2, ensure_ascii=False)
    from services.classifier import clear_unified_cache
    clear_unified_cache()


def auto_validate_model(onnx_path: str) -> dict[str, Any] | None:
    try:
        import onnxruntime as ort
    except ImportError:
        return None
    try:
        session = ort.InferenceSession(onnx_path)
        inp = session.get_inputs()[0]
        shape = inp.shape
        input_channels = 3
        input_h = 224
        input_w = 224
        if len(shape) == 4:
            if shape[1] == 3 and isinstance(shape[2], int) and isinstance(shape[3], int):
                input_channels = 3
                input_h = shape[2]
                input_w = shape[3]
            elif shape[3] == 3 and isinstance(shape[1], int) and isinstance(shape[2], int):
                input_channels = 3
                input_h = shape[1]
                input_w = shape[2]
            else:
                input_channels = shape[1] if isinstance(shape[1], int) else 3
                input_h = shape[2] if isinstance(shape[2], int) else 224
                input_w = shape[3] if isinstance(shape[3], int) else 224

        output_metas = session.get_outputs()
        num_classes = 0
        for out in output_metas:
            if len(out.shape) >= 2 and isinstance(out.shape[1], int):
                num_classes = max(num_classes, out.shape[1])
        del session
        preprocess = PREPROCESS_IMAGENET
        filename = os.path.basename(onnx_path).lower()
        if "pixai" in filename:
            preprocess = PREPROCESS_PIXAI
        elif len(shape) == 4 and shape[3] == 3 and num_classes > 5000:
            preprocess = PREPROCESS_WD14_NHWC
        elif input_channels != 3:
            preprocess = "zero_one"
        return {
            "input_size": (input_h, input_w),
            "input_channels": input_channels,
            "num_classes": num_classes,
            "preprocess_type": preprocess,
        }
    except Exception as e:
        _logger.warning("Auto-validate failed for %s: %s", onnx_path, e)
        return None


def auto_detect_category_map(labels: list[str]) -> str:
    if not labels:
        return CATEGORY_MAP_GENERIC
    sample = " ".join(labels[:50]).lower()
    danbooru_keywords = ["1girl", "2girls", "solo", "long_hair", "breasts", "blush",
                         "smile", "blue_eyes", "blonde_hair", "skirt", "dress"]
    for kw in danbooru_keywords:
        if kw in sample:
            return CATEGORY_MAP_WD14
    return CATEGORY_MAP_IMAGENET


def ensure_labels_file(onnx_path: str, labels_path: str) -> tuple[bool, list[str]]:
    if os.path.isfile(labels_path):
        try:
            with open(labels_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content.startswith("["):
                labels = json.loads(content)
            else:
                labels = [line.strip() for line in content.splitlines() if line.strip()]
            return True, labels
        except (OSError, json.JSONDecodeError):
            pass

    info = auto_validate_model(onnx_path)
    num_classes = info["num_classes"] if info else 1000
    labels = [f"class_{i}" for i in range(num_classes)]
    try:
        os.makedirs(os.path.dirname(labels_path), exist_ok=True)
        with open(labels_path, "w", encoding="utf-8") as f:
            json.dump(labels, f, indent=2)
        return False, labels
    except OSError:
        return False, []


def _builtin_preprocess_type(model_id: str) -> str:
    if model_id == "wd14_convnext_v2":
        return PREPROCESS_WD14_NHWC
    if model_id == "pixai_tagger_v09":
        return PREPROCESS_PIXAI
    return PREPROCESS_IMAGENET


def scan_installed_models() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if not os.path.isdir(MODEL_DIR):
        return result

    seen_ids: set[str] = set()

    for f in sorted(os.listdir(MODEL_DIR)):
        if not f.endswith(".onnx"):
            continue
        if f.endswith("_int8.onnx"):
            continue
        onnx_path = os.path.join(MODEL_DIR, f)
        if not os.path.isfile(onnx_path):
            continue

        model_id = f[:-5]
        seen_ids.add(model_id)
        builtin = get_builtin_by_onnx(f)
        name = builtin.name if builtin else model_id.replace("_", " ").title()
        desc = builtin.description if builtin else ""
        classes = builtin.classes if builtin else 0
        size_mb = os.path.getsize(onnx_path) / (1024 * 1024)
        default_map = builtin.default_map if builtin else CATEGORY_MAP_GENERIC

        config = load_model_config(model_id)
        needs_save = False
        if config.model_id != model_id:
            config.model_id = model_id
            needs_save = True
        if builtin:
            if config.category_map_type != builtin.default_map:
                if config.category_map_type in (CATEGORY_MAP_IMAGENET, CATEGORY_MAP_DANBOORU, CATEGORY_MAP_GENERIC):
                    config.category_map_type = builtin.default_map
                    needs_save = True
            builtin_preprocess = _builtin_preprocess_type(model_id)
            if config.preprocess_type != builtin_preprocess:
                config.preprocess_type = builtin_preprocess
                needs_save = True

        needs_validation = False
        if config.input_channels > 10:
            needs_validation = True
        if isinstance(config.input_size, (tuple, list)) and len(config.input_size) == 2:
            h, w = config.input_size
            if h <= 5 or w <= 5:
                needs_validation = True
        if needs_validation:
            info = auto_validate_model(onnx_path)
            if info:
                config.input_size = info["input_size"]
                config.input_channels = info["input_channels"]
                config.preprocess_type = info["preprocess_type"]
                needs_save = True

        if config.model_id != model_id:
            if builtin:
                config.category_map_type = builtin.default_map
            info = auto_validate_model(onnx_path)
            if info:
                config.input_size = info["input_size"]
                config.input_channels = info["input_channels"]
                config.preprocess_type = info["preprocess_type"]
            needs_save = True
        if needs_save:
            save_model_config(config)

        if not builtin:
            labels_path = onnx_path.replace(".onnx", "_labels.json")
            if os.path.isfile(labels_path):
                try:
                    with open(labels_path, "r", encoding="utf-8") as lf:
                        labels_data = json.load(lf)
                    if isinstance(labels_data, list) and len(labels_data) > 0:
                        sample = labels_data[:min(10, len(labels_data))]
                        if all(isinstance(lbl, str) and lbl.startswith("class_") for lbl in sample):
                            _logger.warning(
                                "模型 %s 的标签全部为自动生成占位符，分类结果将全部归入「其他」。"
                                "请提供正确的标签文件或重新下载该模型。", model_id
                            )
                except (OSError, json.JSONDecodeError):
                    pass

        int8_path = onnx_path.replace(".onnx", "_int8.onnx")
        has_int8 = os.path.isfile(int8_path)

        if has_int8 and config.use_int8:
            display_size_mb = os.path.getsize(int8_path) / (1024 * 1024)
        else:
            display_size_mb = size_mb

        result.append({
            "model_id": model_id,
            "name": name,
            "description": desc,
            "classes": classes,
            "size_mb": round(display_size_mb, 1),
            "fp32_size_mb": round(size_mb, 1),
            "onnx_path": onnx_path,
            "onnx_filename": f,
            "default_map": default_map,
            "builtin_id": builtin.id if builtin else None,
            "enabled": config.enabled,
            "config": config,
            "has_int8": has_int8,
            "int8_path": int8_path,
            "int8_only": False,
        })

    for f in sorted(os.listdir(MODEL_DIR)):
        if not f.endswith("_int8.onnx"):
            continue
        model_id = f.removesuffix("_int8.onnx")
        if model_id in seen_ids:
            continue

        int8_path = os.path.join(MODEL_DIR, f)
        if not os.path.isfile(int8_path):
            continue

        seen_ids.add(model_id)
        builtin = get_builtin_by_id(model_id)
        name = builtin.name if builtin else model_id.replace("_", " ").title()
        desc = builtin.description if builtin else ""
        classes = builtin.classes if builtin else 0
        size_mb = os.path.getsize(int8_path) / (1024 * 1024)
        default_map = builtin.default_map if builtin else CATEGORY_MAP_GENERIC

        fp32_filename = model_id + ".onnx"
        fp32_path = os.path.join(MODEL_DIR, fp32_filename)

        config = load_model_config(model_id)
        if not config.use_int8:
            config.use_int8 = True
            save_model_config(config)

        needs_save = False
        if config.model_id != model_id:
            config.model_id = model_id
            needs_save = True
        if builtin:
            if config.category_map_type != builtin.default_map:
                if config.category_map_type in (CATEGORY_MAP_IMAGENET, CATEGORY_MAP_DANBOORU, CATEGORY_MAP_GENERIC):
                    config.category_map_type = builtin.default_map
                    needs_save = True
            builtin_preprocess = _builtin_preprocess_type(model_id)
            if config.preprocess_type != builtin_preprocess:
                config.preprocess_type = builtin_preprocess
                needs_save = True
        if needs_save:
            save_model_config(config)

        result.append({
            "model_id": model_id,
            "name": name,
            "description": desc,
            "classes": classes,
            "size_mb": round(size_mb, 1),
            "fp32_size_mb": 0,
            "onnx_path": fp32_path,
            "onnx_filename": fp32_filename,
            "default_map": default_map,
            "builtin_id": builtin.id if builtin else None,
            "enabled": config.enabled,
            "config": config,
            "has_int8": True,
            "int8_path": int8_path,
            "int8_only": True,
        })

    return result


def get_fallback_level() -> int:
    s = QSettings("ImageGallery", "ImageGallery")
    return s.value("classify/fallback_level", 2, type=int)

def set_fallback_level(level: int) -> None:
    s = QSettings("ImageGallery", "ImageGallery")
    s.setValue("classify/fallback_level", level)
    s.sync()
    from services.classifier import clear_unified_cache
    clear_unified_cache()

def get_model_order() -> list[str]:
    s = QSettings("ImageGallery", "ImageGallery")
    order = s.value("classify/model_order", [], type=list)
    if not isinstance(order, list):
        order = []
    return [str(x) for x in order]

def set_model_order(order: list[str]) -> None:
    s = QSettings("ImageGallery", "ImageGallery")
    s.setValue("classify/model_order", order)
    s.sync()
    from services.classifier import clear_unified_cache
    clear_unified_cache()

def get_enabled_models() -> list[dict[str, Any]]:
    installed = scan_installed_models()
    enabled = [m for m in installed if m["enabled"]]
    order = get_model_order()
    if not order:
        return enabled
    ordered = []
    seen = set()
    for mid in order:
        for m in enabled:
            if m["model_id"] == mid and mid not in seen:
                ordered.append(m)
                seen.add(mid)
                break
    for m in enabled:
        if m["model_id"] not in seen:
            ordered.append(m)
            seen.add(m["model_id"])
    return ordered


def set_model_enabled(model_id: str, enabled: bool) -> None:
    config = load_model_config(model_id)
    config.enabled = enabled
    save_model_config(config)
    from services.classifier import clear_unified_cache
    clear_unified_cache()


def delete_model(model_id: str) -> bool:
    info = None
    for m in scan_installed_models():
        if m["model_id"] == model_id:
            info = m
            break
    if info is None:
        return False

    onnx_path = info["onnx_path"]
    labels_path = onnx_path.replace(".onnx", "_labels.json")
    int8_path = onnx_path.replace(".onnx", "_int8.onnx")
    config_path = os.path.join(MODEL_DIR, f"{model_id}_config.json")

    for p in (onnx_path, labels_path, int8_path, config_path):
        if os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass
    order = get_model_order()
    if model_id in order:
        order.remove(model_id)
        s = QSettings("ImageGallery", "ImageGallery")
        s.setValue("classify/model_order", order)
        s.sync()
    from services.classifier import clear_unified_cache
    clear_unified_cache()
    return True
