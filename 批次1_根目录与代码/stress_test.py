#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新增：端到端压测脚本，仅使用 Python 标准库。"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


ROOT_DIR = Path(__file__).resolve().parent
PYTHON_FILE = ROOT_DIR / ".python312" / "python.exe"
MAIN_FILE = ROOT_DIR / "agent" / "main.py"
ANNOTATIONS_FILE = ROOT_DIR / "tests" / "benchmark" / "annotations.json"
STRUCTURAL_DIR = ROOT_DIR / "tests" / "benchmark" / "structural_errors"
STRUCTURAL_CASES_FILE = STRUCTURAL_DIR / "cases.json"
FORMULA_DIR = ROOT_DIR / "tests" / "formula"
EMPTY_CPP_FILE = ROOT_DIR / ".tmp" / "empty.cpp"
STRESS_TMP_DIR = ROOT_DIR / "output" / "_stress_tmp"
JSON_REPORT = ROOT_DIR / "output" / "stress_test_report.json"
MARKDOWN_REPORT = ROOT_DIR / "output" / "stress_test_report.md"
TIMEOUT_SECONDS = 180


def _read_json(path: Path) -> Any:
    """新增：读取 UTF-8 JSON，并把错误转成明确运行异常。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取 JSON：{path}：{exc}") from exc


def _ensure_empty_cpp() -> None:
    """新增：仅在文件不存在时创建 0 字节 C++，避免覆盖已有内容。"""
    try:
        EMPTY_CPP_FILE.parent.mkdir(parents=True, exist_ok=True)
        if EMPTY_CPP_FILE.exists() and EMPTY_CPP_FILE.stat().st_size > 0:
            raise RuntimeError(
                f"{EMPTY_CPP_FILE} 已存在且非空，请先确认后再手动清理")
        if not EMPTY_CPP_FILE.exists():
            EMPTY_CPP_FILE.write_bytes(b"")
    except OSError as exc:
        raise RuntimeError(f"无法准备空 C++ 文件：{exc}") from exc


def _base_case(case_id: str, category: str, description: str) -> dict:
    """新增：统一用例公共字段。"""
    return {
        "id": case_id,
        "category": category,
        "description": description,
        "args": [],
        "expected_ok": None,
        "normal": True,
    }


def build_cases() -> tuple[list[dict], list[dict]]:
    """新增：按工单固定清单构建 20 个正常用例和 4 个异常用例。"""
    annotations = _read_json(ANNOTATIONS_FILE)
    annotation_cases = annotations.get("cases", [])
    if not isinstance(annotation_cases, list):
        raise RuntimeError("annotations.json 的 cases 必须是数组")

    normal_cases: list[dict] = []
    for item in annotation_cases:
        case = _base_case(
            str(item["id"]),
            "benchmark",
            f"benchmark/{item.get('category', 'unknown')}",
        )
        case["args"] = [
            "-i", str(ROOT_DIR / "tests" / "benchmark" / item["image"]),
            "-c", str(ROOT_DIR / "tests" / "benchmark" / item["cpp"]),
        ]
        case["expected_ok"] = (
            True if item.get("expected") == "pass"
            else False if item.get("expected") == "fail"
            else None
        )
        case["validation_applicable"] = True
        normal_cases.append(case)

    structural_cases = _read_json(STRUCTURAL_CASES_FILE)
    if not isinstance(structural_cases, list):
        raise RuntimeError("structural_errors/cases.json 必须是数组")
    for item in structural_cases:
        latex_file = STRUCTURAL_DIR / item["latex_file"]
        try:
            latex_text = latex_file.read_text(
                encoding="utf-8", errors="replace")
        except OSError as exc:
            raise RuntimeError(f"无法读取结构错误 LaTeX：{latex_file}") from exc
        case = _base_case(
            f"structural_{item['id']}",
            "structural",
            str(item.get("note", "structural")),
        )
        case["args"] = [
            "-l", latex_text,
            "-c", str(STRUCTURAL_DIR / item["cpp"]),
        ]
        case["expected_ok"] = item.get("expected") == "pass"
        case["validation_applicable"] = True
        normal_cases.append(case)

    for index in range(1, 11):
        case = _base_case(
            f"formula_{index:02d}",
            "formula",
            "仅测试公式图片 OCR 流程",
        )
        case["args"] = [
            "-i", str(FORMULA_DIR / f"formula_{index:02d}.png"),
        ]
        case["validation_applicable"] = False
        normal_cases.append(case)

    _ensure_empty_cpp()
    abnormal_cases = [
        {
            **_base_case(
                "abnormal_empty_png",
                "abnormal",
                "0 字节图片文件",
            ),
            "args": ["-i", str(ROOT_DIR / ".tmp" / "empty.png")],
            "normal": False,
        },
        {
            **_base_case(
                "abnormal_not_image",
                "abnormal",
                "扩展名与内容均不是图片",
            ),
            "args": ["-i", str(ROOT_DIR / ".tmp" / "not_image.txt")],
            "normal": False,
        },
        {
            **_base_case(
                "abnormal_missing_image",
                "abnormal",
                "不存在的图片路径",
            ),
            "args": ["-i", str(ROOT_DIR / ".tmp" / "missing_image.png")],
            "normal": False,
        },
        {
            **_base_case(
                "abnormal_empty_cpp",
                "abnormal",
                "0 字节 C++ 文件配合正常图片",
            ),
            "args": [
                "-i", str(
                    ROOT_DIR / "tests" / "benchmark" / "correct"
                    / "image_02.png"),
                "-c", str(EMPTY_CPP_FILE),
            ],
            "normal": False,
        },
    ]
    return normal_cases, abnormal_cases


def _redact(text: Any) -> str:
    """新增：从输出中移除当前 API Key，绝不写入报告。"""
    value = str(text or "")
    secret = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if secret:
        value = value.replace(secret, "[已脱敏]")
    return value


def _short_text(value: Any, limit: int = 500) -> str:
    """新增：把错误压缩成单行短文本。"""
    text = " ".join(_redact(value).split())
    return text[:limit]


def _has_traceback(text: str) -> bool:
    """新增：识别 Python 未捕获异常输出。"""
    return bool(re.search(
        r"Traceback \(most recent call last\)|"
        r"Unhandled exception|未处理异常",
        text or "",
        flags=re.IGNORECASE,
    ))


def _stderr_summary(text: str) -> str:
    """新增：从 traceback 中提取最后一条关键异常，避免报告被整段堆栈淹没。"""
    lines = [line.strip() for line in _redact(text).splitlines() if line.strip()]
    for line in reversed(lines):
        if re.search(
            r"(?:Error|Exception|RuntimeError|ImportError|OSError|"
            r"请设置|失败|异常)",
            line,
            flags=re.IGNORECASE,
        ):
            return _short_text(line)
    return _short_text(lines[-1] if lines else "")


def _cache_values(report: Optional[dict]) -> dict:
    """新增：兼容 report.cache 顶层与 total 嵌套结构。"""
    if not isinstance(report, dict):
        return {"hits": 0, "misses": 0, "writes": 0, "errors": 0}
    cache = report.get("cache", {})
    if not isinstance(cache, dict):
        return {"hits": 0, "misses": 0, "writes": 0, "errors": 0}
    total = cache.get("total", {})
    source = total if isinstance(total, dict) else cache
    return {
        name: int(source.get(name, 0) or 0)
        for name in ("hits", "misses", "writes", "errors")
    }


def run_one(case: dict) -> dict:
    """新增：运行单个用例并记录 JSON、耗时、缓存与错误摘要。"""
    out_dir = STRESS_TMP_DIR / case["id"]
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {
            "id": case["id"],
            "category": case["category"],
            "description": case["description"],
            "normal": case["normal"],
            "validation_applicable": case.get(
                "validation_applicable", False),
            "expected_ok": case.get("expected_ok"),
            "returncode": None,
            "json_ok": False,
            "elapsed_seconds": 0.0,
            "report_ok": None,
            "cache": {"hits": 0, "misses": 0, "writes": 0, "errors": 0},
            "error": _short_text(f"无法创建输出目录：{exc}"),
            "traceback": False,
            "graceful_failure": False,
            "command": [],
        }
    command = [
        str(PYTHON_FILE),
        str(MAIN_FILE),
        "--json",
        "--out",
        str(out_dir),
        *case["args"],
    ]
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    started = time.perf_counter()
    completed: Optional[subprocess.CompletedProcess] = None
    timeout_error = ""
    process_error = ""
    try:
        completed = subprocess.run(
            command,
            cwd=str(ROOT_DIR),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        timeout_error = f"timeout>{TIMEOUT_SECONDS}s: {exc}"
    except OSError as exc:
        process_error = f"{type(exc).__name__}: {exc}"

    elapsed = round(time.perf_counter() - started, 3)
    stdout = completed.stdout if completed is not None else ""
    stderr = completed.stderr if completed is not None else ""
    returncode = completed.returncode if completed is not None else None
    report = None
    parse_error = ""
    if stdout.strip():
        try:
            parsed = json.loads(stdout.strip())
            if isinstance(parsed, dict):
                report = parsed
            else:
                parse_error = "stdout JSON 顶层不是对象"
        except json.JSONDecodeError as exc:
            parse_error = f"stdout 不是合法 JSON：{exc}"
    elif not timeout_error and not process_error:
        parse_error = "stdout 为空"

    valid_json = report is not None
    cache = _cache_values(report)
    report_ok = report.get("ok") if report is not None else None
    report_error = report.get("error", "") if report is not None else ""
    stderr_text = _redact(stderr)
    error_summary = _short_text(
        report_error or timeout_error or process_error
        or _stderr_summary(stderr_text) or parse_error
    )
    traceback_found = _has_traceback(stderr_text)
    graceful_failure = (
        returncode in {1, 2}
        and not traceback_found
        and not timeout_error
        and not process_error
    )
    return {
        "id": case["id"],
        "category": case["category"],
        "description": case["description"],
        "normal": case["normal"],
        "validation_applicable": case.get("validation_applicable", False),
        "expected_ok": case.get("expected_ok"),
        "returncode": returncode,
        "json_ok": valid_json,
        "elapsed_seconds": elapsed,
        "report_ok": report_ok,
        "cache": cache,
        "error": error_summary,
        "traceback": traceback_found,
        "graceful_failure": graceful_failure,
        "command": command,
    }


def percentile_95(values: list[float]) -> float:
    """新增：用最近秩法计算 P95，不依赖第三方库。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return ordered[index]


