"""对比 SymPy-only 与 Hybrid 校验器在错误样例上的捕获情况。"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


ROOT_DIR = Path(__file__).resolve().parents[2]
BENCHMARK_DIR = Path(__file__).resolve().parent
MAIN_FILE = ROOT_DIR / "agent" / "main.py"
PYTHON_FILE = ROOT_DIR / ".python312" / "python.exe"
ANNOTATIONS_FILE = BENCHMARK_DIR / "annotations.json"
STRUCTURAL_DIR = BENCHMARK_DIR / "structural_errors"
STRUCTURAL_CASES_FILE = STRUCTURAL_DIR / "cases.json"
OUTPUT_DIR = ROOT_DIR / "output"
JSON_OUTPUT = OUTPUT_DIR / "verifier_comparison.json"
MARKDOWN_OUTPUT = OUTPUT_DIR / "verifier_comparison.md"
TIMEOUT_SECONDS = 240
MODES = ("sympy-only", "hybrid")
# 新增：区分“明确判错”和“无法判定”，避免把 unknown/error 也计为有效捕获。
EXPLICIT_FAIL_STATUSES = {
    "not_equivalent", "shape_mismatch", "needs_human_review",
    "translation_failed",
}
UNDETERMINED_STATUSES = {"unknown", "error", "parse_error", "timeout"}


def _read_json(path: Path) -> Any:
    """读取 UTF-8 JSON，并把格式错误转成清晰异常。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取 JSON：{path}：{exc}") from exc


def _build_cases() -> list[dict]:
    """新增：动态合并 benchmark incorrect 与 structural 用例。"""
    annotations = _read_json(ANNOTATIONS_FILE)
    annotation_cases = annotations.get("cases", [])
    if not isinstance(annotation_cases, list):
        raise ValueError("annotations.json 的 cases 必须是数组")

    cases = []
    for item in annotation_cases:
        if item.get("category") != "incorrect":
            continue
        cases.append({
            "id": item["id"],
            "kind": "incorrect",
            "expected": "fail",
            "image": BENCHMARK_DIR / item["image"],
            "cpp": BENCHMARK_DIR / item["cpp"],
            "note": item.get("note", ""),
        })

    structural_cases = _read_json(STRUCTURAL_CASES_FILE)
    if not isinstance(structural_cases, list):
        raise ValueError("structural_errors/cases.json 必须是数组")
    for item in structural_cases:
        cases.append({
            "id": item["id"],
            "kind": "structural",
            "expected": item.get("expected", "fail"),
            "latex_file": STRUCTURAL_DIR / item["latex_file"],
            "cpp": STRUCTURAL_DIR / item["cpp"],
            "image": STRUCTURAL_DIR / item["image"],
            "note": item.get("note", ""),
        })

    return cases


def _command_for(case: dict, mode: str, out_dir: Path) -> list[str]:
    """生成单个用例的 main.py 子进程命令。"""
    command = [
        str(PYTHON_FILE),
        str(MAIN_FILE),
        "--verifier-mode",
        mode,
        "--json",
        "--out",
        str(out_dir),
        "-c",
        str(case["cpp"]),
    ]
    if case["kind"] == "incorrect":
        command += ["-i", str(case["image"])]
    else:
        latex_text = case["latex_file"].read_text(
            encoding="utf-8", errors="replace")
        command += ["-l", latex_text]
    return command


def _short_text(value: Any, limit: int = 280) -> str:
    """把错误文本压缩成适合表格和 JSON 的单行短文本。"""
    text = " ".join(str(value or "").split())
    return text[:limit]


def _redact_text(value: Any) -> str:
    """从错误文本中移除当前环境里的 API Key。"""
    text = str(value or "")
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if api_key:
        text = text.replace(api_key, "[已脱敏]")
    return text


def _extract_check_statuses(report: dict) -> list[str]:
    """提取每个校验步骤的名称与状态。"""
    statuses = []
    checks = report.get("steps", {}).get("checks", [])
    if not isinstance(checks, list):
        return statuses
    for check in checks:
        if not isinstance(check, dict):
            continue
        name = str(check.get("name", "check"))
        status = str(check.get("status", "unknown"))
        statuses.append(f"{name}:{status}")
    return statuses


