#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工单1/4（w11）离线回归：可解释批改层（不联网）。

覆盖：
1. 只在 not_equivalent / CONFIRMED_ERROR 上生成解释；
2. 字段齐全、类型正确、分类别名归一化、置信度裁剪；
3. PDE 与低置信度强制人工复核；
4. 模型失败时走确定性规则兜底（仍输出合法 JSON）；
5. 持久化缓存命中不重复调用模型；
6. EXPLAIN_ENABLED 关闭时 Pipeline 报告与改动前一致（无 explain 字段）；
7. 解释层不修改任何判定字段。
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("DEEPSEEK_API_KEY", "offline-test-key")
os.environ["MATH_AGENT_CACHE"] = "0"

from agent import cache as agent_cache  # noqa: E402
from agent import config, explain  # noqa: E402
from agent.main import Pipeline  # noqa: E402

PASS, FAIL = [], []
REQUIRED_FIELDS = {
    "error_type", "location", "suggestion", "confidence",
    "needs_human_review",
}


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    print(f"[{'PASS' if condition else 'FAIL'}] {name} {detail}")


def make_check(**overrides) -> dict:
    payload = {
        "name": "latex_vs_cpp",
        "ok": False,
        "status": "not_equivalent",
        "lhs": "eta*g",
        "rhs": "-eta*g",
        "detail": "所有可判定校验器均认为不一致",
        "diff_points": [
            {"point": {"eta": 1.0, "g": 2.0}, "lhs": "2.0", "rhs": "-2.0",
             "rel_delta": 2.0},
        ],
        "meta": {
            "verdict_label": "CONFIRMED_ERROR",
            "hetero_verdicts": [{
                "status": "not_equivalent",
                "detail": "公式是减号，代码是加号，符号完全相反",
                "meta": {"verifier": "qwen-vl", "index_check": "无循环"},
            }],
        },
    }
    payload.update(overrides)
    return payload


def fake_model(payload: dict):
    """记录调用次数的确定性替身。"""
    calls = {"count": 0}

    def call(prompt: str):
        calls["count"] += 1
        assert "error_type_taxonomy" in prompt or "error_type" in prompt
        return dict(payload), {
            "model": "mock-model", "attempts": 1, "elapsed": 0.01,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                      "total_tokens": 15},
        }

    return call, calls


def test_gating() -> None:
    print("== 生成条件 ==")
    equivalent = make_check(status="equivalent", ok=True)
    check("等价结论不生成解释",
          explain.explain_check(equivalent, latex_expr="x", model_call=None) is None)
    unknown = make_check(status="unknown", meta={"verdict_label": "UNKNOWN"})
    check("unknown 默认不生成解释",
          explain.explain_check(unknown, latex_expr="x") is None)
    check("unknown + force 可显式生成（用于质量验收）",
          explain.explain_check(
              unknown, latex_expr="x", force=True,
              model_call=fake_model({"error_type": "unknown"})[0]) is not None)
    conflict = make_check(status="needs_human_review",
                          meta={"verdict_label": "CONFLICT"})
    check("冲突结论不生成解释", explain.explain_check(conflict) is None)