def build_summary(results: list[dict]) -> dict:
    """新增：计算流程、校验、耗时与缓存指标。"""
    normal_results = [item for item in results if item["normal"]]
    abnormal_results = [item for item in results if not item["normal"]]
    normal_elapsed = [
        float(item["elapsed_seconds"]) for item in normal_results]
    normal_json_ok = sum(
        1 for item in normal_results if item["json_ok"])
    validation_cases = [
        item for item in normal_results
        if item["validation_applicable"] and item["expected_ok"] is True
    ]
    negative_cases = [
        item for item in normal_results
        if item["validation_applicable"] and item["expected_ok"] is False
    ]
    validation_passed = sum(
        1 for item in validation_cases if item["report_ok"] is True)
    negative_caught = sum(
        1 for item in negative_cases if item["report_ok"] is False)
    total_hits = sum(int(item["cache"]["hits"]) for item in results)
    total_misses = sum(int(item["cache"]["misses"]) for item in results)
    total_writes = sum(int(item["cache"]["writes"]) for item in results)
    graceful_abnormal = sum(
        1 for item in abnormal_results if item["graceful_failure"])
    cache_denominator = total_hits + total_misses
    return {
        "normal_total": len(normal_results),
        "normal_json_success": normal_json_ok,
        "normal_process_success_rate": round(
            normal_json_ok / len(normal_results), 4
        ) if normal_results else 0.0,
        "validation_total": len(validation_cases),
        "validation_passed": validation_passed,
        "validation_pass_rate": round(
            validation_passed / len(validation_cases), 4
        ) if validation_cases else 0.0,
        "negative_total": len(negative_cases),
        "negative_caught": negative_caught,
        "negative_capture_rate": round(
            negative_caught / len(negative_cases), 4
        ) if negative_cases else 0.0,
        "elapsed_average_seconds": round(
            statistics.mean(normal_elapsed), 3
        ) if normal_elapsed else 0.0,
        "elapsed_median_seconds": round(
            statistics.median(normal_elapsed), 3
        ) if normal_elapsed else 0.0,
        "elapsed_p95_seconds": round(
            percentile_95(normal_elapsed), 3
        ) if normal_elapsed else 0.0,
        "cache_hits": total_hits,
        "cache_misses": total_misses,
        "cache_writes": total_writes,
        "cache_hit_rate": round(
            total_hits / cache_denominator, 4
        ) if cache_denominator else 0.0,
        "abnormal_total": len(abnormal_results),
        "abnormal_graceful": graceful_abnormal,
        "abnormal_graceful_rate": round(
            graceful_abnormal / len(abnormal_results), 4
        ) if abnormal_results else 0.0,
    }


