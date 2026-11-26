#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工单1 回归测试：C++ 参考公式的表示归一化（全离线）。"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "agent"))

import ref_normalize as rn  # noqa: E402

PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    print(f"[{'PASS' if condition else 'FAIL'}] {name} {detail}")


def test_split_lines() -> None:
    print("== 行拆分与去噪 ==")
    text = "\\[\nZ=XY,\\quad z_{ij}=\\sum_{k}x_{ik}y_{kj}\n\\]\n\\[\nE=(((AB)C)D)\n\\]"
    lines = rn.split_math_lines(text)
    check("去掉 \\[ \\] 包裹", len(lines) == 2, str(lines))
    check("保留公式正文", lines[0].startswith("Z=XY"), lines[0][:40])


def test_expand_intermediate() -> None:
    print("== 中间变量代入 ==")
    softmax = (
        "T=\\sum_{j=1}^{n}\\exp(z_j)\n"
        "s_i=\\frac{\\exp(z_i)}{T},\\quad i=1,\\dots,n"
    )
    expanded = rn.expand_intermediate_variables(softmax)
    check("定义行保留", "T=\\sum" in expanded, expanded.splitlines()[0][:40])
    check("引用被展开为求和",
          "\\frac{\\exp(z_i)}{{\\sum_{j=1}^{n}\\exp(z_j)}}" in expanded,
          expanded.splitlines()[1][:80])
    check("不再残留孤立 T", "}{T}" not in expanded)

    # 两边都有语义的等式不应被代入（A=LU 这类）。
    keep = "A=LU\nL_{ii}=1\nU_{ij}=A_{ij}"
    check("非单符号定义不代入",
          rn.expand_intermediate_variables(keep).count("A=LU") == 1,
          rn.expand_intermediate_variables(keep).replace("\n", " | ")[:60])

    # 纯符号右端不代入（避免 T=Z 这种简单改名导致误展开）。
    plain = "T=Z\ns=T+1"
    check("右端为纯符号时不代入",
          rn.expand_intermediate_variables(plain) == plain, plain)


def test_strip_signatures() -> None:
    print("== 函数签名剥离 ==")
    chain4 = "\\operatorname{chain4}(A,B,C,D)=(((AB)C)D)"
    check("剥离 chain4 签名",
          rn.strip_function_signatures(chain4) == "(((AB)C)D)",
          rn.strip_function_signatures(chain4))

    chain3 = "\\operatorname{chain3}(a,b,c)=\\operatorname{mul}(\\operatorname{mul}(a,b),c)=(ab)c"
    check("链式等式取最终右端",
          rn.strip_function_signatures(chain3) == "(ab)c",
          rn.strip_function_signatures(chain3))

    indexed = (
        "\\operatorname{chain3}(a,b,c)_{i,j}="
        "\\sum_{\\ell=0}^{n-1}\\left(\\sum_{k=0}^{n-1}a_{i,k}b_{k,\\ell}\\right)c_{\\ell,j}"
    )
    stripped = rn.strip_function_signatures(indexed)
    check("带下标的签名也能剥离",
          stripped.startswith("\\sum_{\\ell"), stripped[:50])
    check("剥离后不含 operatorname", "\\operatorname" not in stripped)

    # 普通等式不受影响。
    normal = "C=AB"
    check("普通等式保持不变",
          rn.strip_function_signatures(normal) == normal)


def test_right_sides() -> None:
    print("== 右端提取 ==")
    text = (
        "D = ABC\n"
        "d_{ij} = \\sum_{k}\\sum_{l} a_{ik} b_{kl} c_{lj}"
    )
    pairs = dict(rn.right_sides(text))
    check("提取两行右端", len(pairs) == 2, str(list(pairs.values()))[:80])
    check("第二行右端正确",
          pairs.get(2, "").startswith("\\sum_{k}"), pairs.get(2, "")[:40])
    check("多等号行跳过",
          rn.right_sides("a=b=c") == [], str(rn.right_sides("a=b=c")))


def test_prepare() -> None:
    print("== 一站式预处理 ==")
    text = (
        "\\operatorname{chain4}(A,B,C,D)=(((AB)C)D)"
        "\nZ=XY"
    )
    prepared = rn.prepare_reference(text)
    check("先剥签名再代入", prepared.splitlines()[0] == "(((AB)C)D)",
          prepared.replace("\n", " | ")[:80])


def main() -> int:
    test_split_lines()
    test_expand_intermediate()
    test_strip_signatures()
    test_right_sides()
    test_prepare()
    print()
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for name in FAIL:
        print(f"  - 失败：{name}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
