from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.models_registry import scan_installed_models


def quantize_model_cli(model_id: str, calibration_count: int = 300) -> bool:
    from services.quantize_service import quantize_model

    print(f"\n{'=' * 56}")
    print(f"  量化模型: {model_id}")
    print(f"{'=' * 56}")

    def on_progress(phase, pct, msg):
        if phase == "prepare":
            print(f"  [{phase}] {msg}")
        elif pct % 20 == 0:
            print(f"  [{phase}] {pct}% - {msg}")

    ok, msg = quantize_model(model_id, progress_callback=on_progress, delete_fp32=False)
    if ok:
        print(f"\n  {msg}")
    else:
        print(f"\n  量化失败: {msg}")
    return ok


def verify_quantization(model_id: str, test_count: int = 50) -> None:
    from services.quantize_service import (
        _collect_calibration_images,
        _preprocess_for_calibration,
    )

    print(f"\n{'=' * 56}")
    print(f"  验证量化: {model_id}")
    print(f"{'=' * 56}")

    try:
        import onnxruntime as ort
        import numpy as np
    except ImportError:
        print("  错误: 需要安装 onnxruntime, numpy")
        return

    installed = scan_installed_models()
    model_info = None
    for m in installed:
        if m["model_id"] == model_id:
            model_info = m
            break

    if model_info is None:
        print(f"  错误: 模型 {model_id} 未安装")
        return

    fp32_path = model_info["onnx_path"]
    int8_path = model_info.get("int8_path") or fp32_path.replace(".onnx", "_int8.onnx")

    if not os.path.isfile(int8_path):
        print(f"  INT8 模型不存在: {int8_path}")
        return

    if not os.path.isfile(fp32_path):
        print(f"  FP32 模型不存在（已被删除），无法对比验证")
        print(f"  INT8 模型大小: {os.path.getsize(int8_path) / (1024 * 1024):.1f} MB")
        return

    cal_images = _collect_calibration_images(test_count)
    if not cal_images:
        print("  无可用测试图片")
        return

    from services.models_registry import load_model_config
    config = load_model_config(model_id)
    input_size = config.input_size if config.input_size else (224, 224)

    print(f"  加载 FP32 模型...")
    fp32_sess = ort.InferenceSession(fp32_path)
    print(f"  加载 INT8 模型...")
    int8_sess = ort.InferenceSession(int8_path)

    input_meta = fp32_sess.get_inputs()[0]
    input_name = input_meta.name

    top1_match = 0
    top5_match = 0
    fp32_times: list[float] = []
    int8_times: list[float] = []

    test_images = cal_images[:test_count]
    print(f"  测试 {len(test_images)} 张图片...")

    for img_path in test_images:
        arr = _preprocess_for_calibration(img_path, input_size)
        if arr is None:
            continue

        import time

        t0 = time.perf_counter()
        fp32_out = fp32_sess.run(None, {input_name: arr})
        fp32_times.append(time.perf_counter() - t0)

        t0 = time.perf_counter()
        int8_out = int8_sess.run(None, {input_name: arr})
        int8_times.append(time.perf_counter() - t0)

        fp32_scores = np.asarray(fp32_out[0]).flatten()
        int8_scores = np.asarray(int8_out[0]).flatten()

        fp32_top1 = int(np.argmax(fp32_scores))
        int8_top1 = int(np.argmax(int8_scores))
        if fp32_top1 == int8_top1:
            top1_match += 1

        fp32_top5 = set(np.argsort(fp32_scores)[::-1][:5])
        int8_top5 = set(np.argsort(int8_scores)[::-1][:5])
        if fp32_top5 == int8_top5:
            top5_match += 1

    total = len(test_images)
    if total == 0:
        print("  无有效测试结果")
        return

    fp32_avg = sum(fp32_times) / len(fp32_times) * 1000
    int8_avg = sum(int8_times) / len(int8_times) * 1000
    speedup = fp32_avg / int8_avg if int8_avg > 0 else float("inf")

    fp32_size_mb = os.path.getsize(fp32_path) / (1024 * 1024)
    int8_size_mb = os.path.getsize(int8_path) / (1024 * 1024)

    print(f"\n  结果:")
    print(f"    FP32 大小:   {fp32_size_mb:.1f} MB")
    print(f"    INT8 大小:   {int8_size_mb:.1f} MB")
    print(f"    体积缩减:    {(1 - int8_size_mb / fp32_size_mb) * 100:.1f}%")
    print(f"    FP32 推理:   {fp32_avg:.1f} ms")
    print(f"    INT8 推理:   {int8_avg:.1f} ms")
    print(f"    加速比:      {speedup:.2f}x")
    print(f"    Top-1 一致:  {top1_match}/{total} ({top1_match / total * 100:.1f}%)")
    print(f"    Top-5 一致:  {top5_match}/{total} ({top5_match / total * 100:.1f}%)")

    if top1_match / total >= 0.99:
        print(f"    精度验证:    通过 (Top-1 损失 < 1%)")
    elif top1_match / total >= 0.95:
        print(f"    精度验证:    可接受 (Top-1 损失 < 5%)")
    else:
        print(f"    精度验证:    警告 (Top-1 损失较大，建议检查)")


def list_models() -> None:
    print(f"\n已安装模型:")
    print(f"{'=' * 56}")

    installed = scan_installed_models()
    if not installed:
        print("  无已安装模型")
        return

    for m in installed:
        model_id = m["model_id"]
        int8_only = m.get("int8_only", False)
        has_int8 = m.get("has_int8", False)
        int8_path = m.get("int8_path", "")

        if int8_only:
            int8_size = os.path.getsize(int8_path) / (1024 * 1024)
            print(f"  {model_id}")
            print(f"    状态: INT8-only (FP32 已删除)")
            print(f"    INT8: {int8_size:.1f} MB")
        else:
            fp32_path = m["onnx_path"]
            fp32_size = os.path.getsize(fp32_path) / (1024 * 1024)
            print(f"  {model_id}")
            print(f"    FP32: {fp32_size:.1f} MB")
            if has_int8:
                int8_size = os.path.getsize(int8_path) / (1024 * 1024)
                print(f"    INT8: {int8_size:.1f} MB (缩减 {(1 - int8_size / fp32_size) * 100:.1f}%)")
            else:
                print(f"    INT8: 未量化")


def main() -> None:
    parser = argparse.ArgumentParser(description="ImageGallery ONNX INT8 量化工具")
    parser.add_argument("--list", action="store_true", help="列出已安装模型")
    parser.add_argument("--model", type=str, help="要量化的模型 ID")
    parser.add_argument("--all", action="store_true", help="量化所有已安装模型")
    parser.add_argument("--verify", action="store_true", help="验证量化精度")
    parser.add_argument("--calibration-count", type=int, default=300, help="校准图片数量 (默认 300)")

    args = parser.parse_args()

    if args.list:
        list_models()
        return

    if args.verify:
        if args.model:
            verify_quantization(args.model)
        else:
            installed = scan_installed_models()
            for m in installed:
                if m.get("has_int8"):
                    verify_quantization(m["model_id"])
        return

    if args.model:
        success = quantize_model_cli(args.model, args.calibration_count)
        if success and args.verify:
            verify_quantization(args.model)
        return

    if args.all:
        installed = scan_installed_models()
        for m in installed:
            if not m.get("int8_only"):
                quantize_model_cli(m["model_id"], args.calibration_count)
                if args.verify:
                    verify_quantization(m["model_id"])
        return

    parser.print_help()


if __name__ == "__main__":
    main()