def test_schema_and_alias() -> None:
    print("== 字段与分类归一化 ==")
    caller, calls = fake_model({
        "error_type": "符号错误",  # 中文别名 -> sign_error
        "location": "L6: w[i] += eta * g[i];",
        "suggestion": "改为 w[i] -= eta * g[i];",
        "confidence": 1.7,  # 越界 -> 裁剪到 1.0
        "needs_human_review": False,
    })
    result = explain.explain_check(
        make_check(), latex_expr=r"w_{t+1}=w_t-\eta g_t",
        cpp_code="for (i) w[i] += eta * g[i];", model_call=caller)
    check("返回字典", isinstance(result, dict))
    check("必需字段齐全", REQUIRED_FIELDS <= set(result),
          str(sorted(result)))
    check("中文别名归一化为 sign_error", result["error_type"] == "sign_error",
          result.get("error_type"))
    check("置信度裁剪到 [0,1]", result["confidence"] == 1.0,
          result.get("confidence"))
    check("置信度是数值", isinstance(result["confidence"], float))
    check("needs_human_review 是布尔",
          isinstance(result["needs_human_review"], bool))
    check("location 保留行号", "L6" in result["location"])
    check("suggestion 保留代码", "-=" in result["suggestion"])
    check("模型只被调用一次", calls["count"] == 1)

    bad_type, _ = fake_model({
        "error_type": "完全不存在的类型", "location": "", "suggestion": "",
        "confidence": 0.3, "needs_human_review": False,
    })
    unknown_result = explain.explain_check(
        make_check(), latex_expr="x", model_call=bad_type)
    check("未知分类回落为 unknown",
          unknown_result["error_type"] == "unknown")
    check("未知分类强制人工复核",
          unknown_result["needs_human_review"] is True)
    check("空定位给出占位说明", "未能精确定位" in unknown_result["location"])


def test_pde_and_fallback() -> None:
    print("== PDE 与规则兜底 ==")
    pde_check = make_check(
        lhs=r"\partial_t u", rhs=r"u^{n+1}_i")
    caller, _ = fake_model({
        "error_type": "pde_capability", "location": "L3",
        "suggestion": "核对离散格式", "confidence": 0.9,
        "needs_human_review": False,
    })
    pde_result = explain.explain_check(
        pde_check, latex_expr=r"\frac{\partial u}{\partial t}=\nabla^2 u",
        cpp_code="u[i] = u[i] + dt * lap(u);", model_call=caller)
    check("PDE 识别并标记能力边界", pde_result["pde_boundary"] is True)
    check("PDE 强制人工复核", pde_result["needs_human_review"] is True)

    # 模型失败（返回 None）时，符号相反证据应触发 sign_error 规则兜底。
    failed_result = explain.explain_check(
        make_check(), latex_expr=r"w_{t+1}=w_t-\eta g_t",
        cpp_code="w = w + eta * g;", model_call=lambda prompt: (None, {}))
    check("模型失败仍有合法 JSON", isinstance(failed_result, dict))
    check("兜底来源标记为 rule", failed_result["source"] == "rule")
    check("符号相反证据兜底为 sign_error",
          failed_result["error_type"] == "sign_error",
          failed_result.get("error_type"))
    check("兜底一律要求人工复核",
          failed_result["needs_human_review"] is True)


def test_cache() -> None:
    print("== 解释缓存 ==")
    # 注意：不使用 tempfile.mkdtemp 新建子目录——本机沙箱下新建目录内的
    # 文件创建会被阻塞；沿用 tests/test_cache.py 的做法，直接复用 .tmp。
    temp_dir = BASE / ".tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    original_dir = agent_cache.CACHE_DIR
    original_enabled = config.EXPLAIN_CACHE_ENABLED
    original_cache_flag = os.environ.get("MATH_AGENT_CACHE")
    try:
        agent_cache.CACHE_DIR = temp_dir
        config.EXPLAIN_CACHE_ENABLED = True
        os.environ["MATH_AGENT_CACHE"] = "1"
        agent_cache.clear()
        caller, calls = fake_model({
            "error_type": "sign_error", "location": "L6",
            "suggestion": "改符号", "confidence": 0.9,
            "needs_human_review": False,
        })
        first = explain.explain_check(
            make_check(), latex_expr="cache-latex", cpp_code="cache-code",
            model_call=caller)
        second = explain.explain_check(
            make_check(), latex_expr="cache-latex", cpp_code="cache-code",
            model_call=caller)
        check("首次为 miss", first.get("source") == "llm" and
              first.get("cache") == "miss")
        check("二次命中缓存", second.get("source") == "cache" and
              second.get("cache") == "hit")
        check("缓存命中不重复调用模型", calls["count"] == 1,
              f"calls={calls['count']}")
        check("缓存结果字段一致",
              second["error_type"] == first["error_type"] and
              second["location"] == first["location"])
    finally:
        agent_cache.CACHE_DIR = original_dir
        config.EXPLAIN_CACHE_ENABLED = original_enabled
        if original_cache_flag is None:
            os.environ.pop("MATH_AGENT_CACHE", None)
        else:
            os.environ["MATH_AGENT_CACHE"] = original_cache_flag
        # 只清理本测试写入的解释缓存，绝不删除 .tmp 目录本身。
        try:
            (temp_dir / "explain.pkl").unlink(missing_ok=True)
        except OSError:
            pass