def build_weaknesses(results: list[dict], summary: dict) -> list[str]:
    """新增：只根据本次结果生成可复现的薄弱点描述。"""
    weaknesses: list[str] = []
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        weaknesses.append(
            "当前环境未配置 DEEPSEEK_API_KEY，子进程无法完成依赖翻译层的正常用例。")

    timeout_ids = [
        item["id"] for item in results
        if item["returncode"] is None and "timeout>" in item["error"]
    ]
    if timeout_ids:
        weaknesses.append(f"发生超时的用例：{', '.join(timeout_ids)}。")

    json_failures = [
        item for item in results if item["normal"] and not item["json_ok"]
    ]
    if json_failures:
        weaknesses.append(
            "正常用例未产出合法 JSON："
            + "；".join(
                f"{item['id']}（{item['error'] or '无错误摘要'}）"
                for item in json_failures[:8]
            )
        )

    unexpected_failures = [
        item for item in results
        if item["normal"] and item["expected_ok"] is True
        and item["json_ok"] and item["report_ok"] is not True
    ]
    if unexpected_failures:
        weaknesses.append(
            "预期通过但 report.ok 未通过的用例："
            + "，".join(item["id"] for item in unexpected_failures)
            + "。"
        )

    non_graceful = [
        item for item in results
        if not item["normal"] and not item["graceful_failure"]
    ]
    if non_graceful:
        weaknesses.append(
            "异常输入未优雅失败："
            + "；".join(
                f"{item['id']}（returncode={item['returncode']}，"
                f"{item['error'] or '无错误摘要'}）"
                for item in non_graceful
            )
        )

    if summary["cache_hits"] + summary["cache_misses"] == 0:
        weaknesses.append("所有有效子进程报告均未产生缓存命中/未命中统计。")
    if not weaknesses:
        weaknesses.append("本次运行未观察到需要记录的薄弱点。")
    return weaknesses


