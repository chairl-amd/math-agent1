#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试集批量运行器。

新增：本文件按 annotations.json 逐张调用 agent/main.py，并汇总准确率、
召回率、边界拒绝率、混淆矩阵及失败类 F1。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


# 新增：统一父进程标准流编码，避免 Windows 控制台以 GBK 输出时崩溃。
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


BASE = Path(__file__).resolve().parents[2]
ANNOTATIONS_PATH = Path(__file__).resolve().parent / "annotations.json"
DEFAULT_OUT_DIR = BASE / "output"
EXPECTED_LABELS = ("pass", "fail", "skip")
ACTUAL_LABELS = (
    "pass",
    "fail",
    "non_formula",
    "parse_error",
    "timeout",
    "error",
    "pipeline_error",
    "incomplete",
    "unknown",
)


def positive_int(value: str) -> int:
    """校验 --limit 必须是正整数。"""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--limit 必须是整数") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("--limit 必须大于 0")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="数学公式测试集批量运行器")
    parser.add_argument(
        "--only",
        default="",
        help="只运行指定 id，多个 id 用逗号分隔",
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        default=None,
        help="最多运行前 N 张",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT_DIR),
        help="结果输出目录，默认 D:\\math-agent\\output",
    )
    # 新增：允许直接对比 sympy-only 与 hybrid 两种校验链路。
    parser.add_argument(
        "--verifier",
        choices=["auto", "sympy-only", "hybrid"],
        default="auto",
        help="校验模式：auto=自动链（默认），sympy-only=仅符号，hybrid=符号+DeepSeek",
    )
    return parser


