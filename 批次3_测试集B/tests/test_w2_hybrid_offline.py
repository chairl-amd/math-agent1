#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工单2：异构空响应、SymPy 先验与矩阵候选的离线回归测试。"""
from __future__ import annotations

import os
import struct
import sys
import types
from pathlib import Path
from unittest.mock import patch


BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "agent"))
os.environ.setdefault("DEEPSEEK_API_KEY", "offline-test-key")


def install_temporary_ctypes_compat() -> None:
    """当前便携运行时缺少 _ctypes 时，补足 SymPy 所需的最小定义。"""
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


def install_temporary_pillow_stub() -> None:
    """Pillow DLL 不可用时，仅提供导入 main.py 所需的最小模块。"""
    try:
        import PIL.Image  # noqa: F401
        return
    except Exception:
        for module_name in ("PIL", "PIL.Image", "PIL.ImageOps",
                            "PIL.ImageFilter"):
            sys.modules.pop(module_name, None)

        pillow_module = types.ModuleType("PIL")
        image_module = types.ModuleType("PIL.Image")
        image_ops_module = types.ModuleType("PIL.ImageOps")
        image_filter_module = types.ModuleType("PIL.ImageFilter")

        class FakeImage:
            pass

        image_module.Image = FakeImage
        image_module.open = lambda source: source
        image_module.LANCZOS = 1
        image_ops_module.invert = lambda image: image
        image_filter_module.MedianFilter = lambda size: None
        pillow_module.Image = image_module
        pillow_module.ImageOps = image_ops_module
        pillow_module.ImageFilter = image_filter_module
        sys.modules.update({
            "PIL": pillow_module,
            "PIL.Image": image_module,
            "PIL.ImageOps": image_ops_module,
            "PIL.ImageFilter": image_filter_module,
        })


install_temporary_pillow_stub()

import sympy as sp  # noqa: E402

import config  # noqa: E402
from main import _select_reference_candidate  # noqa: E402
from verify import CheckResult, SymPyVerifier, VerifierBase  # noqa: E402
from verifier_hetero import (  # noqa: E402
    DeepSeekVerifier,
    VerifierChain,
    _BREAKER,
    _read_reasoning_content,
)


PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    print(f"[{'PASS' if condition else 'FAIL'}] {name} {detail}")


def make_response(content: str, reasoning_content: str = "") -> dict:
    """构造 OpenAI 兼容的字典响应，避免发起真实网络请求。"""
    return {
        "choices": [{
            "message": {
                "content": content,
                "reasoning_content": reasoning_content,
            }
        }],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
        },
    }


class FakeCompletions:
    def __init__(self, responses: list[dict], calls: list[dict]) -> None:
        self.responses = responses
        self.calls = calls

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response_index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[response_index]


class FakeClient:
    def __init__(self, responses: list[dict], calls: list[dict]) -> None:
        self.chat = types.SimpleNamespace(
            completions=FakeCompletions(responses, calls))


def run_fake_verify(responses: list[dict]):
    """用假 openai 模块执行一次 HeteroVerifier.verify。"""
    calls = []
    fake_module = types.ModuleType("openai")

    def fake_openai(**kwargs):
        return FakeClient(responses, calls)

    fake_module.OpenAI = fake_openai
    with patch.dict(sys.modules, {"openai": fake_module}):
        verifier = DeepSeekVerifier(
            api_key="offline-test-key",
            model="offline-model",
            base_url="https://offline.invalid/v1",
            timeout=2,
        )
        result = verifier.verify("matrix", "matrix")
    return result, calls


class SymPyPriorStub(SymPyVerifier):
    name = "sympy"

    def verify(self, latex_expr: str, cpp_expr_or_code: str,
               context: dict | None = None) -> CheckResult:
        return CheckResult(
            True,
            "equivalent",
            latex_expr,
            cpp_expr_or_code,
            "规则证得等价",
            diff_points=[{"element": "c_ij", "reason": "索引完整"}],
            meta={
                "verifier": "sympy",
                "verdict_label": "HIGH_CONFIDENCE",
                "cross_representation_rule": "matmul_elementwise",
            },
        )


