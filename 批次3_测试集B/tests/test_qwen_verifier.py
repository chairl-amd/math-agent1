#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工单3 回归测试：Qwen-VL 正式接入校验链（全离线，用替身 SDK）。"""
from __future__ import annotations

import importlib
import json
import os
import sys
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "agent"))

PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    print(f"[{'PASS' if condition else 'FAIL'}] {name} {detail}")


# ---- 环境准备：config 在导入时读取环境变量 ----
os.environ["DEEPSEEK_API_KEY"] = "sk-test-deepseek"
os.environ.pop("QWEN_API_KEY", None)
os.environ.pop("QWEN_VERIFIER_MODEL", None)
os.environ.pop("QWEN_BASE_URL", None)
os.environ["DASHSCOPE_API_KEY"] = "sk-test-dashscope"

import config  # noqa: E402
importlib.reload(config)
import verifier_hetero  # noqa: E402
importlib.reload(verifier_hetero)

CLIENT_KWARGS: list[dict] = []
REQUEST_KWARGS: list[dict] = []


class _FakeCompletions:
    def __init__(self, payload: dict):
        self.payload = payload

    def create(self, **kwargs):
        REQUEST_KWARGS.append(kwargs)
        return {
            "choices": [{
                "message": {
                    "content": json.dumps(self.payload, ensure_ascii=False),
                },
            }],
            "usage": {
                "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
            },
        }


class _FakeClient:
    def __init__(self, payload: dict):
        self.chat = types.SimpleNamespace(
            completions=_FakeCompletions(payload))


def install_fake_openai(payload: dict) -> None:
    module = types.ModuleType("openai")

    def _openai(**kwargs):
        CLIENT_KWARGS.append(kwargs)
        return _FakeClient(payload)

    module.OpenAI = _openai
    module.APITimeoutError = type("APITimeoutError", (Exception,), {})
    sys.modules["openai"] = module


def main() -> int:
    print("== 配置解析 ==")
    check("Qwen Key 兼容 DASHSCOPE_API_KEY",
          config.QWEN_API_KEY == "sk-test-dashscope", config.QWEN_API_KEY)
    check("默认模型为 qwen3-vl-plus",
          config.QWEN_VERIFIER_MODEL == "qwen3-vl-plus",
          config.QWEN_VERIFIER_MODEL)

    print("== 校验器构造 ==")
    verifier = verifier_hetero.QwenVLVerifier()
    check("构造出 qwen-vl 校验器",
          verifier.name == "qwen-vl" and verifier.model == "qwen3-vl-plus",
          f"{verifier.name}/{verifier.model}")
    check("base_url 指向 DashScope 兼容地址",
          verifier.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1",
          verifier.base_url)

    print("== 在线判定（替身 SDK）==")
    # 熔断状态与网络无关，测试时强制关闭，避免历史状态干扰。
    verifier_hetero._BREAKER.is_open = lambda: False
    install_fake_openai({
        "verdict": "not_equivalent",
        "reason": "求和上界不同，索引收缩范围不一致",
        "diff": "LaTeX: k<n；代码: k<n-1",
    })
    result = verifier.verify(
        r"c_{ij}=\sum_{k=0}^{n-1}a_{ik}b_{kj}",
        "for k in range(n-1) c[i][j] += a[i][k]*b[k][j]",
    )
    check("返回 not_equivalent 结论",
          (not result.ok) and result.status == "not_equivalent",
          f"ok={result.ok}, status={result.status}")
    check("结论带 qwen-vl 出处与网络标记",
          result.meta.get("verifier") == "qwen-vl"
          and result.meta.get("provider") == "Qwen-VL"
          and result.meta.get("network_used") is True,
          str({k: result.meta.get(k) for k in ("verifier", "provider")}))
    check("请求使用 qwen3-vl-plus 模型",
          bool(REQUEST_KWARGS) and REQUEST_KWARGS[-1].get("model")
          == "qwen3-vl-plus",
          str(REQUEST_KWARGS[-1].get("model") if REQUEST_KWARGS else None))
    check("客户端使用 DashScope Key 与地址",
          bool(CLIENT_KWARGS)
          and CLIENT_KWARGS[-1].get("api_key") == "sk-test-dashscope"
          and CLIENT_KWARGS[-1].get("base_url")
          == "https://dashscope.aliyuncs.com/compatible-mode/v1",
          str({k: CLIENT_KWARGS[-1].get(k) for k in ("base_url",)}))
    check("证据里不含明文 Key",
          "sk-test-dashscope" not in json.dumps(result.to_dict(),
                                                ensure_ascii=False))

    install_fake_openai({
        "verdict": "equivalent",
        "reason": "两侧均为 k=0..n-1 的完整收缩",
        "diff": "",
    })
    equivalent = verifier.verify(
        r"c_{ij}=\sum_{k=0}^{n-1}a_{ik}b_{kj}",
        "for k in range(n) c[i][j] += a[i][k]*b[k][j]",
    )
    check("返回 equivalent 结论", equivalent.ok,
          f"ok={equivalent.ok}, status={equivalent.status}")

    print("== 接入构建链 ==")
    chain = verifier_hetero.build_verifier_chain(
        mode="serial", include_deepseek=False)
    names = chain.verifier_names if hasattr(chain, "verifier_names") else []
    check("默认链包含 qwen-vl", "qwen-vl" in names, str(names))
    check("默认链仍以 sympy 为第一判定", names[:1] == ["sympy"], str(names))

    chain_without = verifier_hetero.build_verifier_chain(
        mode="serial", include_deepseek=False, include_qwen=False)
    names_without = (chain_without.verifier_names
                     if hasattr(chain_without, "verifier_names") else [])
    check("include_qwen=False 可关闭", "qwen-vl" not in names_without,
          str(names_without))

    print("== 缺少 Key 时的行为 ==")
    os.environ.pop("DASHSCOPE_API_KEY", None)
    os.environ.pop("QWEN_API_KEY", None)
    importlib.reload(config)
    try:
        verifier_hetero.QwenVLVerifier()
        configured = True
    except verifier_hetero.VerifierConfigurationError:
        configured = False
    check("无 Key 时拒绝构造并回退", configured is False)
    chain_none = verifier_hetero.build_verifier_chain(
        mode="serial", include_deepseek=False)
    check("无 Key 时链退化为纯 SymPy",
          not hasattr(chain_none, "verifier_names")
          or chain_none.verifier_names == ["sympy"],
          str(getattr(chain_none, "verifier_names", "sympy-only")))

    print(f"\n== 通过 {len(PASS)} / 失败 {len(FAIL)} ==")
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
