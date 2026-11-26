#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工单3（w11）：用现有 50 例中的「预期 fail」错例验收解释质量。

方法（不重跑 OCR/翻译，避免结论漂移）：
1. 读取 tests/benchmark/annotations.json，筛出 expected == "fail" 的用例；
2. 复用 output/benchmark_w10_final 下 w10 全量运行的逐例报告作为**证据**，
   这样解释针对的是真实判定结论，而不是另造一份输入；
3. 对每条结论调用 agent/explain.py 生成结构化批改说明（真实模型）；
4. 把 explain.error_type 与 annotations.json 的 error_type 逐例对照。

两个口径都会输出，避免夸大：
- 严格一致：预测类型 == 标注类型（主指标）；
- 根因族一致：两者落在同一根因族（辅助指标，族定义见 ROOT_CAUSE_FAMILIES）。

用法（Key 只从环境变量注入）：
    $env:DEEPSEEK_API_KEY="..."; python scripts/eval_explain_quality.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

ANNOTATIONS = BASE / "tests" / "benchmark" / "annotations.json"
BENCHMARK_DIR = BASE / "output" / "benchmark_w10_final" / "_benchmark_tmp"
REPORT_MD = BASE / "output" / "explain_quality_report.md"
FAILURES_MD = BASE / "output" / "explain_failures.md"
RESULT_JSON = BASE / "output" / "explain_quality.json"

# 根因族：仅用于辅助口径。族的划分以「错误发生在哪一层/哪类修改能修好」为准。
ROOT_CAUSE_FAMILIES = {
    "boundary": {"index_scope", "off_by_one", "sum_bounds", "stride_error"},
    "transpose": {"transpose_error"},
    "sign_or_operator": {
        "sign_error", "coefficient_error", "missing_operation", "missing_term",
        "norm_error", "order_error", "operator_error",
    },
    "representation": {"variable_mapping", "notation_variant"},
    "capability": {
        "pde_capability", "symbolic_scope_limit", "parser_limitation",
    },
    "unknown": {"unknown"},
}


def family_of(error_type: str) -> str:
    for family, members in ROOT_CAUSE_FAMILIES.items():
        if error_type in members:
            return family
    return "unknown"


def load_annotations() -> list[dict]:
    payload = json.loads(ANNOTATIONS.read_text(encoding="utf-8"))
    return list(payload.get("cases") or [])


def find_report(case_id: str) -> Path | None:
    """取该用例最新一份逐例报告。"""
    candidates = sorted(
        BENCHMARK_DIR.glob(f"*/{case_id}/*.json"),
        key=lambda path: path.stat().st_mtime,
    )
    return candidates[-1] if candidates else None


def pick_check(report: dict) -> dict | None:
    checks = (report.get("steps") or {}).get("checks") or []
    for check in checks:
        if isinstance(check, dict) and check.get("name") == "latex_vs_cpp":
            return check
    for check in checks:
        if isinstance(check, dict):
            return check
    return None


def build_inputs(report: dict) -> tuple[str, str, str]:
    steps = report.get("steps") or {}
    latex = str((steps.get("latex") or {}).get("text") or "")
    ref_latex = str((steps.get("translate_cpp") or {}).get("ref_latex") or "")
    cpp_path = (report.get("inputs") or {}).get("cpp") or ""
    code = ""
    if cpp_path:
        path = Path(str(cpp_path))
        if path.is_file():
            code = path.read_text(encoding="utf-8", errors="replace")
    return latex, ref_latex, code