def _classify_quality(statuses: list[str], ok: Any) -> str:
    """把最终结论分成 pass / explicit / undetermined / other。

    explicit 表示校验器给出了明确判错结论；undetermined 表示只能判
    unknown/error，不能作为“已捕获错误”的可行动证据。
    """
    status_values = {
        status.split(":", 1)[-1].strip() for status in statuses
    }
    if ok is True:
        return "pass"
    if ok is False:
        if status_values & EXPLICIT_FAIL_STATUSES:
            return "explicit"
        if status_values & UNDETERMINED_STATUSES:
            return "undetermined"
        return "other"
    return "invalid"


def _count_network_calls(report: dict) -> int:
    """统计报告中带 provider 且实际使用网络的校验器结果。"""
    count = 0

    def walk(value: Any) -> None:
        nonlocal count
        if isinstance(value, dict):
            if value.get("provider") and value.get("network_used") is True:
                count += 1
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(report)
    return count


def _run_one(case: dict, mode: str) -> dict:
    """运行一个用例和一个模式，并保留完整可审计结果。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    command = None
    out_dir = (
        OUTPUT_DIR / "_verifier_comparison_tmp" / f"{case['id']}_{mode}")
    out_dir.mkdir(parents=True, exist_ok=True)
    command = _command_for(case, mode, out_dir)
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
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
        return {
            "case_id": case["id"],
            "kind": case["kind"],
            "mode": mode,
            "verdict": "timeout",
            "captured": False,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "status": f"timeout>{TIMEOUT_SECONDS}s",
            "error": _short_text(exc),
            "returncode": None,
            "verifier_mode": None,
            "cache": {},
            "network_calls": 0,
            "command": command,
        }
    except OSError as exc:
        return {
            "case_id": case["id"],
            "kind": case["kind"],
            "mode": mode,
            "verdict": "error",
            "captured": False,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "status": "process_error",
            "error": _short_text(exc),
            "returncode": None,
            "verifier_mode": None,
            "cache": {},
            "network_calls": 0,
            "command": command,
        }

    elapsed = round(time.monotonic() - started, 3)
    report = None
    parse_error = ""
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        parse_error = f"stdout 不是有效 JSON：{exc}"

    if not isinstance(report, dict):
        report = None
    valid_report = report is not None
    if valid_report:
        ok = report.get("ok")
        if ok is True:
            verdict = "pass"
        elif ok is False:
            verdict = "fail"
        else:
            verdict = "invalid_report"
        captured = ok is False
        statuses = _extract_check_statuses(report)
        status = " | ".join(statuses) or _short_text(report.get("error"))
        quality = _classify_quality(statuses, ok)
        error_text = _short_text(_redact_text(report.get("error")))
        verifier_mode = report.get("verifier_mode")
        cache = report.get("cache", {})
        network_calls = _count_network_calls(report)
    else:
        verdict = "error"
        captured = False
        quality = "invalid"
        stderr_text = _redact_text(completed.stderr)
        if "请设置 DEEPSEEK_API_KEY" in stderr_text:
            status_detail = "DEEPSEEK_API_KEY 未设置"
        elif "DLL load failed" in stderr_text:
            status_detail = "Python 扩展 DLL 加载失败"
        else:
            status_detail = _short_text(parse_error or stderr_text, 180)
        status = f"returncode={completed.returncode} | {status_detail}"
        error_text = _short_text(
            f"{parse_error} | stderr: {stderr_text}", limit=800)
        verifier_mode = None
        cache = {}
        network_calls = 0

    return {
        "case_id": case["id"],
        "kind": case["kind"],
        "mode": mode,
        "verdict": verdict,
        "captured": captured,
        "verdict_quality": quality,
        "elapsed_seconds": elapsed,
        "status": status,
        "error": error_text,
        "returncode": completed.returncode,
        "verifier_mode": verifier_mode,
        "cache": cache,
        "network_calls": network_calls,
        "command": command,
        "stderr": _short_text(_redact_text(completed.stderr), limit=800),
    }


def _mode_summary(results: list[dict], mode: str) -> dict:
    """汇总单个模式的捕获率、耗时与网络调用。"""
    mode_results = [item for item in results if item["mode"] == mode]
    total = len(mode_results)
    captured = sum(1 for item in mode_results if item["captured"])
    explicit = sum(
        1 for item in mode_results
        if item.get("verdict_quality") == "explicit")
    undetermined = sum(
        1 for item in mode_results
        if item.get("verdict_quality") == "undetermined")
    elapsed_values = [float(item["elapsed_seconds"]) for item in mode_results]
    network_calls = sum(int(item["network_calls"]) for item in mode_results)
    return {
        "total": total,
        "captured": captured,
        "capture_rate": round(captured / total, 4) if total else 0.0,
        "explicit_captured": explicit,
        "explicit_rate": round(explicit / total, 4) if total else 0.0,
        "undetermined": undetermined,
        "average_elapsed_seconds": (
            round(sum(elapsed_values) / total, 3) if total else 0.0
        ),
        "network_calls": network_calls,
    }


def _build_conclusion(results: list[dict], mode_summaries: dict,
                      key_configured: bool) -> str:
    """根据实际结果生成不推测原因的结论段。"""
    by_case: dict[str, dict[str, dict]] = {}
    for item in results:
        by_case.setdefault(item["case_id"], {})[item["mode"]] = item

    only_hybrid = []
    only_sympy = []
    both_missed = []
    both_captured = []
    both_undetermined = []
    execution_problem = []
    for case_id, modes in by_case.items():
        sympy_item = modes.get("sympy-only", {})
        hybrid_item = modes.get("hybrid", {})
        sympy_quality = sympy_item.get("verdict_quality")
        hybrid_quality = hybrid_item.get("verdict_quality")
        sympy_captured = sympy_item.get("captured") is True
        hybrid_captured = hybrid_item.get("captured") is True
        sympy_verdict = sympy_item.get("verdict")
        hybrid_verdict = hybrid_item.get("verdict")
        if sympy_quality == "explicit" and hybrid_quality == "explicit":
            both_captured.append(case_id)
        elif sympy_quality == "explicit" and hybrid_captured:
            only_sympy.append(case_id)
        elif hybrid_quality == "explicit" and sympy_captured:
            only_hybrid.append(case_id)
        elif sympy_verdict == "pass" and hybrid_captured:
            only_hybrid.append(case_id)
        elif sympy_verdict == "pass" and hybrid_verdict == "pass":
            both_missed.append(case_id)
        elif (sympy_captured and hybrid_captured
              and sympy_quality == "undetermined"
              and hybrid_quality == "undetermined"):
            both_undetermined.append(case_id)
        elif sympy_verdict == "fail" and hybrid_verdict == "fail":
            # 都判 fail 但一方明确、一方无法判定时，归入明确判错的一方。
            if sympy_quality == "explicit":
                only_sympy.append(case_id)
            elif hybrid_quality == "explicit":
                only_hybrid.append(case_id)
            else:
                both_undetermined.append(case_id)
        else:
            execution_problem.append(case_id)

    only_hybrid = list(dict.fromkeys(only_hybrid))
    only_sympy = list(dict.fromkeys(only_sympy))

    lines = []
    if only_hybrid:
        lines.append(
            f"- 仅 hybrid 给出明确判错：{', '.join(only_hybrid)}。"
            "这是由本次报告中的实际状态差异直接得到。"
        )
    else:
        lines.append("- 仅 hybrid 明确判错：无。")
    if only_sympy:
        lines.append(
            f"- 仅 SymPy-only 给出明确判错：{', '.join(only_sympy)}。"
        )
    lines.append(
        f"- 两者都明确判错：{', '.join(both_captured) if both_captured else '无'}。"
    )
    lines.append(
        f"- 两者都只能判 unknown/error："
        f"{', '.join(both_undetermined) if both_undetermined else '无'}。"
    )
    lines.append(
        f"- 两者都漏：{', '.join(both_missed) if both_missed else '无'}。"
    )
    if execution_problem:
        lines.append(
            f"- 执行异常或报告缺失：{', '.join(execution_problem)}。"
        )

    lift = round(
        (mode_summaries["hybrid"]["explicit_rate"]
         - mode_summaries["sympy-only"]["explicit_rate"]) * 100,
        2,
    )
    lines.append(
        f"- 明确判错率提升：{lift:.2f} 个百分点；"
        f"hybrid 实际网络调用 {mode_summaries['hybrid']['network_calls']} 次。"
    )
    if not key_configured:
        lines.append(
            "- 本次运行未检测到 DEEPSEEK_API_KEY，hybrid 无法发起真实 DeepSeek "
            "网络校验；因此该结果不能代表完整的 Hybrid 能力。"
        )
    return "\n".join(lines)


def _describe_chain() -> list[str]:
    """新增（工单3）：报告本次实际会加载的异构校验器与模型。"""
    try:
        sys.path.insert(0, str(ROOT_DIR / "agent"))
        import config  # type: ignore
        import verifier_hetero  # type: ignore

        chain = verifier_hetero.build_verifier_chain(
            mode="serial", include_deepseek=True)
        names = getattr(chain, "verifier_names", ["sympy"])
        labels = []
        for name in names:
            if name == "sympy":
                labels.append("sympy")
                continue
            if name == "deepseek":
                labels.append(f"deepseek@{config.TEXT_MODEL}")
                continue
            if name == "qwen-vl":
                labels.append(f"qwen-vl@{config.QWEN_VERIFIER_MODEL}")
                continue
            if name == "glm-4v":
                labels.append(f"glm-4v@{config.GLM_VERIFIER_MODEL}")
                continue
            labels.append(str(name))
        return labels
    except Exception as exc:  # 仅用于报告展示，失败不影响主流程
        return [f"未知（{type(exc).__name__}）"]


def _write_markdown(payload: dict) -> None:
    """生成便于人工查看的逐例表格和汇总。"""
    lines = [
        "# SymPy-only 与 Hybrid 校验对比",
        "",
        f"- 生成时间：{payload['generated_at']}",
        f"- DEEPSEEK_API_KEY 已配置："
        f"{'是' if payload['api_key_configured'] else '否'}",
        f"- 子进程超时：{TIMEOUT_SECONDS}s",
        f"- Hybrid 校验链：{' + '.join(payload.get('verifier_chain') or ['sympy'])}",
        f"- 实际用例数：{payload['case_counts']['total']}",
        f"- benchmark incorrect：{payload['case_counts']['incorrect']}",
        f"- structural：{payload['case_counts']['structural']}",
        "",
        "## 逐例明细",
        "",
        "| 用例 | 模式 | 判定 | 判定质量 | 是否捕获 | 耗时(s) | 关键状态 |",
        "|---|---|---|---|---|---:|---|",
    ]
    quality_labels = {
        "pass": "通过",
        "explicit": "明确判错",
        "undetermined": "无法判定",
        "other": "其他",
        "invalid": "无效",
    }
    for item in payload["results"]:
        captured_text = "是" if item["captured"] else "否"
        quality_text = quality_labels.get(
            item.get("verdict_quality"), item.get("verdict_quality") or "-")
        status = _short_text(
            item.get("status") or item.get("error"), 180
        ).replace("|", "\\|")
        lines.append(
            f"| {item['case_id']} | {item['mode']} | {item['verdict']} | "
            f"{quality_text} | {captured_text} | "
            f"{item['elapsed_seconds']:.3f} | {status} |"
        )

    sympy_summary = payload["summary"]["sympy-only"]
    hybrid_summary = payload["summary"]["hybrid"]
    lift = round(
        (hybrid_summary["explicit_rate"]
         - sympy_summary["explicit_rate"]) * 100,
        2,
    )
    lines += [
        "",
        "## 汇总",
        "",
        "| 模式 | 捕获数 | 明确判错 | 明确判错率 | 无法判定 | "
        "用例数 | 平均耗时(s) | 网络调用次数 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| sympy-only | {sympy_summary['captured']} | "
        f"{sympy_summary['explicit_captured']} | "
        f"{sympy_summary['explicit_rate']:.2%} | "
        f"{sympy_summary['undetermined']} | "
        f"{sympy_summary['total']} | "
        f"{sympy_summary['average_elapsed_seconds']:.3f} | "
        f"{sympy_summary['network_calls']} |",
        f"| hybrid | {hybrid_summary['captured']} | "
        f"{hybrid_summary['explicit_captured']} | "
        f"{hybrid_summary['explicit_rate']:.2%} | "
        f"{hybrid_summary['undetermined']} | "
        f"{hybrid_summary['total']} | "
        f"{hybrid_summary['average_elapsed_seconds']:.3f} | "
        f"{hybrid_summary['network_calls']} |",
        "",
        f"Hybrid 明确判错率相对 SymPy-only 提升：**{lift:.2f} 个百分点**。",
        "",
        "## 结论",
        "",
        payload["conclusion"],
        "",
    ]
    MARKDOWN_OUTPUT.write_text("\n".join(lines), encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    """执行完整对比并写出 JSON/Markdown 报告。"""
    global JSON_OUTPUT, MARKDOWN_OUTPUT
    parser = argparse.ArgumentParser(
        description="对比 SymPy-only 与 Hybrid 校验器在错误样例上的捕获情况")
    parser.add_argument("--only", default="",
                        help="逗号分隔的用例 id，只跑这些用例（默认全部）")
    parser.add_argument("--json-out", default=str(JSON_OUTPUT),
                        help="对比 JSON 输出路径")
    parser.add_argument("--md-out", default=str(MARKDOWN_OUTPUT),
                        help="对比 Markdown 输出路径")
    args = parser.parse_args(argv)

    if not PYTHON_FILE.is_file():
        print(f"项目 Python 不存在：{PYTHON_FILE}", file=sys.stderr)
        return 2
    if not MAIN_FILE.is_file():
        print(f"agent/main.py 不存在：{MAIN_FILE}", file=sys.stderr)
        return 2

    cases = _build_cases()
    selected = [item.strip() for item in args.only.split(",") if item.strip()]
    if selected:
        known = {case["id"] for case in cases}
        unknown_ids = [item for item in selected if item not in known]
        if unknown_ids:
            print(f"未知用例 id：{','.join(unknown_ids)}", file=sys.stderr)
            return 2
        cases = [case for case in cases if case["id"] in selected]
    JSON_OUTPUT = Path(args.json_out)
    MARKDOWN_OUTPUT = Path(args.md_out)

    api_key_configured = bool(os.environ.get("DEEPSEEK_API_KEY", "").strip())
    chain_labels = _describe_chain()
    results = []
    for case in cases:
        for mode in MODES:
            print(f"[运行] {case['id']} / {mode}", flush=True)
            results.append(_run_one(case, mode))

    summaries = {
        mode: _mode_summary(results, mode)
        for mode in MODES
    }
    conclusion = _build_conclusion(results, summaries, api_key_configured)
    serialized_cases = []
    for case in cases:
        serialized_case = {
            key: (str(value) if isinstance(value, Path) else value)
            for key, value in case.items()
        }
        serialized_cases.append(serialized_case)
    # 新增：记录每类实际加载数量，新增或为空时无需同步修改常量。
    case_counts = {
        "incorrect": sum(
            1 for case in cases if case.get("kind") == "incorrect"),
        "structural": sum(
            1 for case in cases if case.get("kind") == "structural"),
        "total": len(cases),
    }
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "api_key_configured": api_key_configured,
        "timeout_seconds": TIMEOUT_SECONDS,
        "case_counts": case_counts,
        # 新增（工单3）：记录本次 hybrid 实际加载的校验器构成。
        "verifier_chain": chain_labels,
        "cases": serialized_cases,
        "results": results,
        "summary": summaries,
        "conclusion": conclusion,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    MARKDOWN_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_markdown(payload)
    print(f"[完成] {JSON_OUTPUT}")
    print(f"[完成] {MARKDOWN_OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