def test_pipeline_integration() -> None:
    print("== Pipeline 集成（不改变判定） ==")
    original_enabled = config.EXPLAIN_ENABLED
    original_caller = explain._default_model_call
    try:
        caller, calls = fake_model({
            "error_type": "coefficient_error", "location": "L1: x + 2",
            "suggestion": "改成 x + 1", "confidence": 0.9,
            "needs_human_review": False,
        })
        explain._default_model_call = caller
        config.EXPLAIN_ENABLED = True
        report = Pipeline(engine="auto", use_llm=False,
                          verifier_mode="sympy-only").run(
            latex="x + 1", expect="x + 2")
        check_dict = report["steps"]["checks"][0]
        check("开启时报告含 explain", "explain" in check_dict)
        check("判定字段未被改写", check_dict["status"] == "not_equivalent"
              and report["ok"] is False)
        check("解释内容来自注入的模型",
              check_dict["explain"].get("error_type") == "coefficient_error")
        check("集成路径调用一次模型", calls["count"] == 1)

        config.EXPLAIN_ENABLED = False
        before_calls = calls["count"]
        report_off = Pipeline(engine="auto", use_llm=False,
                              verifier_mode="sympy-only").run(
            latex="x + 1", expect="x + 2")
        check_off = report_off["steps"]["checks"][0]
        check("关闭时报告无 explain 字段", "explain" not in check_off)
        check("关闭时零模型调用", calls["count"] == before_calls)
        check("关闭前后判定一致",
              check_off["status"] == check_dict["status"] and
              report_off["ok"] == report["ok"])
        check("关闭时报告键集合与改动前一致",
              set(check_off) == {"name", "ok", "status", "lhs", "rhs",
                                 "detail", "diff_points", "meta"},
              str(sorted(check_off)))
    finally:
        config.EXPLAIN_ENABLED = original_enabled
        explain._default_model_call = original_caller


def test_attach_and_summary() -> None:
    print("== 报告挂载与摘要 ==")
    payload = {
        "error_type": "transpose_error", "location": "L9: a[j][i]",
        "suggestion": "改成 a[i][j]", "confidence": 0.8,
        "needs_human_review": False,
    }
    caller, _ = fake_model(payload)
    report = {
        "ok": False,
        "steps": {"checks": [
            make_check(meta={"verdict_label": "CONFIRMED_ERROR"}),
            make_check(status="equivalent", ok=True,
                       meta={"verdict_label": "HIGH_CONFIDENCE"}),
        ]},
    }
    original = copy.deepcopy(report)
    produced = explain.attach_explanations(
        report, latex_expr="x", model_call=caller)
    check("只为错例生成解释", len(produced) == 1)
    check("等价结论不被添加 explain",
          "explain" not in report["steps"]["checks"][1])
    check("挂载不改变判定字段",
          report["steps"]["checks"][0]["status"] ==
          original["steps"]["checks"][0]["status"] and
          report["ok"] == original["ok"])
    summary = explain.explanation_summary(produced[0])
    check("摘要含类型/位置/置信度",
          "转置" in summary and "L9" in summary and "0.8" in summary,
          summary)
    check("空解释摘要为空串", explain.explanation_summary({}) == "")


def main() -> int:
    test_gating()
    test_schema_and_alias()
    test_pde_and_fallback()
    test_cache()
    test_pipeline_integration()
    test_attach_and_summary()
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
