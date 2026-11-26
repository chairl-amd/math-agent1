#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_edge_cases.py — 边界测试（工单28）。

覆盖：
1. 空输入（空 LaTeX / 空表达式）
2. 超长公式（1000 项求和）
3. 非数学图片
4. 不存在的图片文件
5. 损坏的 LaTeX
6. 维度不匹配的矩阵比较
新增：空图片、非图片、超长公式、非 LaTeX 文本。
运行：python tests/test_edge_cases.py
"""
from __future__ import annotations

import os
import struct
import sys
import types
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "agent"))

# 新增：导入 config 需要 Key；测试只验证离线错误分支，不发起网络请求。
os.environ.setdefault("DEEPSEEK_API_KEY", "edge-case-test-key")
os.environ.setdefault("SYMPY_GROUND_TYPES", "python")


def install_temporary_pil_compat() -> str:
    """PIL 原生 DLL 不可用时，为错误路径测试安装最小兼容模块。"""
    try:
        from PIL import Image, ImageFilter, ImageOps  # noqa: F401
        return ""
    except Exception as exc:
        for module_name in list(sys.modules):
            if module_name == "PIL" or module_name.startswith("PIL."):
                del sys.modules[module_name]

        class UnavailableImage:
            pass

        def unavailable_open(*args, **kwargs):
            raise OSError(f"PIL unavailable: {type(exc).__name__}: {exc}")

        pil_package = types.ModuleType("PIL")
        pil_package.__path__ = []
        image_module = types.ModuleType("PIL.Image")
        image_module.Image = UnavailableImage
        image_module.open = unavailable_open
        image_module.LANCZOS = 1
        image_module.RGB = "RGB"
        ops_module = types.ModuleType("PIL.ImageOps")
        ops_module.invert = lambda image: image
        filter_module = types.ModuleType("PIL.ImageFilter")
        filter_module.MedianFilter = lambda size: None
        pil_package.Image = image_module
        pil_package.ImageOps = ops_module
        pil_package.ImageFilter = filter_module
        sys.modules["PIL"] = pil_package
        sys.modules["PIL.Image"] = image_module
        sys.modules["PIL.ImageOps"] = ops_module
        sys.modules["PIL.ImageFilter"] = filter_module
        return f"{type(exc).__name__}: {exc}"


PIL_COMPAT_ERROR = install_temporary_pil_compat()


# 新增：当前便携解释器的 _ctypes DLL 也可能加载失败。
def install_temporary_ctypes_compat() -> str:
    """_ctypes DLL 不可用时，提供 SymPy 导入所需的最小纯 Python 定义。"""
    try:
        import ctypes  # noqa: F401
        return ""
    except Exception as exc:
        sys.modules.pop("ctypes", None)
        ctypes_module = types.ModuleType("ctypes")
        ctypes_module.c_long = int
        ctypes_module.sizeof = lambda value: struct.calcsize("l")
        sys.modules["ctypes"] = ctypes_module
        return f"{type(exc).__name__}: {exc}"


CTYPES_COMPAT_ERROR = install_temporary_ctypes_compat()

from verify import (compare_expressions, latex_to_expr,  # noqa: E402
                    verify_latex_vs_code)
from ocr_pix import PixOcr  # noqa: E402
from ocr_vision import VisionOcr, looks_like_latex  # noqa: E402
import sympy as sp  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")


def main() -> int:
    # 1) 空输入
    r = verify_latex_vs_code("", "x")
    check("空 LaTeX -> unknown", r.status == "unknown", r.status)
    check("空 LaTeX 错误可判定", r.ok is False and r.detail.startswith("无法判定："),
          r.detail[:80])
    r = verify_latex_vs_code(r"\frac", "x")
    check("残缺 LaTeX 不崩溃", r.status in ("parse_error", "not_equivalent", "unknown"), r.status)

    # 2) 超长公式：sum_{i=1}^{1000} i 与闭式
    tex = r"\sum_{i=1}^{1000} i"
    r = verify_latex_vs_code(tex, "1000*1001/2")
    check("超长求和(1000项)闭合", r.ok, f"{r.status} {r.detail[:60]}")

    # 3) 非数学图片 -> OCR 层应返回 ok=False 而非抛异常
    try:
        ocr = PixOcr()
        r = ocr.recognize(BASE / "tests" / "formula" / "non_math.png")
        check("非数学图片不抛异常",
              isinstance(r.ok, bool) and (r.ok or bool(r.error_code)),
              f"ok={r.ok} code={r.error_code} err={r.error[:60]}")
    except Exception as e:
        check("非数学图片不抛异常", False, f"{type(e).__name__}: {e}")

    # 4) 不存在的文件
    try:
        ocr = PixOcr()
        r = ocr.recognize(BASE / "not_exist_xyz.png")
        check("缺失图片不抛异常",
              r.ok is False and bool(r.error_code),
              f"code={r.error_code} err={r.error[:80]}")
    except Exception as e:
        check("缺失图片不抛异常", False, f"{type(e).__name__}: {e}")

    # 5) 矩阵维度不匹配
    m1 = sp.Matrix([[1, 2], [3, 4]])
    m2 = sp.Matrix([[1, 2, 3], [4, 5, 6]])
    r = compare_expressions(m1, m2)
    check("矩阵维度不匹配 -> shape_mismatch", r.status == "shape_mismatch", r.status)

    # 6) 相同矩阵
    r = compare_expressions(sp.Matrix([[1, 2], [3, 4]]), sp.Matrix([[1, 2], [3, 4]]))
    check("相同矩阵等价", r.ok, r.status)

    # 新增 a) 空文件：Vision 无 Key 或编码失败都应返回结构化错误。
    tmp_dir = BASE / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    empty_image = tmp_dir / "empty.png"
    empty_image.write_bytes(b"")
    no_key_result = VisionOcr(api_key="").recognize(empty_image, retries=0)
    check("a 空文件无 Key -> 结构化错误",
          no_key_result.ok is False and bool(no_key_result.error_code),
          f"code={no_key_result.error_code}")
    empty_image_result = VisionOcr(api_key="edge-case-test-key").recognize(
        empty_image, retries=0)
    check("a 空文件 Vision -> 结构化错误",
          empty_image_result.ok is False and bool(empty_image_result.error_code),
          f"code={empty_image_result.error_code}")

    # 新增 b) 非图片：绕过模型加载，只验证 to_image 失败分支。
    not_image = tmp_dir / "not_image.txt"
    not_image.write_text("abc", encoding="utf-8")
    pix = PixOcr()
    with patch.object(pix, "_ensure_model", return_value=object()):
        not_image_result = pix.recognize(not_image, retries=0)
    check("b 非图片 -> image_unreadable",
          not_image_result.ok is False and
          not_image_result.error_code == "image_unreadable",
          f"code={not_image_result.error_code}")

    # 新增 c) 超长文本：只要求不抛异常且状态属于合法枚举。
    long_latex = ("x+" * 2500) + "1"
    long_result = verify_latex_vs_code(long_latex, "1")
    legal_statuses = {
        "equivalent", "numeric_equivalent", "not_equivalent", "parse_error",
        "shape_mismatch", "unknown", "error", "needs_human_review",
    }
    check("c 超长文本 -> 合法状态", long_result.status in legal_statuses,
          long_result.status)

    # 新增 d) LaTeX 特征识别。
    check("d 中文文本不是 LaTeX", looks_like_latex("今天天气真好") is False)
    check("d 数学文本是 LaTeX", looks_like_latex("x^2+1") is True)

    if PIL_COMPAT_ERROR or CTYPES_COMPAT_ERROR:
        compat_detail = "；".join(
            item for item in (PIL_COMPAT_ERROR, CTYPES_COMPAT_ERROR) if item)
        print(f"[INFO] 已启用临时原生扩展兼容层：{compat_detail[:160]}")
    print(f"\n== 通过 {len(PASS)} / 失败 {len(FAIL)} ==")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