class RecordingVerifier(VerifierBase):
    name = "recording"

    def __init__(self) -> None:
        self.contexts = []

    def verify(self, latex_expr: str, cpp_expr_or_code: str,
               context: dict | None = None) -> CheckResult:
        self.contexts.append(context)
        return CheckResult(
            True,
            "equivalent",
            latex_expr,
            cpp_expr_or_code,
            "记录器收到先验",
            meta={"verifier": "recording"},
        )


def test_empty_response_paths() -> None:
    _BREAKER.reset()
    config.HETERO_MAX_TOKENS = 2048

    normal, normal_calls = run_fake_verify([
        make_response(
            '{"verdict":"not_equivalent","reason":"元素符号错误",'
            '"diff":"(0,2) 符号互换","index_check":"无关"}'
        )
    ])
    check(
        "a) 正文正常 JSON -> not_equivalent",
        normal.status == "not_equivalent"
        and normal.meta.get("empty_retry") is False
        and len(normal_calls) == 1,
        f"status={normal.status}, calls={len(normal_calls)}",
    )

    _BREAKER.reset()
    reasoning_json, reasoning_calls = run_fake_verify([
        make_response(
            "",
            '{"verdict":"not_equivalent","reason":"从推理字段恢复",'
            '"diff":"(0,2) 符号互换","index_check":"无关"}',
        )
    ])
    check(
        "b) 正文空、reasoning_content 含 JSON -> 恢复判定且不重试",
        reasoning_json.status == "not_equivalent"
        and reasoning_json.meta.get("empty_retry") is False
        and len(reasoning_calls) == 1,
        f"status={reasoning_json.status}, calls={len(reasoning_calls)}",
    )
    sdk_response = types.SimpleNamespace(
        choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(reasoning_content='{"verdict":"unknown"}'))]
    )
    check(
        "reasoning_content 读取兼容 SDK 对象",
        _read_reasoning_content(sdk_response) == '{"verdict":"unknown"}',
    )

    _BREAKER.reset()
    empty, empty_calls = run_fake_verify([
        make_response(None, ""),
        make_response("", ""),
    ])
    retry_prompt = empty_calls[-1]["messages"][0]["content"]
    check(
        "c) 正文和推理均空 -> 2 次强制 JSON 修复后 unknown",
        empty.status == "unknown"
        and empty.meta.get("empty_retry") is True
        and empty.meta.get("json_retries") == 2
        and empty.meta.get("repair_elapsed") is not None
        and len(empty_calls) == 3,
        f"status={empty.status}, calls={len(empty_calls)}",
    )
    check(
        "首次 2048、两次修复 8192 且提示只输出 JSON",
        empty_calls[0]["max_tokens"] == 2048
        and empty_calls[1]["max_tokens"] == 8192
        and empty_calls[2]["max_tokens"] == 8192
        and "只输出一个 JSON 对象" in retry_prompt
        and "禁止输出推理过程" in retry_prompt,
        f"tokens={[call['max_tokens'] for call in empty_calls]}",
    )

    _BREAKER.reset()
    retry_success, retry_success_calls = run_fake_verify([
        make_response("", ""),
        make_response(
            '{"verdict":"not_equivalent","reason":"重试后明确判定",'
            '"diff":"(0,2) 符号互换","index_check":"无关"}'
        ),
    ])
    check(
        "空正文第一次修复成功时记录 empty_retry=true",
        retry_success.status == "not_equivalent"
        and retry_success.meta.get("empty_retry") is True
        and retry_success.meta.get("json_retries") == 1
        and retry_success.meta.get("repair_elapsed") is not None
        and len(retry_success_calls) == 2,
        f"status={retry_success.status}, calls={len(retry_success_calls)}",
    )