def evaluate(cases: list[dict], *, limit: int | None = None,
             only: str | None = None) -> dict:
    from agent import explain  # 延迟导入：确保 config 能读到环境变量

    targets = [case for case in cases if case.get("expected") == "fail"]
    if only:
        wanted = {item.strip() for item in str(only).split(",") if item.strip()}
        targets = [case for case in targets if case.get("id") in wanted]
    if limit:
        targets = targets[:limit]

    rows = []
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for index, case in enumerate(targets, start=1):
        case_id = str(case.get("id"))
        annotation_type = str(case.get("error_type") or "unknown")
        report_path = find_report(case_id)
        print(f"[{index}/{len(targets)}] {case_id} ...", flush=True)
        if report_path is None:
            rows.append({
                "case": case_id,
                "expected": annotation_type,
                "predicted": "no_report",
                "match": False,
                "family_match": False,
                "note": "未找到 w10 逐例报告",
            })
            continue
        report = json.loads(report_path.read_text(encoding="utf-8"))
        check = pick_check(report)
        if check is None:
            rows.append({
                "case": case_id,
                "expected": annotation_type,
                "predicted": "no_check",
                "match": False,
                "family_match": False,
                "note": "报告中无校验结论",
            })
            continue
        latex, ref_latex, code = build_inputs(report)
        forced = not explain.needs_explanation(check)
        explanation = explain.explain_check(
            check, latex_expr=latex, ref_latex=ref_latex, cpp_code=code,
            force=True)
        explanation = explanation or {}
        predicted = str(explanation.get("error_type") or "none")
        match = predicted == annotation_type
        family_match = family_of(predicted) == family_of(annotation_type)
        usage = explanation.get("usage") or {}
        if explanation.get("source") == "llm":
            for key in usage_total:
                usage_total[key] += int(usage.get(key) or 0)
        rows.append({
            "case": case_id,
            "expected": annotation_type,
            "predicted": predicted,
            "predicted_label": explanation.get("error_type_label"),
            "match": match,
            "family_match": family_match,
            "family": family_of(predicted),
            "expected_family": family_of(annotation_type),
            "confidence": explanation.get("confidence"),
            "needs_human_review": explanation.get("needs_human_review"),
            "source": explanation.get("source"),
            "status": check.get("status"),
            "forced": forced,
            "location": explanation.get("location"),
            "suggestion": explanation.get("suggestion"),
            "root_cause_layer": explanation.get("root_cause_layer"),
            "usage": usage,
            "report": str(report_path),
            "note": case.get("note") or "",
        })

    total = len(rows)
    strict_hits = sum(1 for row in rows if row.get("match"))
    family_hits = sum(1 for row in rows if row.get("family_match"))
    return {
        "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
        "model": getattr(__import__("agent.config", fromlist=["config"]),
                         "TEXT_MODEL", ""),
        "prompt_version": explain.EXPLAIN_PROMPT_VERSION,
        "total": total,
        "strict_hits": strict_hits,
        "family_hits": family_hits,
        "strict_rate": round(strict_hits / total, 4) if total else 0.0,
        "family_rate": round(family_hits / total, 4) if total else 0.0,
        "usage": usage_total,
        "rows": rows,
    }


def _md_cell(value) -> str:
    return str(value if value is not None else "-").replace("|", "\\|").replace(
        "\n", " ")


