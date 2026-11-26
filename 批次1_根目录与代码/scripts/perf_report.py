# -*- coding: utf-8 -*-
"""工单5：从 benchmark 报告生成性能剖析报告（只读，不触发任何网络调用）。"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_DIR = ROOT / "output" / "benchmark_w8_hybrid"
OUT_FILE = ROOT / "output" / "performance_analysis.md"


def _load_reports(run_dir: Path) -> list[dict]:
    tmp_root = run_dir / "_benchmark_tmp"
    if not tmp_root.is_dir():
        raise SystemExit(f"找不到运行目录：{tmp_root}（请先跑 benchmark）")
    latest = sorted(tmp_root.iterdir(), key=lambda p: p.stat().st_mtime)[-1]
    reports = []
    for case_dir in sorted(latest.iterdir()):
        if not case_dir.is_dir():
            continue
        files = sorted(case_dir.glob("report_*.json"))
        if not files:
            continue
        payload = json.loads(files[-1].read_text(encoding="utf-8"))
        payload["_case"] = case_dir.name
        reports.append(payload)
    if not reports:
        raise SystemExit(f"{latest} 下没有报告文件")
    return reports


def _pct(part: float, total: float) -> str:
    return f"{part / total * 100:.1f}%" if total > 0 else "-"


def _num(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _collect_stage_times(report: dict) -> dict:
    """抽取各阶段“首次真实耗时”，缓存命中时沿用当时的测量值。"""
    steps = report.get("steps") or {}
    ocr = steps.get("ocr") or {}
    ocr_meta = ocr.get("meta") or {}
    trans = steps.get("translate_cpp") or {}
    trans_meta = trans.get("meta") or {}

    ocr_time = _num(ocr_meta.get("elapsed"))
    ocr_cached = str(ocr_meta.get("cache") or "") == "hit"

    if trans_meta.get("cache_hit"):
        cached = trans_meta.get("cached_meta") or {}
        trans_time = _num(cached.get("elapsed"))
        trans_cached = True
    else:
        trans_time = _num(trans_meta.get("elapsed"))
        trans_cached = False

    sympy_time = 0.0
    llm_time = 0.0
    llm_calls = 0
    llm_retries = 0
    prompt_tokens = 0
    completion_tokens = 0
    checks_cached = True
    slowest: list[dict] = []

    for check in steps.get("checks") or []:
        meta = check.get("meta") or {}
        if str(meta.get("cache") or "") != "hit":
            checks_cached = False
        sympy_verdict = meta.get("sympy_verdict")
        sympy_meta = (sympy_verdict.get("meta")
                      if isinstance(sympy_verdict, dict) else {}) or {}
        sympy_time += _num(sympy_meta.get("elapsed"))
        for verdict in meta.get("hetero_verdicts") or []:
            vmeta = verdict.get("meta") or {}
            elapsed = _num(vmeta.get("elapsed"))
            llm_time += elapsed
            llm_calls += 1
            if vmeta.get("empty_retry"):
                llm_retries += 1
            usage = vmeta.get("usage") or {}
            prompt_tokens += int(_num(usage.get("prompt_tokens")))
            completion_tokens += int(_num(usage.get("completion_tokens")))
            slowest.append({
                "case": report["_case"],
                "check": check.get("name") or "-",
                "verifier": vmeta.get("verifier") or "-",
                "elapsed": elapsed,
                "empty_retry": bool(vmeta.get("empty_retry")),
            })

    return {
        "elapsed": _num(report.get("elapsed")),
        "ocr": ocr_time,
        "ocr_cached": ocr_cached,
        "translate": trans_time,
        "translate_cached": trans_cached,
        "sympy": sympy_time,
        "llm": llm_time,
        "llm_calls": llm_calls,
        "llm_retries": llm_retries,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "checks_cached": checks_cached,
        "slow_calls": slowest,
    }


def build_report(reports: list[dict]) -> str:
    rows = [(r["_case"], _collect_stage_times(r)) for r in reports]
    warm = [row for row in rows
            if row[1]["ocr_cached"] and row[1]["translate_cached"]
            and row[1]["checks_cached"]]
    cold = [row for row in rows if row not in warm]
    totals = [row[1]["elapsed"] for row in rows]
    ocr_sum = sum(row[1]["ocr"] for row in cold)
    trans_sum = sum(row[1]["translate"] for row in cold)
    sympy_sum = sum(row[1]["sympy"] for row in cold)
    llm_sum = sum(row[1]["llm"] for row in cold)
    stage_sum = ocr_sum + trans_sum + sympy_sum + llm_sum
    llm_calls = sum(row[1]["llm_calls"] for row in cold)
    retries = sum(row[1]["llm_retries"] for row in cold)
    prompt_tokens = sum(row[1]["prompt_tokens"] for row in cold)
    completion_tokens = sum(row[1]["completion_tokens"] for row in cold)
    llm_times = [c["elapsed"] for row in cold for c in row[1]["slow_calls"]]

    lines = [
        "# 性能剖析报告（工单5）",
        "",
        f"- 数据来源：`{DEFAULT_RUN_DIR.relative_to(ROOT)}` 中最新一次 hybrid 运行",
        f"- 用例数：{len(rows)}（冷启动 {len(cold)} / 全缓存命中 {len(warm)}）",
        "- 说明：缓存命中时各阶段耗时沿用首次真实测量值，"
        "因此「阶段合计」代表首次跑完这批用例的真实成本，"
        "而「本次墙钟」反映缓存命中后的交互延迟。",
        "",
        "## 1. 总耗时",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 本次墙钟合计 | {sum(totals):.1f}s |",
        f"| 平均每例（墙钟） | {statistics.mean(totals):.2f}s |",
        f"| 中位数 | {statistics.median(totals):.2f}s |",
        f"| P90 | {sorted(totals)[max(0, int(len(totals) * 0.9) - 1)]:.2f}s |",
        f"| 最慢 | {max(totals):.2f}s |",
    ]
    if warm:
        warm_times = [row[1]["elapsed"] for row in warm]
        lines.append(
            f"| 全缓存命中平均 | {statistics.mean(warm_times):.2f}s |")
    lines += [
        "",
        "## 2. 阶段拆分（冷启动样本）",
        "",
        "| 阶段 | 合计(s) | 占比 | 平均(s) |",
        "|---|---:|---:|---:|",
    ]
    for label, value, count in (
        ("OCR（视觉模型）", ocr_sum, len(cold)),
        ("C++→LaTeX 翻译", trans_sum, len(cold)),
        ("SymPy 校验", sympy_sum, len(cold)),
        ("异构 LLM 校验", llm_sum, max(1, llm_calls)),
    ):
        shown = f"{value:.1f}" if value > 0 else "≈0"
        lines.append(
            f"| {label} | {shown} | {_pct(value, stage_sum)} | "
            f"{value / count:.2f} |")
    overhead = max(0.0, sum(totals) - stage_sum)
    lines += [
        f"| **阶段合计** | **{stage_sum:.1f}** | 100% | "
        f"{stage_sum / max(1, len(cold)):.2f} |",
        f"| 其它（进程启动、导入 sympy/openai、写报告） | "
        f"{overhead:.1f} | - | {overhead / max(1, len(rows)):.2f} |",
        "",
        f"> 每例都新起一个 python 进程，固定成本约 "
        f"{overhead / max(1, len(rows)):.1f}s；Web 界面复用同一个 Pipeline，"
        f"这部分成本被摊薄，因此交互延迟远低于 benchmark 墙钟。",
        "",
        "## 3. 异构模型调用",
        "",
        f"- 调用次数：{llm_calls} 次（空响应重试 {retries} 次）",
    ]
    if llm_times:
        lines += [
            f"- 单次耗时：平均 {statistics.mean(llm_times):.2f}s / "
            f"中位 {statistics.median(llm_times):.2f}s / "
            f"最大 {max(llm_times):.2f}s",
        ]
    lines += [
        f"- Token 用量：prompt {prompt_tokens} / completion {completion_tokens}"
        f"（合计 {prompt_tokens + completion_tokens}）",
        "",
        "## 4. 最慢的 5 次模型调用",
        "",
        "| 用例 | 校验项 | 校验器 | 耗时(s) | 空响应重试 |",
        "|---|---|---|---:|:--:|",
    ]
    for call in sorted(
            (c for row in cold for c in row[1]["slow_calls"]),
            key=lambda item: -item["elapsed"])[:5]:
        lines.append(
            f"| {call['case']} | {call['check']} | {call['verifier']} | "
            f"{call['elapsed']:.2f} | {'是' if call['empty_retry'] else '否'} |")

    slowest_cases = sorted(rows, key=lambda row: -row[1]["elapsed"])[:5]
    lines += [
        "",
        "## 5. 最慢的 5 个用例",
        "",
        "| 用例 | 墙钟(s) | OCR | 翻译 | SymPy | 异构 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, stage in slowest_cases:
        lines.append(
            f"| {name} | {stage['elapsed']:.2f} | {stage['ocr']:.2f} | "
            f"{stage['translate']:.2f} | {stage['sympy']:.2f} | "
            f"{stage['llm']:.2f} |")

    lines += [
        "",
        "## 6. 结论与优化建议",
        "",
        f"- 首次全量成本约 {stage_sum:.0f}s，其中异构 LLM 占 {_pct(llm_sum, stage_sum)}，"
        f"是绝对瓶颈；SymPy 仅占 {_pct(sympy_sum, stage_sum)}。",
        f"- 缓存命中后单例约 "
        f"{statistics.mean([row[1]['elapsed'] for row in warm]):.2f}s"
        if warm else "- 本次没有全缓存命中样本。",
        "- 可行优化（按收益排序）：",
        "  1. **提高异构结论复用率**：校验缓存键已含 LaTeX/C++ 与逻辑版本，"
        "重复图片可直接命中（实测约 1s/例），批量场景应优先保证命中率；",
        "  2. **抑制空响应重试**：重试会直接翻倍单次成本，"
        "提示词需继续保持「仅输出 JSON」约束；",
        "  3. **并行化多校验项**：同一用例的多个 check 相互独立，"
        "可在 chain 层并行发起，把多校验项用例的墙钟压到最慢一次调用；",
        "  4. **compact_cpp_context 已生效**：原始/裁剪字符数记录在报告 "
        "`meta.cpp_context`，提示词长度不是当前瓶颈，继续裁剪收益有限。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    run_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_RUN_DIR
    reports = _load_reports(run_dir)
    markdown = build_report(reports)
    OUT_FILE.write_text(markdown, encoding="utf-8")
    print(f"[完成] {OUT_FILE}（{len(reports)} 例）")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