def test_prompt_prior() -> None:
    verifier = DeepSeekVerifier(
        api_key="offline-test-key",
        model="offline-model",
        base_url="https://offline.invalid/v1",
        timeout=2,
    )
    context = {
        "sympy_verdict": {
            "status": "not_equivalent",
            "detail": "矩阵第(0,2)元素为 -s，应为 s",
            "verdict_label": "CONFIRMED_ERROR",
            "cross_representation_rule": "",
            "diff_points": [
                {"element": "(0,2)", "detail": "符号互换"},
                {"element": "(2,0)", "detail": "符号互换"},
            ],
        }
    }
    prompt = verifier._build_verify_prompt("matrix_a", "matrix_b", context)
    check(
        "提示词包含 SymPy 独立段与强先验约束",
        "<sympy_verdict>" in prompt
        and "status: not_equivalent" in prompt
        and "强先验" in prompt
        and "不得因为矩阵写法" in prompt
        and "assumptions" in prompt,
    )
    check(
        "提示词包含两个 few-shot JSON 示例",
        prompt.count('"verdict":"not_equivalent"') >= 1
        and prompt.count('"verdict":"equivalent"') >= 1
        and "第(0,2)/(2,0)元素符号互换" in prompt
        and "同一矩阵乘法定义" in prompt,
    )
    check("提示词长度保持在合理范围", len(prompt) < 6500, f"len={len(prompt)}")


def test_chain_prior_injection() -> None:
    original_context = {
        "cpp_code": "for (int i = 0; i < n; ++i) {}",
        "nested": {"keep": "unchanged"},
    }
    original_snapshot = {
        "cpp_code": original_context["cpp_code"],
        "nested": dict(original_context["nested"]),
    }

    serial_recorder = RecordingVerifier()
    serial_result = VerifierChain(
        [SymPyPriorStub(), serial_recorder], mode="serial"
    ).verify("matrix_a", "matrix_b", original_context)
    serial_prior = serial_recorder.contexts[0].get("sympy_verdict", {})
    check(
        "串行链向下游注入 SymPy 先验",
        serial_prior.get("status") == "equivalent"
        and serial_prior.get("verdict_label") == "HIGH_CONFIDENCE"
        and serial_prior.get("cross_representation_rule")
        == "matmul_elementwise"
        and len(serial_prior.get("diff_points", [])) == 1,
        str(serial_prior),
    )
    check(
        "串行链不修改调用方 context，且结果顺序不变",
        original_context == original_snapshot
        and [item["detail"] for item in serial_result.meta["evidence"]]
        == ["规则证得等价", "记录器收到先验"],
    )

    parallel_recorder = RecordingVerifier()
    parallel_result = VerifierChain(
        [SymPyPriorStub(), parallel_recorder], mode="parallel"
    ).verify("matrix_a", "matrix_b", original_context)
    parallel_prior = parallel_recorder.contexts[0].get("sympy_verdict", {})
    check(
        "并行链先同步注入 SymPy 先验再执行其余校验器",
        parallel_prior.get("status") == "equivalent"
        and parallel_prior.get("verdict_label") == "HIGH_CONFIDENCE",
        str(parallel_prior),
    )
    check(
        "并行链不修改调用方 context，且结果顺序不变",
        original_context == original_snapshot
        and [item["detail"] for item in parallel_result.meta["evidence"]]
        == ["规则证得等价", "记录器收到先验"],
    )


def test_matrix_candidate_selection() -> None:
    scalar_candidate = (
        sp.Symbol("c"),
        r"c=\cos\theta",
        "line:1",
    )
    matrix_candidate = (
        sp.Matrix([[1, 0], [0, 1]]),
        r"\begin{pmatrix}1&0\\0&1\end{pmatrix}",
        "matrix_block:0",
    )
    selected, fallbacks = _select_reference_candidate(
        sp.Matrix([[1, 0], [0, 1]]),
        [scalar_candidate, matrix_candidate],
    )
    check(
        "矩阵左侧优先选择矩阵候选",
        selected == [matrix_candidate] and fallbacks == [scalar_candidate],
        f"selected={selected}, fallbacks={fallbacks}",
    )

    no_matrix_selected, no_matrix_fallbacks = _select_reference_candidate(
        sp.Matrix([[1, 0], [0, 1]]), [scalar_candidate]
    )
    check(
        "无矩阵候选时保留标量 fallback 供整段校验",
        no_matrix_selected == [] and no_matrix_fallbacks == [scalar_candidate],
    )


def main() -> int:
    test_empty_response_paths()
    test_prompt_prior()
    test_chain_prior_injection()
    test_matrix_candidate_selection()
    print(f"\n== 通过 {len(PASS)} / 失败 {len(FAIL)} ==")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