def write_report(result: dict) -> None:
    rows = result["rows"]
    total = result["total"]
    strict = result["strict_hits"]
    family = result["family_hits"]
    lines = [
        "# 可解释批改质量报告（w11 工单3）",
        "",
        f"- 生成时间：{result['generated_at']}",
        f"- 模型：`{result['model']}`；提示词版本：`{result['prompt_version']}`",
        f"- 样本：`annotations.json` 中 expected=fail 的全部用例，共 **{total}** 例",
        "- 证据来源：`output/benchmark_w10_final` 的逐例真实运行报告"
        "（不重新跑 OCR/翻译，避免结论漂移）",
        "",
        "## 1. 一致率",
        "",
        "| 口径 | 命中 | 一致率 |",
        "|---|---:|---:|",
        f"| 严格一致（预测类型 == 标注类型，主指标） | {strict}/{total} | "
        f"**{result['strict_rate'] * 100:.2f}%** |",
        f"| 根因族一致（辅助口径） | {family}/{total} | "
        f"{result['family_rate'] * 100:.2f}% |",
        "",
        "> 根因族定义：boundary={index_scope, off_by_one, sum_bounds, stride_error}；"
        "transpose={transpose_error}；sign_or_operator={sign_error, coefficient_error, "
        "missing_operation, missing_term, norm_error, order_error, operator_error}；"
        "representation={variable_mapping, notation_variant}；"
        "capability={pde_capability, symbolic_scope_limit, parser_limitation}。",
        "",
        "## 2. 逐例结果",
        "",
        "| # | 用例 | 标注根因 | 预测根因 | 严格 | 族 | 置信度 | 来源 | 结论状态 |",
        "|---:|---|---|---|---|---|---:|---|---|",
    ]
    for index, row in enumerate(rows, start=1):
        lines.append(
            f"| {index} | `{_md_cell(row.get('case'))}` | "
            f"`{_md_cell(row.get('expected'))}` | "
            f"`{_md_cell(row.get('predicted'))}` | "
            f"{'✅' if row.get('match') else '❌'} | "
            f"{'✅' if row.get('family_match') else '❌'} | "
            f"{_md_cell(row.get('confidence'))} | "
            f"{_md_cell(row.get('source'))} | "
            f"`{_md_cell(row.get('status'))}` |")

    good = [row for row in rows if row.get("match")][:3]
    bad = [row for row in rows if not row.get("match")]
    lines += ["", "## 3. 典型例子", ""]
    lines.append("### 3.1 命中的好例子")
    lines.append("")
    for row in good:
        lines += [
            f"**`{row['case']}`**（{row['expected']}，置信度 {row.get('confidence')}）",
            "",
            f"- 定位：`{_md_cell(row.get('location'))}`",
            "- 建议：",
            "",
            "```text",
            str(row.get("suggestion") or "")[:600],
            "```",
            "",
        ]
    lines.append("### 3.2 未命中例子")
    lines.append("")
    if not bad:
        lines.append("本轮全部严格一致。")
    for row in bad:
        lines += [
            f"**`{row['case']}`**：标注 `{row['expected']}` / 预测 "
            f"`{row['predicted']}`（根因族：{row['expected_family']} → "
            f"{row['family']}，置信度 {row.get('confidence')}）",
            "",
            f"- 定位：`{_md_cell(row.get('location'))}`",
            "",
        ]

    lines += [
        "## 4. 结论与局限",
        "",
        f"- 主指标 {strict}/{total}（{result['strict_rate'] * 100:.2f}%），"
        f"目标 80%：{'达标' if result['strict_rate'] >= 0.8 else '**未达标**'}。",
        "- 解释层只依据「参考公式 + 翻译结果 + C++ 源码 + SymPy 差异 + VLM 判定依据」"
        "推断根因，未读取 annotations.json 的 note 或 error_type，因此不存在把标准答案"
        "喂给模型的情况。",
        "- `pde_residual_scope` 在 w10 运行中的结论是 `unknown`（PDE 能力边界），"
        "按设计不会自动生成解释；本报告用 `force=True` 显式生成，并在逐例表中保留"
        "`forced` 信息，未改动任何判定结论。",
        "- PDE / 解析能力边界的用例（image_04、heat_1d、wave_1d、poisson_2d、"
        "ns_incompressible）本身没有可自动证明的代数错误，解释只能给出边界说明，"
        "与标注的 `parser_limitation` / `pde_capability` 存在天然口径差异。",
    ]
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_failures(result: dict) -> None:
    bad = [row for row in result["rows"] if not row.get("match")]
    lines = [
        "# 解释未命中明细（w11 工单3）",
        "",
        f"- 生成时间：{result['generated_at']}",
        f"- 未命中：{len(bad)}/{result['total']}",
        "- 说明：以下逐例列出「标注根因 vs 预测根因」及模型给出的完整解释，"
        "不做任何事后改写；是否属于能力边界由读者判断。",
        "",
    ]
    for row in bad:
        usage = row.get("usage") or {}
        lines += [
            f"## `{row['case']}`",
            "",
            f"- 标注根因：`{row['expected']}`（族：{row['expected_family']}）",
            f"- 预测根因：`{row['predicted']}`（族：{row['family']}，"
            f"置信度 {row.get('confidence')}）",
            f"- 校验结论：`{row.get('status')}`；解释来源：`{row.get('source')}`；"
            f"force={row.get('forced')}",
            f"- 定位：`{_md_cell(row.get('location'))}`",
            f"- 标注备注：{_md_cell(row.get('note'))}",
            f"- token 用量：{usage}",
            "",
            "<details><summary>完整修改建议</summary>",
            "",
            "```text",
            str(row.get("suggestion") or ""),
            "```",
            "",
            "</details>",
            "",
        ]
    if not bad:
        lines.append("本轮无未命中。")
    FAILURES_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="解释质量验收（真实模型）")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 例")
    parser.add_argument("--case", dest="only", default=None,
                        help="只跑指定用例 id")
    parser.add_argument("--cases", dest="only", default=None,
                        help="只跑指定用例，逗号分隔")
    args = parser.parse_args(argv)

    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        print("[SKIP] 未设置 DEEPSEEK_API_KEY，解释质量验收需要真实模型。")
        return 2

    result = evaluate(load_annotations(), limit=args.limit, only=args.only)
    RESULT_JSON.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(result)
    if result["rows"]:
        write_failures(result)
    else:
        FAILURES_MD.write_text("没有可评估的用例。\n", encoding="utf-8")

    print(f"\n严格一致 {result['strict_hits']}/{result['total']} "
          f"({result['strict_rate'] * 100:.2f}%) | "
          f"根因族 {result['family_hits']}/{result['total']} "
          f"({result['family_rate'] * 100:.2f}%)")
    print("分布（预测）：", dict(Counter(
        row.get("predicted") for row in result["rows"])))
    print("token 用量：", result["usage"])
    print("报告：", REPORT_MD)
    print("未命中：", FAILURES_MD)
    return 0 if result["strict_rate"] >= 0.8 else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