def load_annotations(path: Path) -> list[dict[str, Any]]:
    """读取并校验标注文件。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"标注文件不存在：{path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"标注文件不是合法 JSON：{exc}") from exc
    except OSError as exc:
        raise ValueError(f"无法读取标注文件：{exc}") from exc

    cases = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError("annotations.json 缺少非空 cases 列表")

    seen_ids: set[str] = set()
    required_fields = {"id", "category", "image", "cpp", "expected"}
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"第 {index} 个用例不是对象")
        missing = required_fields - set(case)
        if missing:
            raise ValueError(f"第 {index} 个用例缺少字段：{sorted(missing)}")
        case_id = str(case["id"]).strip()
        if not case_id:
            raise ValueError(f"第 {index} 个用例的 id 为空")
        if case_id in seen_ids:
            raise ValueError(f"用例 id 重复：{case_id}")
        if case["expected"] not in EXPECTED_LABELS:
            raise ValueError(f"用例 {case_id} 的 expected 非法：{case['expected']}")
        seen_ids.add(case_id)
    return cases


def parse_stdout_json(stdout: str) -> tuple[dict[str, Any] | None, str | None]:
    """从 stdout 的第一个左花括号开始解析 JSON，允许前后存在杂文本。"""
    start = stdout.find("{")
    if start < 0:
        return None, "stdout 中未找到 JSON 对象"

    candidate = stdout[start:]
    decoder = json.JSONDecoder()
    try:
        report, _ = decoder.raw_decode(candidate)
    except json.JSONDecodeError:
        report = None
    if isinstance(report, dict):
        return report, None

    # 新增：兼容 JSON 后跟杂文本、且 raw_decode 失败的少见情况。
    for end_index, char in enumerate(candidate):
        if char != "}":
            continue
        try:
            report = json.loads(candidate[: end_index + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(report, dict):
            return report, None
    return None, "stdout 中的 JSON 无法解析"


def normalize_latex(value: Any) -> str:
    """把空值和常见占位值统一视为空 LaTeX。"""
    text = "" if value is None else str(value).strip()
    while len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    placeholders = {
        "",
        "none",
        "null",
        "n/a",
        "无",
        "未识别",
        "未识别到内容",
        "非公式",
        "not a formula",
        "(empty)",
        "`",
        "``",
    }
    return "" if text.lower() in placeholders else text


def get_ocr_latex(report: dict[str, Any]) -> str:
    """安全读取 report.steps.ocr.latex。"""
    steps = report.get("steps")
    if not isinstance(steps, dict):
        return ""
    ocr = steps.get("ocr")
    if not isinstance(ocr, dict):
        return ""
    return normalize_latex(ocr.get("latex"))


def classify_actual(report: dict[str, Any]) -> str:
    """按约定把单张报告映射为实际结论。"""
    report_error = report.get("error")
    steps = report.get("steps")
    ocr_latex = get_ocr_latex(report)
    if report_error and not ocr_latex:
        return "non_formula"

    # 新增：兼容视觉模型把空结果写成占位文本但不设置 error 的情况。
    ocr_step = steps.get("ocr") if isinstance(steps, dict) else None
    if (
        not ocr_latex
        and isinstance(ocr_step, dict)
        and ocr_step.get("ok") is True
    ):
        return "non_formula"

    checks = steps.get("checks", []) if isinstance(steps, dict) else []
    if isinstance(checks, list):
        for check in checks:
            if not isinstance(check, dict) or check.get("name") != "latex_vs_cpp":
                continue
            status = str(check.get("status", "")).strip().lower()
            if status == "equivalent":
                return "pass"
            if status == "not_equivalent":
                return "fail"
            if status == "translation_failed":
                return "incomplete"
            if status == "unknown":
                return "unknown"
            if status == "parse_error":
                return "parse_error"
            return "unknown"

    # 新增：翻译步骤已执行但未产出公式时，单独标记为未完成。
    translate_cpp = (
        steps.get("translate_cpp") if isinstance(steps, dict) else None
    )
    if (
        not checks
        and not report_error
        and isinstance(translate_cpp, dict)
        and not normalize_latex(translate_cpp.get("ref_latex"))
    ):
        return "incomplete"

    if not checks and report.get("ok") is True:
        return "pass"
    return "unknown"


def is_hit(expected: str, actual: str) -> bool:
    """按预期类别判断是否命中。"""
    return (
        (expected == "pass" and actual == "pass")
        or (expected == "fail" and actual == "fail")
        or (expected == "skip" and actual == "non_formula")
    )


def safe_ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def build_suite_metrics(
    cases: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """新增：按 suite 字段分组统计命中情况（未标注时回退到 category）。"""
    case_by_id = {str(case["id"]): case for case in cases}
    grouped: dict[str, dict[str, int]] = {}
    for result in results:
        case = case_by_id.get(result["id"], {})
        suite = str(case.get("suite") or case.get("category") or "unknown")
        bucket = grouped.setdefault(suite, {"total": 0, "hits": 0})
        bucket["total"] += 1
        bucket["hits"] += 1 if result.get("hit") else 0
    return {
        suite: {
            "total": bucket["total"],
            "hits": bucket["hits"],
            "accuracy": safe_ratio(bucket["hits"], bucket["total"]),
        }
        for suite, bucket in sorted(grouped.items())
    }


def build_batch_metrics(
    cases: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """新增（w10）：按 batch 字段分组统计，用于把基线用例与压力用例分开看。

    未标注 batch 的历史用例统一归入 baseline，保证旧报告口径不被破坏。
    """
    case_by_id = {str(case["id"]): case for case in cases}
    grouped: dict[str, dict[str, int]] = {}
    for result in results:
        case = case_by_id.get(result["id"], {})
        batch = str(case.get("batch") or "baseline")
        bucket = grouped.setdefault(batch, {"total": 0, "hits": 0})
        bucket["total"] += 1
        bucket["hits"] += 1 if result.get("hit") else 0
    return {
        batch: {
            "total": bucket["total"],
            "hits": bucket["hits"],
            "accuracy": safe_ratio(bucket["hits"], bucket["total"]),
        }
        for batch, bucket in sorted(grouped.items())
    }


def calculate_metrics(
    cases: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """计算任务要求的指标与标注行、实际列的混淆矩阵。"""
    total = len(results)
    hit_count = sum(1 for result in results if result["hit"])

    correct_cases = [case for case in cases if case["category"] == "correct"]
    incorrect_cases = [case for case in cases if case["category"] == "incorrect"]
    boundary_cases = [case for case in cases if case["category"] == "boundary"]
    result_by_id = {result["id"]: result for result in results}

    correct_pass = sum(
        1
        for case in correct_cases
        if result_by_id[case["id"]]["actual"] == "pass"
    )
    incorrect_fail = sum(
        1
        for case in incorrect_cases
        if result_by_id[case["id"]]["actual"] == "fail"
    )
    boundary_rejected = sum(
        1
        for case in boundary_cases
        if result_by_id[case["id"]]["actual"] == "non_formula"
    )

    true_positive = incorrect_fail
    false_positive = sum(
        1
        for case in correct_cases
        if result_by_id[case["id"]]["actual"] == "fail"
    )
    false_negative = len(incorrect_cases) - incorrect_fail
    precision = safe_ratio(true_positive, true_positive + false_positive)
    recall = safe_ratio(true_positive, true_positive + false_negative)
    f1 = (
        round(2 * precision * recall / (precision + recall), 4)
        if precision is not None and recall is not None and precision + recall > 0
        else (0.0 if precision is not None and recall is not None else None)
    )

    metrics = {
        "total_cases": total,
        "hits": hit_count,
        "overall_accuracy": safe_ratio(hit_count, total),
        "correct_samples": len(correct_cases),
        "correct_pass_count": correct_pass,
        "positive_recall": safe_ratio(correct_pass, len(correct_cases)),
        "incorrect_samples": len(incorrect_cases),
        "incorrect_fail_count": incorrect_fail,
        "negative_recall": safe_ratio(incorrect_fail, len(incorrect_cases)),
        "boundary_samples": len(boundary_cases),
        "boundary_rejected_count": boundary_rejected,
        "boundary_rejection_rate": safe_ratio(
            boundary_rejected, len(boundary_cases)
        ),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "fail_precision": precision,
        "fail_recall": recall,
        "fail_f1": f1,
        # 新增：按 suite（用例来源目录）分组汇总，便于分别观察 pde/matrix_chain。
        "by_suite": build_suite_metrics(cases, results),
        # 新增（w10）：基线用例与压力用例分开统计，避免混在一起看准确率。
        "by_batch": build_batch_metrics(cases, results),
    }

    matrix = {
        expected: {actual: 0 for actual in ACTUAL_LABELS}
        for expected in EXPECTED_LABELS
    }
    for result in results:
        expected = result["expected"]
        actual = result["actual"]
        if actual not in matrix[expected]:
            matrix[expected][actual] = 0
        matrix[expected][actual] += 1
    confusion = {
        "expected_labels": list(EXPECTED_LABELS),
        "actual_labels": list(ACTUAL_LABELS),
        "matrix": matrix,
    }
    return metrics, confusion


def run_case(
    case: dict[str, Any],
    tmp_root: Path,
    verifier: str = "auto",
) -> dict[str, Any]:
    """执行单张用例；任何异常只影响当前用例。"""
    case_id = str(case["id"])
    image_path = Path(__file__).resolve().parent / str(case["image"])
    cpp_path = Path(__file__).resolve().parent / str(case["cpp"])
    case_out_dir = tmp_root / case_id
    case_out_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    if not image_path.is_file() or not cpp_path.is_file():
        missing = []
        if not image_path.is_file():
            missing.append(f"图片不存在：{image_path}")
        if not cpp_path.is_file():
            missing.append(f"C++ 文件不存在：{cpp_path}")
        return {
            "id": case_id,
            "expected": case["expected"],
            "actual": "error",
            "hit": False,
            "elapsed_sec": round(time.perf_counter() - started, 3),
            "error": "；".join(missing),
            "pipeline_ok": False,
        }

    process_result: subprocess.CompletedProcess[str] | None = None
    try:
        # 修改：清理父进程 Python 路径变量，并固定子进程使用 UTF-8。
        child_env = {
            k: v
            for k, v in os.environ.items()
            if k.upper() not in ("PYTHONPATH", "PYTHONHOME")
        }
        child_env["DEEPSEEK_API_KEY"] = os.environ.get("DEEPSEEK_API_KEY", "")
        child_env["PYTHONUTF8"] = "1"
        child_env["PYTHONIOENCODING"] = "utf-8"
        command = [
            sys.executable,
            str(BASE / "agent" / "main.py"),
            "-i",
            str(image_path),
            "-c",
            str(cpp_path),
            "--json",
            "--out",
            str(case_out_dir),
        ]
        # 新增：显式指定校验模式时透传，auto 保持既有默认行为。
        if verifier and verifier != "auto":
            command += ["--verifier-mode", verifier]
        process_result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            env=child_env,
            cwd=str(BASE),
        )
    except subprocess.TimeoutExpired:
        return {
            "id": case_id,
            "expected": case["expected"],
            "actual": "timeout",
            "hit": False,
            "elapsed_sec": round(time.perf_counter() - started, 3),
            "error": "子进程超过 300 秒未完成",
            "pipeline_ok": False,
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "id": case_id,
            "expected": case["expected"],
            "actual": "error",
            "hit": False,
            "elapsed_sec": round(time.perf_counter() - started, 3),
            "error": f"子进程启动或执行失败：{type(exc).__name__}: {exc}",
            "pipeline_ok": False,
        }
    except Exception as exc:
        return {
            "id": case_id,
            "expected": case["expected"],
            "actual": "error",
            "hit": False,
            "elapsed_sec": round(time.perf_counter() - started, 3),
            "error": f"未预期异常：{type(exc).__name__}: {exc}",
            "pipeline_ok": False,
        }

    report, parse_error = parse_stdout_json(process_result.stdout)
    if report is None:
        stderr_text = process_result.stderr.strip()
        details = parse_error or "JSON 解析失败"
        if stderr_text:
            details = f"{details}；stderr: {stderr_text}"
        return {
            "id": case_id,
            "expected": case["expected"],
            "actual": "pipeline_error",
            "hit": False,
            "elapsed_sec": round(time.perf_counter() - started, 3),
            "error": details,
            "pipeline_ok": False,
        }

    actual = classify_actual(report)
    report_error = report.get("error")
    error_text = str(report_error).strip() if report_error else None
    if not error_text and process_result.stderr.strip():
        error_text = f"stderr: {process_result.stderr.strip()}"
    return {
        "id": case_id,
        "expected": case["expected"],
        "actual": actual,
        "hit": is_hit(str(case["expected"]), actual),
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "error": error_text,
        "pipeline_ok": True,
    }


def markdown_cell(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return "-"
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def format_rate(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.2f}%"


def truncate_error_text(value: Any, max_length: int = 200) -> str:
    """新增：截断展示用错误文本，JSON 中仍保留完整内容。"""
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) <= max_length:
        return text
    if max_length <= 3:
        return "." * max_length
    return text[: max_length - 3] + "..."


def build_markdown(
    generated_at: str,
    cases: list[dict[str, Any]],
    results: list[dict[str, Any]],
    metrics: dict[str, Any],
    confusion: dict[str, Any],
) -> str:
    """生成便于快速查看的 Markdown 报告。"""
    case_by_id = {str(case["id"]): case for case in cases}
    lines = [
        "# 数学公式测试集报告",
        "",
        f"- 生成时间：{generated_at}",
        f"- 用例总数：{metrics['total_cases']}",
        "",
        "## 逐张结果",
        "",
        "| 图 | 分类 | 预期 | 实际 | 是否命中 | 耗时(秒) | 错误 |",
        "|---|---|---|---|---:|---:|---|",
    ]
    for result in results:
        case = case_by_id[result["id"]]
        lines.append(
            "| {image} | {category} | {expected} | {actual} | {hit} | "
            "{elapsed} | {error} |".format(
                image=markdown_cell(case["image"]),
                category=markdown_cell(case["category"]),
                expected=markdown_cell(result["expected"]),
                actual=markdown_cell(result["actual"]),
                hit="是" if result["hit"] else "否",
                elapsed=f"{result['elapsed_sec']:.3f}",
                error=truncate_error_text(markdown_cell(result["error"])),
            )
        )

    lines += [
        "",
        "## 汇总指标",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 总体准确率 | {format_rate(metrics['overall_accuracy'])} "
        f"({metrics['hits']}/{metrics['total_cases']}) |",
        f"| 正类召回（应 pass） | {format_rate(metrics['positive_recall'])} "
        f"({metrics['correct_pass_count']}/{metrics['correct_samples']}) |",
        f"| 负类召回（应 fail） | {format_rate(metrics['negative_recall'])} "
        f"({metrics['incorrect_fail_count']}/{metrics['incorrect_samples']}) |",
        f"| 边界拒绝率 | {format_rate(metrics['boundary_rejection_rate'])} "
        f"({metrics['boundary_rejected_count']}/{metrics['boundary_samples']}) |",
        f"| fail 类 Precision | {format_rate(metrics['fail_precision'])} |",
        f"| fail 类 Recall | {format_rate(metrics['fail_recall'])} |",
        f"| fail 类 F1 | {format_rate(metrics['fail_f1'])} |",
        "",
        "## 按套件分组",
        "",
        "| 套件 | 命中 | 总数 | 准确率 |",
        "|---|---:|---:|---:|",
    ]
    for suite, bucket in (metrics.get("by_suite") or {}).items():
        lines.append(
            f"| {markdown_cell(suite)} | {bucket['hits']} | "
            f"{bucket['total']} | {format_rate(bucket['accuracy'])} |"
        )

    lines += [
        "",
        "## 按批次分组（baseline=历史基线，stress=w10 压力集）",
        "",
        "| 批次 | 命中 | 总数 | 准确率 |",
        "|---|---:|---:|---:|",
    ]
    for batch, bucket in (metrics.get("by_batch") or {}).items():
        lines.append(
            f"| {markdown_cell(batch)} | {bucket['hits']} | "
            f"{bucket['total']} | {format_rate(bucket['accuracy'])} |"
        )

    lines += [
        "",
        "## 混淆矩阵",
        "",
    ]
    actual_labels = confusion["actual_labels"]
    lines.append("| 标注 \\ 实际 | " + " | ".join(actual_labels) + " |")
    lines.append("|---|" + "|".join("---:" for _ in actual_labels) + "|")
    for expected in confusion["expected_labels"]:
        row = confusion["matrix"][expected]
        lines.append(
            f"| {expected} | "
            + " | ".join(str(row.get(actual, 0)) for actual in actual_labels)
            + " |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        all_cases = load_annotations(ANNOTATIONS_PATH)
    except ValueError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2

    only_ids = [
        item.strip()
        for item in args.only.split(",")
        if item.strip()
    ]
    available_ids = {str(case["id"]) for case in all_cases}
    unknown_ids = [case_id for case_id in only_ids if case_id not in available_ids]
    if unknown_ids:
        print(f"[错误] --only 包含未知 id：{','.join(unknown_ids)}", file=sys.stderr)
        return 2

    selected_cases = (
        [case for case in all_cases if str(case["id"]) in set(only_ids)]
        if only_ids
        else all_cases
    )
    if args.limit is not None:
        selected_cases = selected_cases[: args.limit]
    if not selected_cases:
        print("[错误] 没有可运行的用例", file=sys.stderr)
        return 2

    out_dir = Path(args.out).expanduser().resolve()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"[错误] 无法创建输出目录 {out_dir}：{exc}", file=sys.stderr)
        return 2

    # 修改：Windows 的系统临时目录可能 ACL 受限，因此改在可写的 output 下留存中间报告。
    tmp_root = (
        out_dir
        / "_benchmark_tmp"
        / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    )
    results: list[dict[str, Any]] = []
    total = len(selected_cases)
    for index, case in enumerate(selected_cases, start=1):
        result = run_case(case, tmp_root, verifier=args.verifier)
        results.append(result)
        print(
            f"进度 [{index}/{total}] {result['id']} | "
            f"预期={result['expected']} | 实际={result['actual']} | "
            f"命中={'是' if result['hit'] else '否'} | "
            f"耗时={result['elapsed_sec']:.3f}s | "
            f"错误={truncate_error_text(result['error']) or '无'}",
            flush=True,
        )

    metrics, confusion = calculate_metrics(selected_cases, results)
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    result_document = {
        "generated_at": generated_at,
        "cases": results,
        "metrics": metrics,
        "confusion": confusion,
    }
    json_path = out_dir / "benchmark_result.json"
    markdown_path = out_dir / "benchmark_report.md"
    try:
        json_path.write_text(
            json.dumps(result_document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        markdown_path.write_text(
            build_markdown(
                generated_at,
                selected_cases,
                results,
                metrics,
                confusion,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"[错误] 结果报告写入失败：{exc}", file=sys.stderr)
        return 2

    print(
        "汇总 | "
        f"命中={metrics['hits']}/{metrics['total_cases']} | "
        f"总体准确率={metrics['overall_accuracy']} | "
        f"正类召回={metrics['positive_recall']} | "
        f"负类召回={metrics['negative_recall']} | "
        f"边界拒绝率={metrics['boundary_rejection_rate']} | "
        f"fail_F1={metrics['fail_f1']}"
    )
    print(f"结果已保存：{json_path}")
    print(f"报告已保存：{markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
