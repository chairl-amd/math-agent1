#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""矩阵解析单元测试，可直接用 Python 或 pytest 运行。"""
from __future__ import annotations

import struct
import sys
import types
import unittest
from pathlib import Path


BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "agent"))


def install_temporary_ctypes_compat() -> None:
    """当前便携运行时 _ctypes 不可用时，提供 SymPy 所需的最小定义。"""
    try:
        import ctypes  # noqa: F401
        return
    except Exception:
        sys.modules.pop("ctypes", None)
        ctypes_module = types.ModuleType("ctypes")
        ctypes_module.c_long = int
        ctypes_module.sizeof = lambda value: struct.calcsize("l")
        sys.modules["ctypes"] = ctypes_module


install_temporary_ctypes_compat()

import sympy as sp  # noqa: E402

from verify import latex_to_expr  # noqa: E402


class MatrixParseTest(unittest.TestCase):
    def test_rotation_bmatrix(self) -> None:
        theta = sp.Symbol("theta")
        tex = (
            r"R_y(\theta)=\begin{bmatrix}"
            r"\cos\theta&0&\sin\theta\\"
            r"0&1&0\\"
            r"-\sin\theta&0&\cos\theta"
            r"\end{bmatrix}"
        )
        expr, error = latex_to_expr(tex)
        expected = sp.Matrix([
            [sp.cos(theta), 0, sp.sin(theta)],
            [0, 1, 0],
            [-sp.sin(theta), 0, sp.cos(theta)],
        ])
        self.assertEqual(error, "")
        self.assertIsInstance(expr, sp.MatrixBase)
        self.assertEqual(expr.shape, (3, 3))
        for row in range(3):
            for column in range(3):
                self.assertEqual(
                    sp.simplify(expr[row, column] - expected[row, column]), 0)

    def test_pmatrix(self) -> None:
        expr, error = latex_to_expr(
            r"\begin{pmatrix}1&x\\0&2\end{pmatrix}")
        self.assertEqual(error, "")
        self.assertEqual(expr, sp.Matrix([[1, sp.Symbol("x")], [0, 2]]))

    def test_array_with_column_format(self) -> None:
        expr, error = latex_to_expr(
            r"\begin{array}{ccc}1&2&3\\4&5&6\end{array}")
        self.assertEqual(error, "")
        self.assertEqual(expr, sp.Matrix([[1, 2, 3], [4, 5, 6]]))

    def test_plain_2x2_matrix(self) -> None:
        expr, error = latex_to_expr(
            r"\begin{matrix}a&b\\c&d\end{matrix}")
        symbols = sp.symbols("a b c d")
        self.assertEqual(error, "")
        self.assertEqual(
            expr,
            sp.Matrix([[symbols[0], symbols[1]],
                       [symbols[2], symbols[3]]]),
        )

    def test_supported_delimiters(self) -> None:
        for environment in ("matrix", "vmatrix", "Bmatrix"):
            with self.subTest(environment=environment):
                expr, error = latex_to_expr(
                    rf"\begin{{{environment}}}1&2\\3&4\end{{{environment}}}")
                self.assertEqual(error, "")
                self.assertEqual(expr, sp.Matrix([[1, 2], [3, 4]]))

    def test_invalid_cell_returns_none(self) -> None:
        try:
            expr, _ = latex_to_expr(
                r"\begin{bmatrix}1&@@@\\3&4\end{bmatrix}")
        except Exception as exc:
            self.fail(f"非法矩阵不应抛异常：{type(exc).__name__}: {exc}")
        self.assertIsNone(expr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
