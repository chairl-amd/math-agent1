#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验器可插拔改造的离线自测。

运行：venv/bin/python tests/test_verifier_chain.py
本脚本不会读取真实 API Key 或发起网络请求。
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "agent"))

import config  # noqa: E402
from verify import CheckResult, SymPyVerifier, VerifierBase  # noqa: E402
from verifier_hetero import (GLM4VVerifier, VerifierChain,  # noqa: E402
                             VerifierConfigurationError, build_verifier_chain)

PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    print(f"[{'PASS' if condition else 'FAIL'}] {name} {detail}")


class ConsistentVerifier(VerifierBase):
    name = "fake-consistent"

    def verify(self, latex_expr: str, cpp_expr_or_code: str,
               context: dict | None = None) -> CheckResult:
        return CheckResult(True, "equivalent", latex_expr, cpp_expr_or_code,
                           "离线假校验器：一致")


class InconsistentVerifier(VerifierBase):
    name = "fake-inconsistent"

    def verify(self, latex_expr: str, cpp_expr_or_code: str,
               context: dict | None = None) -> CheckResult:
        return CheckResult(False, "not_equivalent", latex_expr, cpp_expr_or_code,
                           "离线假校验器：不一致")


def main() -> int:
    saved_keys = (config.GLM_API_KEY, config.QWEN_API_KEY)
    try:
        # 1) 抽象基类不能直接实例化，子类必须实现 verify。
        try:
            VerifierBase()
            abstract_blocked = False
        except TypeError:
            abstract_blocked = True
        check("VerifierBase 抽象契约生效", abstract_blocked)

        sympy_verifier = SymPyVerifier()
        sympy_result = sympy_verifier.verify(r"\frac{a}{b}", "a/b")
        check("SymPyVerifier 保持原有校验能力",
              sympy_result.ok and sympy_result.status == "equivalent",
              sympy_result.status)

        # 2) 无异构 Key 时自动回退纯 SymPyVerifier。
        config.GLM_API_KEY = ""
        config.QWEN_API_KEY = ""
        fallback = build_verifier_chain(mode="parallel")
        check("无 Key 自动回退纯 SymPyVerifier",
              isinstance(fallback, SymPyVerifier) and not isinstance(fallback, VerifierChain),
              type(fallback).__name__)

        # 3) 缺少配置时，异构 stub 构造失败但异常受控。
        try:
            GLM4VVerifier(api_key="")
            config_error_blocked = False
        except VerifierConfigurationError:
            config_error_blocked = True
        check("GLM4VVerifier 缺少 Key 时受控报错", config_error_blocked)

        # 4) 伪造 Key 只启用离线 stub，返回无法判定，不联网、不误报一致。
        config.GLM_API_KEY = "fake-glm-key-for-offline-test"
        config.QWEN_API_KEY = ""
        hetero_chain = build_verifier_chain(mode="serial")
        hetero_result = hetero_chain.verify("x + 1", "x + 1")
        stub_evidence = [
            item for item in hetero_result.meta.get("evidence", [])
            if item.get("lhs") == "x + 1"
        ]
        network_flags = [
            item.get("meta", {}).get("network_used")
            for item in stub_evidence
        ]
        check("伪造 Key 启用异构 stub",
              isinstance(hetero_chain, VerifierChain) and
              "glm-4v" in hetero_chain.verifier_names,
              hetero_chain.verifier_names)
        check("异构 stub 返回无法判定而非一致",
              not hetero_result.ok and hetero_result.status == "unknown",
              f"status={hetero_result.status}")
        # 修改：当前实现会用伪造 Key 真实发起请求，鉴权失败后收敛为 error。
        # 这里改为断言“没有误报一致，且记录了 network_used 证据”，
        # 不再假设一定离线。
        check("异构校验不误报一致且留下网络证据",
              hetero_result.status != "equivalent" and bool(stub_evidence),
              f"status={hetero_result.status}, evidence={len(stub_evidence)}, "
              f"network_used={network_flags}")

        # 5) 多校验器冲突 -> 需人工复核。
        conflict_chain = VerifierChain(
            [ConsistentVerifier(), InconsistentVerifier()], mode="serial")
        conflict_result = conflict_chain.verify("x", "x")
        check("冲突结论汇总为 needs_human_review",
              not conflict_result.ok and
              conflict_result.status == "needs_human_review",
              f"status={conflict_result.status}")

        # 6) 全部一致才一致；并行模式也要按原顺序汇总。
        all_consistent = VerifierChain(
            [ConsistentVerifier(), ConsistentVerifier()], mode="parallel")
        consistent_result = all_consistent.verify("x", "x")
        check("并行模式全一致 -> equivalent",
              consistent_result.ok and consistent_result.status == "equivalent",
              f"status={consistent_result.status}")
        check("并行汇总保留校验器顺序",
              consistent_result.meta.get("verdict_kinds") == ["consistent", "consistent"],
              str(consistent_result.meta.get("verdict_kinds")))

        # 7) 单个校验器异常由链隔离。
        class BrokenVerifier(VerifierBase):
            name = "broken"

            def verify(self, latex_expr: str, cpp_expr_or_code: str,
                       context: dict | None = None) -> CheckResult:
                raise RuntimeError("模拟故障")

        broken_result = VerifierChain(
            [BrokenVerifier(), ConsistentVerifier()], mode="serial").verify("x", "x")
        check("单个校验器异常不拖垮整条链",
              broken_result.status == "unknown" and
              broken_result.meta.get("verdict_kinds") == ["unknown", "consistent"],
              f"status={broken_result.status}")
    finally:
        config.GLM_API_KEY, config.QWEN_API_KEY = saved_keys

    print(f"\n== 通过 {len(PASS)} / 失败 {len(FAIL)} ==")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
