#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新增：不依赖 Pillow/网络的矩阵块提取纯文本单测。"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
MAIN_FILE = ROOT_DIR / "agent" / "main.py"


def load_extract_function() -> Any:
    """新增：仅执行 main.py 中提取函数及其常量，避开 OCR 原生依赖。"""
    source = MAIN_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MAIN_FILE))
    wanted_assignments = {
        "_MATRIX_BLOCK_ENVIRONMENTS",
        "_LATEX_ENV_TOKEN_PATTERN",
    }
    selected_nodes = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                node.targets if isinstance(node, ast.Assign)
                else [node.target]
            )
            names = {
                target.id for target in targets
                if isinstance(target, ast.Name)
            }
            if names & wanted_assignments:
                selected_nodes.append(node)
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "_extract_latex_math_blocks"
        ):
            selected_nodes.append(node)

    namespace: dict[str, Any] = {
        "re": re,
        "__builtins__": __builtins__,
    }
    module = ast.Module(body=selected_nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(MAIN_FILE), "exec"), namespace)
    return namespace["_extract_latex_math_blocks"]


class ExtractLatexMathBlocksTest(unittest.TestCase):
    """验证典型矩阵、嵌套环境、多块和不完整输入。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.extract = staticmethod(load_extract_function())

    def test_bmatrix_with_ocr_prefix(self) -> None:
        text = (
            r"R_y(\theta)\quad \mathrm{3D}\,Y "
            r"\begin{bmatrix}\cos\theta&0&\sin\theta\\"
            r"0&1&0\\-\sin\theta&0&\cos\theta\end{bmatrix}"
        )
        blocks = self.extract(text)
        self.assertEqual(len(blocks), 1)
        block_text, start, end = blocks[0]
        self.assertTrue(block_text.startswith(r"\begin{bmatrix}"))
        self.assertTrue(block_text.endswith(r"\end{bmatrix}"))
        self.assertEqual(text[start:end], block_text)

    def test_nested_array_stays_balanced(self) -> None:
        text = (
            r"\begin{bmatrix}"
            r"\begin{array}{cc}a&b\\c&d\end{array}&0\\"
            r"0&1\end{bmatrix}"
        )
        blocks = self.extract(text)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0][0], text)

    def test_pmatrix_and_multiple_blocks(self) -> None:
        text = (
            r"\begin{pmatrix}a&b\\c&d\end{pmatrix} + "
            r"\begin{array}{c}x\\y\end{array}"
        )
        blocks = self.extract(text)
        self.assertEqual(len(blocks), 2)
        self.assertIn(r"\begin{pmatrix}", blocks[0][0])
        self.assertIn(r"\begin{array}", blocks[1][0])

    def test_non_matrix_environment_is_ignored(self) -> None:
        text = r"\begin{aligned}a&=b\\c&=d\end{aligned}"
        self.assertEqual(self.extract(text), [])

    def test_unclosed_matrix_is_ignored(self) -> None:
        text = r"prefix \begin{bmatrix}a&b\\c&d"
        self.assertEqual(self.extract(text), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
