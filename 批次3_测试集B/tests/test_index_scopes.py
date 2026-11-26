#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""索引范围结构提取的单元测试。"""
from __future__ import annotations

import json
import struct
import sys
import types
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "agent"))


def install_temporary_ctypes_compat() -> str:
    """便携解释器缺少 _ctypes DLL 时，提供 SymPy 导入所需的最小定义。"""
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

from verify import extract_index_scopes  # noqa: E402


class IndexScopeExtractionTest(unittest.TestCase):
    """验证 LaTeX 求和与 C++ 循环的范围提取。"""

    def test_image_03_extracts_j_upper_bound(self) -> None:
        annotations = json.loads(
            (BASE / "tests" / "benchmark" / "annotations.json").read_text(
                encoding="utf-8")
        )
        case = next(
            item for item in annotations["cases"] if item["id"] == "image_03")
        cpp_code = (
            BASE / "tests" / "benchmark" / case["cpp"]
        ).read_text(encoding="utf-8")

        result = extract_index_scopes(case["latex"], cpp_code)

        self.assertTrue(any(
            item["upper"] == "j" for item in result["latex_sums"]))
        self.assertTrue(any(
            item["upper"] == "j" for item in result["cpp_loops"]))
        self.assertIn("U_{ij}", result["context"]["latex_line"])
        self.assertIn("k < j", result["context"]["cpp_line"])

    def test_latex_sum_variants(self) -> None:
        result = extract_index_scopes(
            r"\sum_{k=1}^{n-1} a_k + \sum_{k=1}^{j-1} b_k + \sum_k c_k",
            "",
        )

        sums = result["latex_sums"]
        self.assertEqual(len(sums), 3)
        self.assertEqual(
            (sums[0]["var"], sums[0]["lower"], sums[0]["upper"]),
            ("k", "1", "n-1"),
        )
        self.assertEqual(
            (sums[1]["var"], sums[1]["lower"], sums[1]["upper"]),
            ("k", "1", "j-1"),
        )
        self.assertEqual((sums[2]["var"], sums[2]["lower"]), ("k", ""))

    def test_cpp_loop_variants(self) -> None:
        cpp_code = r"""
        for (std::size_t k = 0; k < j; ++k) { }
        for (k = 0; k <= n; k++) { }
        for (int k = 0; k != 0; k += 1) { }
        """
        result = extract_index_scopes("", cpp_code)

        loops = result["cpp_loops"]
        self.assertEqual(len(loops), 3)
        self.assertEqual(
            (loops[0]["var"], loops[0]["lower"], loops[0]["op"],
             loops[0]["upper"]),
            ("k", "0", "<", "j"),
        )
        self.assertEqual((loops[1]["op"], loops[1]["upper"]), ("<=", "n"))
        self.assertEqual((loops[2]["op"], loops[2]["upper"]), ("!=", "0"))

    def test_empty_and_garbage_inputs_do_not_raise(self) -> None:
        for latex_text, cpp_code in (
            ("", ""),
            ("not latex {{{", "not c++ for (;"),
            ("\x00\x01乱码", "\x00\x02garbage"),
        ):
            with self.subTest(latex_text=latex_text, cpp_code=cpp_code):
                result = extract_index_scopes(latex_text, cpp_code)
                self.assertEqual(result["latex_sums"], [])
                self.assertEqual(result["cpp_loops"], [])


if __name__ == "__main__":
    if CTYPES_COMPAT_ERROR:
        print(f"[INFO] 已启用临时 ctypes 兼容层：{CTYPES_COMPAT_ERROR[:160]}")
    unittest.main(verbosity=2)