def markdown_cell(value: Any) -> str:
    """新增：转义 Markdown 表格中的竖线与换行。"""
    return (
        str(value if value is not None else "")
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def build_markdown(payload: dict) -> str:
    """新增：生成逐例表格、汇总、失败列表与薄弱点。"""
    summary = payload["summary"]
    lines = [
        "# math-agent 端到端压测报告",
        "",
        f"- 生成时间：{payload['generated_at']}",
        f"- DEEPSEEK_API_KEY 已配置："
        f"{'是' if payload['api_key_configured'] else '否'}",
        f"- 子进程超时：{TIMEOUT_SECONDS}s",
        f"- 正常用例数：{summary['normal_total']}",
        f"- 异常用例数：{summary['abnormal_total']}",
        "",
        "## 汇总指标",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 正常用例流程成功率 | "
        f"{summary['normal_process_success_rate']:.2%} "
        f"({summary['normal_json_success']}/{summary['normal_total']}) |",
        f"| 预期通过用例通过率 | "
        f"{summary['validation_pass_rate']:.2%} "
        f"({summary['validation_passed']}/{summary['validation_total']}) |",
        f"| 预期失败用例拦截率 | "
        f"{summary['negative_capture_rate']:.2%} "
        f"({summary['negative_caught']}/{summary['negative_total']}) |",
        f"| 平均耗时 | {summary['elapsed_average_seconds']:.3f}s |",
        f"| 中位耗时 | {summary['elapsed_median_seconds']:.3f}s |",
        f"| P95 耗时 | {summary['elapsed_p95_seconds']:.3f}s |",
        f"| 缓存命中率 | {summary['cache_hit_rate']:.2%} "
        f"({summary['cache_hits']}/{summary['cache_hits'] + summary['cache_misses']}) |",
        f"| 异常用例优雅失败率 | "
        f"{summary['abnormal_graceful_rate']:.2%} "
        f"({summary['abnormal_graceful']}/{summary['abnormal_total']}) |",
        "",
        "## 逐例结果",
        "",
        "| ID | 类别 | 返回码 | 合法 JSON | 耗时(s) | report.ok | "
        "缓存 H/M/W | 错误摘要 |",
        "|---|---|---:|---|---:|---|---:|---|",
    ]
    for item in payload["results"]:
        cache = item["cache"]
        lines.append(
            f"| {markdown_cell(item['id'])} | "
            f"{markdown_cell(item['category'])} | "
            f"{item['returncode'] if item['returncode'] is not None else '-'} | "
            f"{'是' if item['json_ok'] else '否'} | "
            f"{item['elapsed_seconds']:.3f} | "
            f"{item['report_ok'] if item['report_ok'] is not None else '-'} | "
            f"{cache['hits']}/{cache['misses']}/{cache['writes']} | "
            f"{markdown_cell(item['error'] or '-')} |"
        )

    failures = [
        item for item in payload["results"]
        if (
            (item["normal"] and not item["json_ok"])
            or (
                item["normal"]
                and item["expected_ok"] is True
                and item["json_ok"]
                and item["report_ok"] is not True
            )
            or (not item["normal"] and not item["graceful_failure"])
        )
    ]
    lines += ["", "## 失败用例", ""]
    if failures:
        for item in failures:
            lines.append(
                f"- `{item['id']}`：returncode={item['returncode']}，"
                f"{item['error'] or '无错误摘要'}"
            )
    else:
        lines.append("- 无。")

    lines += ["", "## 薄弱点与建议", ""]
    lines.extend(f"- {item}" for item in payload["weaknesses"])
    lines.append("")
    return "\n".join(lines)


def positive_int(value: str) -> int:
    """新增：限制 --limit 为正整数。"""
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--limit 必须是整数") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("--limit 必须大于 0")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="math-agent 端到端压测（20 个正常用例 + 4 个异常用例）")
    parser.add_argument(
        "--limit",
        type=positive_int,
        default=None,
        help="只跑前 N 个正常用例；异常用例仍会执行",
    )
    parser.add_argument(
        "--only",
        default="",
        help="逗号分隔的用例 ID，只运行匹配项",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """新增：执行压测并写出 JSON/Markdown 报告。"""
    args = build_parser().parse_args(argv)
    if not PYTHON_FILE.is_file():
        print(f"项目 Python 不存在：{PYTHON_FILE}", file=sys.stderr)
        return 2
    if not MAIN_FILE.is_file():
        print(f"主程序不存在：{MAIN_FILE}", file=sys.stderr)
        return 2

    try:
        normal_cases, abnormal_cases = build_cases()
    except RuntimeError as exc:
        print(f"[错误] 构建用例失败：{exc}", file=sys.stderr)
        return 2

    only_ids = {
        item.strip() for item in args.only.split(",") if item.strip()
    }
    if only_ids:
        normal_cases = [
            case for case in normal_cases if case["id"] in only_ids]
        abnormal_cases = [
            case for case in abnormal_cases if case["id"] in only_ids]
    if args.limit is not None:
        normal_cases = normal_cases[:args.limit]

    selected_cases = normal_cases + abnormal_cases
    if not selected_cases:
        print("[错误] --only 未匹配任何用例", file=sys.stderr)
        return 2

    results = []
    for index, case in enumerate(selected_cases, start=1):
        print(
            f"[{index}/{len(selected_cases)}] 运行 {case['id']}",
            flush=True,
        )
        results.append(run_one(case))

    summary = build_summary(results)
    weaknesses = build_weaknesses(results, summary)
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(
            timespec="seconds"),
        "api_key_configured": bool(
            os.environ.get("DEEPSEEK_API_KEY", "").strip()),
        "timeout_seconds": TIMEOUT_SECONDS,
        "case_counts": {
            "normal": len(normal_cases),
            "abnormal": len(abnormal_cases),
            "total": len(selected_cases),
        },
        "summary": summary,
        "results": results,
        "weaknesses": weaknesses,
    }
    try:
        JSON_REPORT.parent.mkdir(parents=True, exist_ok=True)
        JSON_REPORT.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        MARKDOWN_REPORT.write_text(build_markdown(payload), encoding="utf-8")
    except OSError as exc:
        print(f"[错误] 报告写入失败：{exc}", file=sys.stderr)
        return 2
    print(f"[完成] {JSON_REPORT}")
    print(f"[完成] {MARKDOWN_REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
