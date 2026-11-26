#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工单4：数学公式 ↔ C++ 校验器的 Gradio Web 界面。

设计约束：
1. 只做展示与编排，判定逻辑全部复用 agent/main.py 的 Pipeline，不重写；
2. 左侧拖图上传 + 模式选择，右侧展示 LaTeX / 翻译 / 结论 / 耗时 / 缓存状态；
3. 底部保留历史记录，并支持一次上传多张图批量校验；
4. 启动地址固定 127.0.0.1:7860（http://localhost:7860）。
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import gradio as gr  # noqa: E402

OUTPUT_DIR = BASE / "output"
HISTORY_PATH = OUTPUT_DIR / "web_history.json"
EXPORT_DIR = OUTPUT_DIR / "web_exports"
HISTORY_HEADERS = [
    "时间", "图片", "结论", "状态", "耗时(秒)", "OCR 链路", "校验模式", "判定依据",
]
EXPORT_HEADERS = [
    "时间", "图片名", "结论", "状态", "耗时", "OCR链路", "校验模式",
    "判定依据", "错误", "解释", "explain",
]
# 结论徽标：绿✅通过 / 红❌不通过 / 黄⚠️需人工确认。
VERDICT_STYLE = {
    "pass": ("✅", "等价（通过）"),
    "fail": ("❌", "不等价（未通过）"),
    "warn": ("⚠️", "需人工确认"),
    "skip": ("⚠️", "非公式 / 已跳过"),
    "error": ("⚠️", "执行异常"),
}
CONCLUSION_LABELS = {
    "pass": "通过",
    "fail": "不通过",
    "warn": "待人工",
    "skip": "待人工",
    "error": "异常",
}

_PIPELINE_CACHE: dict[str, object] = {}
_MAX_HISTORY = 200
HISTORY_LOCK = threading.Lock()
_HISTORY: list[dict[str, Any]] = []
_BATCH_RESULTS: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Pipeline 接入（懒加载，避免界面启动时依赖 API Key）
# ---------------------------------------------------------------------------
def get_pipeline(engine: str, verifier: str):
    """按 (OCR 链路, 校验模式) 复用 Pipeline 实例。"""
    key = f"{engine}|{verifier}"
    if key in _PIPELINE_CACHE:
        return _PIPELINE_CACHE[key]
    from agent.main import Pipeline  # 延迟导入：缺少 Key 时不阻塞界面启动

    pipeline = Pipeline(
        engine="auto" if engine == "auto" else engine,
        use_llm=True,
        verifier_mode=verifier,
    )
    _PIPELINE_CACHE[key] = pipeline
    return pipeline


# ---------------------------------------------------------------------------
# 报告 -> 界面字段
# ---------------------------------------------------------------------------
def _check_status(report: dict) -> str:
    """取 latex_vs_cpp 的校验状态，没有则返回空串。"""
    for check in (report.get("steps") or {}).get("checks") or []:
        if isinstance(check, dict) and check.get("name") == "latex_vs_cpp":
            return str(check.get("status") or "")
    return ""


def _verdict_of(report: dict) -> str:
    """把流水线报告折叠成 pass / fail / warn / skip / error。"""
    if report.get("error") and not (
            (report.get("steps") or {}).get("latex") or {}
    ).get("text"):
        return "skip"
    if report.get("error") and not _check_status(report):
        return "error"
    status = _check_status(report)
    if status == "equivalent":
        return "pass"
    if status == "not_equivalent":
        return "fail"
    if status in {"unknown", "translation_failed", "parse_error"}:
        return "warn"
    if report.get("ok") is True:
        return "pass"
    return "warn"


def _conclusion_markdown(report: dict) -> str:
    """生成右侧顶部的结论卡片。"""
    verdict = _verdict_of(report)
    icon, label = VERDICT_STYLE[verdict]
    elapsed = report.get("elapsed")
    engine = report.get("engine") or "-"
    mode = report.get("verifier_mode") or "-"
    status = _check_status(report) or "无校验结果"
    lines = [
        f"## {icon} {label}",
        "",
        f"- 校验状态：`{status}`",
        f"- 耗时：`{elapsed}s`" if elapsed is not None else "- 耗时：`-`",
        f"- OCR 链路 / 校验模式：`{engine}` / `{mode}`",
    ]
    if _explain_of(report):
        lines.append("- 批改说明：已生成（见右侧「批改说明」面板）")
    if report.get("error"):
        lines.append(f"- 说明：{report['error']}")
    return "\n".join(lines)


def _cache_text(report: dict) -> str:
    stats = report.get("cache") or {}
    if not stats:
        return "-"
    if stats.get("disabled"):
        return "缓存已禁用"
    ocr = stats.get("ocr") or {}
    verify = stats.get("verify") or {}
    translate = stats.get("translate") or {}
    return (
        f"OCR 命中 {ocr.get('hits', 0)}/{ocr.get('hits', 0) + ocr.get('misses', 0)}；"
        f"校验命中 {verify.get('hits', 0)}/"
        f"{verify.get('hits', 0) + verify.get('misses', 0)}；"
        f"翻译命中 {translate.get('hits', 0)}/"
        f"{translate.get('hits', 0) + translate.get('misses', 0)}"
    )


def _detail_text(report: dict) -> str:
    """展示判定依据：优先取校验详情，其次取错误信息。"""
    for check in (report.get("steps") or {}).get("checks") or []:
        if isinstance(check, dict) and check.get("name") == "latex_vs_cpp":
            detail = str(check.get("detail") or "")
            index_check = str((check.get("meta") or {}).get("index_check") or "")
            text = detail
            if index_check:
                text += f"\n索引检查：{index_check}"
            return text or "-"
    return str(report.get("error") or "-")


def _latex_text(report: dict) -> str:
    step = (report.get("steps") or {}).get("latex") or {}
    return str(step.get("text") or "")


def _translation_text(report: dict) -> str:
    step = (report.get("steps") or {}).get("translate_cpp") or {}
    return str(step.get("ref_latex") or "")


def _explain_of(report: dict) -> dict:
    """取出第一条批改说明（结构化字段），没有则返回空字典。"""
    for check in (report.get("steps") or {}).get("checks") or []:
        if isinstance(check, dict) and isinstance(check.get("explain"), dict):
            return check["explain"]
    return {}


def _explain_summary(report: dict) -> str:
    """把批改说明压成一行，供历史/CSV 使用。"""
    explanation = _explain_of(report)
    if not explanation:
        return ""
    label = explanation.get("error_type_label") or explanation.get("error_type")
    location = str(explanation.get("location") or "")
    confidence = explanation.get("confidence")
    parts = [str(label or "")]
    if location:
        parts.append(f"位置：{location[:80]}")
    if confidence is not None:
        parts.append(f"置信度：{confidence}")
    if explanation.get("needs_human_review"):
        parts.append("需人工复核")
    return "；".join(parts)


def _explanation_markdown(explanation: dict) -> str:
    """渲染「批改说明」折叠面板内容：徽标 + 定位 + 建议 + 置信度。"""
    if not isinstance(explanation, dict) or not explanation:
        return "_当前没有批改说明（仅不对等结论会生成解释）。_"
    label = html.escape(str(
        explanation.get("error_type_label") or explanation.get("error_type")
        or "未归类"))
    error_type = html.escape(str(explanation.get("error_type") or "unknown"))
    # 按错误类型选择徽标配色，便于快速扫读。
    palette = {
        "sign_error": ("#fee2e2", "#991b1b"),
        "index_scope": ("#fef3c7", "#92400e"),
        "off_by_one": ("#fef3c7", "#92400e"),
        "sum_bounds": ("#fef3c7", "#92400e"),
        "transpose_error": ("#ede9fe", "#5b21b6"),
        "pde_capability": ("#e0f2fe", "#075985"),
        "symbolic_scope_limit": ("#e0f2fe", "#075985"),
        "parser_limitation": ("#e0f2fe", "#075985"),
        "notation_variant": ("#e5e7eb", "#374151"),
    }
    background, color = palette.get(
        str(explanation.get("error_type") or ""), ("#f3e8ff", "#6b21a8"))
    confidence = explanation.get("confidence")
    try:
        percent = f"{float(confidence) * 100:.0f}%"
    except (TypeError, ValueError):
        percent = "-"
    review = "⚠️ 需人工复核" if explanation.get("needs_human_review") else "✅ 可自动采纳"
    lines = [
        f'<span style="background:{background};color:{color};'
        f'padding:2px 10px;border-radius:10px;font-weight:600;">{label}</span> '
        f'<code>{error_type}</code>',
        "",
        f"- **定位**：`{html.escape(str(explanation.get('location') or '-'))}`",
        f"- **置信度**：`{percent}`",
        f"- **复核标记**：{review}",
        f"- **根因层**：`{html.escape(str(explanation.get('root_cause_layer') or '-'))}`",
    ]
    if explanation.get("pde_boundary"):
        lines.append("- **能力边界**：连续 PDE 与离散实现不可自动证明等价，已按人工复核处理。")
    suggestion = str(explanation.get("suggestion") or "").strip()
    if suggestion:
        lines += ["", "**修改建议**：", "", suggestion]
    if explanation.get("source"):
        lines += [
            "",
            f"<sub>来源：{html.escape(str(explanation.get('source')))}"
            f" · 模型：{html.escape(str(explanation.get('model') or '-'))}</sub>",
        ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 历史记录与导出
# ---------------------------------------------------------------------------
def _truncate_text(value: Any, limit: int = 200) -> str:
    """把文本限制在指定字符数内，避免表格和导出文件被长依据撑大。"""
    text = str(value or "").strip()
    if not text:
        return "-"
    return text if len(text) <= limit else text[:limit]


def _normalize_history_record(record: Any) -> dict[str, Any] | None:
    """兼容旧版六列表格记录，并把历史记录统一为完整字典。"""
    if isinstance(record, dict):
        normalized = {
            "timestamp": record.get("timestamp") or record.get("时间") or "-",
            "image_name": record.get("image_name") or record.get("图片名")
            or record.get("图片") or "-",
            "conclusion": record.get("conclusion") or record.get("结论") or "",
            "status": record.get("status") or record.get("状态") or "-",
            "elapsed": record.get("elapsed") or record.get("耗时") or "-",
            "ocr_chain": record.get("ocr_chain") or record.get("OCR链路")
            or record.get("OCR 链路") or "-",
            "verifier_mode": record.get("verifier_mode") or record.get("校验模式")
            or "-",
            "basis": record.get("basis") or record.get("判定依据") or "-",
            "error": record.get("error") or record.get("错误") or "",
            "verdict": record.get("verdict") or "",
            # 工单2（w11）：批改说明的展示文本与结构化 JSON。
            "explain": record.get("explain") or "",
            "explain_detail": record.get("explain_detail") or {},
        }
    elif isinstance(record, (list, tuple)):
        values = list(record)
        if len(values) >= 9:
            normalized = {
                "timestamp": values[0],
                "image_name": values[1],
                "conclusion": values[2],
                "status": values[3],
                "elapsed": values[4],
                "ocr_chain": values[5],
                "verifier_mode": values[6],
                "basis": values[7],
                "error": values[8],
                "verdict": "",
                "explain": values[9] if len(values) > 9 else "",
                "explain_detail": {},
            }
        elif len(values) >= 6:
            # 修改：兼容旧版 [时间, 图片, 结论, 耗时, OCR, 模式] 记录。
            normalized = {
                "timestamp": values[0],
                "image_name": values[1],
                "conclusion": values[2],
                "status": "-",
                "elapsed": values[3],
                "ocr_chain": values[4],
                "verifier_mode": values[5],
                "basis": "-",
                "error": "",
                "verdict": "",
                "explain": "",
                "explain_detail": {},
            }
        else:
            return None
    else:
        return None

    verdict = str(normalized["verdict"] or "").strip()
    conclusion = str(normalized["conclusion"] or "").strip()
    if verdict not in CONCLUSION_LABELS:
        for candidate, label in CONCLUSION_LABELS.items():
            if conclusion.endswith(label):
                verdict = candidate
                break
    if verdict not in CONCLUSION_LABELS:
        verdict = "warn"
    normalized["verdict"] = verdict
    if not conclusion:
        normalized["conclusion"] = CONCLUSION_LABELS[verdict]
    normalized["basis"] = _truncate_text(normalized["basis"])
    return normalized


def load_history_records(
        path: Path | str | None = None) -> list[dict[str, Any]]:
    """从磁盘加载历史；文件不存在或内容损坏时返回空列表。"""
    history_path = Path(HISTORY_PATH if path is None else path)
    try:
        payload = json.loads(history_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"[web] 历史文件读取失败，已按空历史启动：{exc}")
        return []
    if not isinstance(payload, list):
        print("[web] 历史文件格式不是列表，已按空历史启动")
        return []

    records = []
    for item in payload:
        normalized = _normalize_history_record(item)
        if normalized is not None:
            records.append(normalized)
        if len(records) >= _MAX_HISTORY:
            break
    return records


def _persist_history_unlocked() -> None:
    """在持有 HISTORY_LOCK 时原子写入历史文件。"""
    history_path = Path(HISTORY_PATH)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = history_path.with_name(history_path.name + ".tmp")
    temporary_path.write_text(
        json.dumps(_HISTORY, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary_path, history_path)


def _history_display_rows(
    records: list[dict[str, Any]] | None = None,
) -> list[list[Any]]:
    """把历史字典转换成界面表格行，判定依据最多显示 200 字。"""
    source = _HISTORY if records is None else records
    return [
        [
            record.get("timestamp", "-"),
            record.get("image_name", "-"),
            record.get("conclusion", "-"),
            record.get("status", "-"),
            record.get("elapsed", "-"),
            record.get("ocr_chain", "-"),
            record.get("verifier_mode", "-"),
            _truncate_text(record.get("basis"), 200),
        ]
        for record in source
    ]


def _append_history_record(record: dict[str, Any]) -> dict[str, Any] | None:
    """追加一条历史记录并在锁内原子持久化，最多保留 200 条。"""
    normalized = _normalize_history_record(record)
    if normalized is None:
        return None
    with HISTORY_LOCK:
        _HISTORY.insert(0, normalized)
        del _HISTORY[_MAX_HISTORY:]
        try:
            _persist_history_unlocked()
        except OSError as exc:
            print(f"[web] 历史写入失败，记录仅保留在内存：{exc}")
    return normalized


def get_history_rows() -> list[list[Any]]:
    """返回当前历史表格快照。"""
    with HISTORY_LOCK:
        return _history_display_rows(list(_HISTORY))


def clear_history() -> tuple[str, list[list[Any]]]:
    """清空内存中的历史与当前批量结果，并把历史文件写成空列表。"""
    with HISTORY_LOCK:
        _HISTORY.clear()
        _BATCH_RESULTS.clear()
        try:
            _persist_history_unlocked()
        except OSError as exc:
            print(f"[web] 历史文件清空失败：{exc}")
            return f"内存历史已清空，但文件写入失败：{exc}", []
    return "历史记录已清空。", []


def _ocr_chain(report: dict, fallback: str) -> str:
    """优先读取 OCR 步骤记录的真实链路，缺失时使用界面选择值。"""
    ocr_step = (report.get("steps") or {}).get("ocr") or {}
    meta = ocr_step.get("meta") or {}
    return str(meta.get("engine_chain") or report.get("engine") or fallback or "-")


def _make_history_record(
    report: dict,
    image_path: Any,
    engine: str,
    verifier: str,
    *,
    error: str = "",
    elapsed: Any = None,
) -> dict[str, Any]:
    """把一次 Pipeline 报告转换成可展示、可持久化的完整记录。"""
    verdict = _verdict_of(report)
    basis = _detail_text(report)
    error_text = str(error or report.get("error") or "")
    if basis == "-" and error_text:
        basis = error_text
    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "image_name": Path(str(image_path)).name if image_path else "-",
        "conclusion": CONCLUSION_LABELS[verdict],
        "status": _check_status(report) or ("exception" if error_text else verdict),
        "elapsed": report.get("elapsed", elapsed if elapsed is not None else "-"),
        "ocr_chain": _ocr_chain(report, engine),
        "verifier_mode": report.get("verifier_mode") or verifier or "-",
        "basis": _truncate_text(basis),
        "error": error_text,
        "verdict": verdict,
        # 工单2（w11）：结构化批改说明 + 单行摘要。
        "explain": _explain_summary(report),
        "explain_detail": _explain_of(report),
    }


def _record_to_export_row(record: dict[str, Any], *,
                          structured: bool = True) -> dict[str, Any]:
    """按需求固定导出字段名和顺序；explain 字段保留结构化内容。"""
    normalized = _normalize_history_record(record) or {}
    detail = normalized.get("explain_detail") or {}
    if not isinstance(detail, dict):
        detail = {}
    explain_value: Any = detail if structured else json.dumps(
        detail, ensure_ascii=False)
    return {
        "时间": normalized.get("timestamp", "-"),
        "图片名": normalized.get("image_name", "-"),
        "结论": normalized.get("conclusion", "-"),
        "状态": normalized.get("status", "-"),
        "耗时": normalized.get("elapsed", "-"),
        "OCR链路": normalized.get("ocr_chain", "-"),
        "校验模式": normalized.get("verifier_mode", "-"),
        "判定依据": _truncate_text(normalized.get("basis"), 200),
        "错误": normalized.get("error", ""),
        "解释": normalized.get("explain", ""),
        "explain": explain_value,
    }


def _collect_export_records() -> list[dict[str, Any]]:
    """合并当前批量与历史，同一记录只导出一次。"""
    with HISTORY_LOCK:
        batch_records = list(_BATCH_RESULTS)
        batch_ids = {id(record) for record in batch_records}
        history_records = [
            record for record in _HISTORY if id(record) not in batch_ids
        ]
    return batch_records + history_records


def export_records(
    export_format: str,
    records: list[dict[str, Any]] | None = None,
    export_dir: Path | str | None = None,
) -> tuple[str, str | None]:
    """导出 JSON/CSV；没有数据时直接返回提示，不创建空文件。

    修改（工单4）：export_dir 在调用时解析，避免测试重定向 EXPORT_DIR 后
    仍然写入模块导入时的旧路径。
    """
    if export_dir is None:
        export_dir = EXPORT_DIR
    normalized_format = export_format.lower().strip()
    if normalized_format not in {"json", "csv"}:
        return "不支持的导出格式。", None
    source = _collect_export_records() if records is None else records
    rows = [
        _record_to_export_row(record, structured=normalized_format == "json")
        for record in source
        if _normalize_history_record(record) is not None
    ]
    if not rows:
        return "当前没有可导出的批量或历史数据，未生成文件。", None

    export_path = Path(export_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    target = export_path / f"web_export_{timestamp}.{normalized_format}"
    temporary = target.with_name(target.name + ".tmp")
    try:
        export_path.mkdir(parents=True, exist_ok=True)
        if normalized_format == "json":
            temporary.write_text(
                json.dumps(rows, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        else:
            with temporary.open("w", encoding="utf-8-sig", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=EXPORT_HEADERS)
                writer.writeheader()
                writer.writerows(rows)
        os.replace(temporary, target)
    except OSError as exc:
        print(f"[web] 导出失败：{exc}")
        return f"导出失败：{exc}", None
    return f"已生成 {target.name}", str(target)


def export_current_json() -> tuple[str, str | None]:
    """导出当前批量结果与历史为 JSON。"""
    return export_records("json")


def export_current_csv() -> tuple[str, str | None]:
    """导出当前批量结果与历史为 CSV。"""
    return export_records("csv")


# 新增：界面导入时加载历史；损坏或不存在都按空历史处理。
_HISTORY.extend(load_history_records(HISTORY_PATH))


# ---------------------------------------------------------------------------
# 事件处理
# ---------------------------------------------------------------------------
def run_single(
    image_path: str | None,
    cpp_path: str | None,
    engine: str,
    verifier: str,
    progress=gr.Progress(),
) -> Iterator[tuple[Any, ...]]:
    """单张校验：通过 generator 展示处理中状态，结束后追加历史。"""
    if not image_path:
        yield (
            "## ⚠️ 请先上传图片",
            "",
            "",
            "-",
            "-",
            "未收到图片",
            get_history_rows(),
            _explanation_markdown({}),
        )
        return
    progress(0.05, desc="准备校验")
    yield (
        f"## ⏳ 正在处理：{Path(str(image_path)).name}",
        "",
        "",
        "-",
        "-",
        "校验进行中，请稍候...",
        get_history_rows(),
        "_批改说明将在判定完成后生成。_",
    )
    started = time.time()
    try:
        pipeline = get_pipeline(engine, verifier)
        report = pipeline.run(image=image_path, cpp_path=cpp_path or None)
    except Exception as exc:  # 界面不因单次失败中断
        report = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed": round(time.time() - started, 2),
            "steps": {},
            "engine": engine,
            "verifier_mode": verifier,
            "cache": {},
        }
        print("[web] 单张校验异常：\n" + traceback.format_exc())

    record = _make_history_record(
        report,
        image_path,
        engine,
        verifier,
        error=str(report.get("error") or ""),
    )
    _append_history_record(record)
    progress(1.0, desc="校验完成")
    yield (
        _conclusion_markdown(report),
        _latex_text(report),
        _translation_text(report),
        str(report.get("elapsed", "-")),
        _cache_text(report),
        _detail_text(report),
        get_history_rows(),
        _explanation_markdown(_explain_of(report)),
    )


def progress_bar_html(current: int, total: int, label: str = "") -> str:
    """工单4：生成带“当前/总数 + 百分比”的批量进度条。"""
    try:
        total_value = max(int(total or 0), 0)
    except (TypeError, ValueError):
        total_value = 0
    try:
        current_value = int(current or 0)
    except (TypeError, ValueError):
        current_value = 0
    if total_value <= 0:
        current_value = 0
        percent = 0
    else:
        current_value = max(0, min(current_value, total_value))
        percent = round(current_value * 100 / total_value)
    color = "#16a34a" if percent >= 100 else "#2563eb"
    safe_label = html.escape(str(label or ""))
    return (
        '<div style="margin:6px 0 4px 0;">'
        '<div style="width:100%;height:16px;background:#e5e7eb;'
        'border-radius:8px;overflow:hidden;">'
        f'<div style="width:{percent}%;height:100%;background:{color};'
        'transition:width .25s ease;"></div>'
        '</div>'
        '<div style="font-size:13px;color:#374151;margin-top:4px;">'
        f'已完成 {current_value}/{total_value}（{percent}%）'
        + (f' · {safe_label}' if safe_label else "")
        + '</div></div>'
    )


def _batch_summary(records: list[dict[str, Any]], elapsed: float) -> str:
    """统计通过、不通过、待人工和异常数量。"""
    counts = {"pass": 0, "fail": 0, "warn": 0, "skip": 0, "error": 0}
    for record in records:
        verdict = str(record.get("verdict") or "warn")
        counts[verdict] = counts.get(verdict, 0) + 1
    return (
        f"批量完成：通过 {counts['pass']} / 不通过 {counts['fail']} / "
        f"待人工 {counts['warn'] + counts['skip']} / 异常 {counts['error']}；"
        f"总耗时 {elapsed:.2f} 秒"
    )


def run_batch(
    files: Any,
    cpp_path: str | None,
    engine: str,
    verifier: str,
    progress=gr.Progress(),
) -> Iterator[tuple[Any, ...]]:
    """批量校验：逐张执行，实时追加结果，单张失败不中断整批。"""
    if not files:
        yield (
            progress_bar_html(0, 0, "等待批量处理"),
            "请先选择至少一张图片。",
            [],
            get_history_rows(),
            "尚未开始批量处理。",
        )
        return
    file_list = files if isinstance(files, (list, tuple)) else [files]
    rows: list[list[Any]] = []
    total = len(file_list)
    batch_started = time.time()
    batch_records: list[dict[str, Any]] = []
    with HISTORY_LOCK:
        _BATCH_RESULTS.clear()

    for index, item in enumerate(file_list, start=1):
        path = getattr(item, "name", None) or str(item)
        image_name = Path(str(path)).name
        progress(
            (index - 1) / max(1, total),
            desc=f"正在处理 {index}/{total}：{image_name}",
        )
        yield (
            progress_bar_html(
                index - 1, total, f"正在处理：{image_name}"),
            f"正在处理 {index}/{total}：{image_name}",
            list(rows),
            get_history_rows(),
            f"已完成 {index - 1}/{total}"
            f"（{round((index - 1) * 100 / max(1, total))}%）。",
        )
        started = time.time()
        # 新增：批量模式下若图片同目录存在同名 .cpp，自动作为该图的参考代码，
        # 这样“多图+多代码”成对校验也能在批量区一次跑完。
        effective_cpp = cpp_path
        if not effective_cpp:
            sibling_cpp = Path(str(path)).with_suffix(".cpp")
            if sibling_cpp.is_file():
                effective_cpp = str(sibling_cpp)
        try:
            pipeline = get_pipeline(engine, verifier)
            report = pipeline.run(image=path, cpp_path=effective_cpp or None)
            error_text = str(report.get("error") or "")
        except Exception as exc:
            elapsed = round(time.time() - started, 2)
            error_text = f"{type(exc).__name__}: {exc}"
            report = {
                "ok": False,
                "error": error_text,
                "elapsed": elapsed,
                "steps": {},
                "engine": engine,
                "verifier_mode": verifier,
                "cache": {},
            }
            print(f"[web] 批量图片处理异常（{image_name}）：\n{traceback.format_exc()}")

        record = _make_history_record(
            report, path, engine, verifier, error=error_text
        )
        stored_record = _append_history_record(record)
        if stored_record is not None:
            batch_records.append(stored_record)
            with HISTORY_LOCK:
                _BATCH_RESULTS.append(stored_record)
        rows.append(_history_display_rows([record])[0])
        progress(
            index / max(1, total),
            desc=f"已完成 {index}/{total}：{image_name}",
        )
        yield (
            progress_bar_html(
                index, total, f"已完成：{image_name}"),
            f"已完成 {index}/{total}：{image_name}"
            f"（{round(index * 100 / max(1, total))}%）",
            list(rows),
            get_history_rows(),
            f"当前通过 {sum(1 for row in batch_records if row['verdict'] == 'pass')}，"
            f"异常 {sum(1 for row in batch_records if row['verdict'] == 'error')}。",
        )

    total_elapsed = time.time() - batch_started
    progress(1.0, desc="批量完成")
    yield (
        progress_bar_html(total, total, "批量处理完成"),
        "批量处理完成。",
        list(rows),
        get_history_rows(),
        _batch_summary(batch_records, total_elapsed),
    )


def clear_all():
    """清空输入与右侧展示区，历史记录保留。"""
    return (None, None, None, "", "", "-", "-", "-", get_history_rows(),
            _explanation_markdown({}))


def update_preview(image_path: str | None) -> str | None:
    """把新上传的图片同步到右侧大图预览区。"""
    return image_path or None


def zoom_preview():
    """切换为大图高度，模拟 100% 查看。"""
    return gr.update(height=900)


def fit_preview():
    """恢复为适合窗口的预览高度。"""
    return gr.update(height=520)


# ---------------------------------------------------------------------------
# 界面
# ---------------------------------------------------------------------------
def build_demo() -> "gr.Blocks":
    with gr.Blocks(title="数学公式 ↔ C++ 校验器") as demo:
        gr.Markdown(
            "# 数学公式 ↔ C++ 校验器\n"
            "拖入公式图片（可选附参考 C++ 文件），系统完成 OCR → LaTeX → "
            "代码翻译 → 等价性判定。"
        )
        with gr.Row():
            with gr.Column(scale=1):
                image_input = gr.Image(
                    label="公式图片（可拖拽上传）",
                    type="filepath",
                    sources=["upload"],
                    height=300,
                )
                cpp_input = gr.File(
                    label="参考 C++ 源码（可选）",
                    file_types=[".cpp", ".cc", ".h", ".hpp", ".txt"],
                    type="filepath",
                )
                engine_radio = gr.Radio(
                    ["auto", "vision"], value="auto", label="OCR 链路")
                verifier_radio = gr.Radio(
                    ["hybrid", "auto", "sympy-only"], value="hybrid",
                    label="校验模式")
                with gr.Row():
                    run_button = gr.Button("开始校验", variant="primary")
                    clear_button = gr.Button("清空")

            with gr.Column(scale=2):
                with gr.Row():
                    zoom_button = gr.Button("100% 放大")
                    fit_button = gr.Button("适应窗口")
                image_preview = gr.Image(
                    label="公式图片预览",
                    type="filepath",
                    interactive=False,
                    height=520,
                )
                conclusion_box = gr.Markdown("## ⚠️ 等待输入")
                latex_box = gr.Textbox(label="OCR / LaTeX", lines=3)
                translation_box = gr.Textbox(label="代码翻译（LaTeX）", lines=3)
                with gr.Row():
                    elapsed_box = gr.Textbox(label="耗时(秒)", scale=1)
                    cache_box = gr.Textbox(label="缓存状态", scale=3)
                detail_box = gr.Textbox(label="判定依据", lines=4)
                # 工单2（w11）：可解释批改折叠面板。
                with gr.Accordion("批改说明（可解释批改）", open=True):
                    explanation_box = gr.Markdown(
                        "_只有判为不等价的用例才会生成批改说明。_")

        with gr.Accordion("批量上传（多图）", open=False):
            batch_files = gr.Files(
                label="一次选择多张图片", file_types=["image"])
            batch_button = gr.Button("批量校验")
            # 工单4：显式进度条，显示 当前/总数 与百分比。
            batch_progress = gr.HTML(
                value=progress_bar_html(0, 0, "等待批量处理"),
                label="批量进度",
            )
            batch_status = gr.Markdown("### 等待批量处理")
            batch_output = gr.Dataframe(
                headers=HISTORY_HEADERS,
                value=[],
                interactive=False,
                wrap=True,
            )
            batch_summary = gr.Markdown("尚未开始批量处理。")

        with gr.Row():
            export_json_button = gr.Button("导出 JSON")
            export_csv_button = gr.Button("导出 CSV")
        export_message = gr.Markdown("导出文件将写入 output/web_exports/。")
        export_file = gr.File(
            label="下载导出文件",
            file_count="single",
            interactive=False,
        )

        with gr.Row():
            gr.Markdown("### 历史记录")
            clear_history_button = gr.Button("清空历史")
        history_box = gr.Dataframe(
            headers=HISTORY_HEADERS,
            value=get_history_rows(),
            interactive=False,
            wrap=True,
        )

        image_input.change(
            fn=update_preview,
            inputs=[image_input],
            outputs=[image_preview],
            show_progress="hidden",
        )
        zoom_button.click(
            fn=zoom_preview,
            inputs=None,
            outputs=[image_preview],
            show_progress="hidden",
        )
        fit_button.click(
            fn=fit_preview,
            inputs=None,
            outputs=[image_preview],
            show_progress="hidden",
        )
        run_button.click(
            fn=run_single,
            inputs=[image_input, cpp_input, engine_radio, verifier_radio],
            outputs=[conclusion_box, latex_box, translation_box,
                     elapsed_box, cache_box, detail_box, history_box,
                     explanation_box],
            show_progress="full",
        )
        clear_button.click(
            fn=clear_all,
            inputs=None,
            outputs=[image_input, cpp_input, image_preview, latex_box,
                     translation_box, elapsed_box, cache_box, detail_box,
                     history_box, explanation_box],
        )
        batch_button.click(
            fn=run_batch,
            inputs=[batch_files, cpp_input, engine_radio, verifier_radio],
            outputs=[batch_progress, batch_status, batch_output,
                     history_box, batch_summary],
            show_progress="full",
        )
        export_json_button.click(
            fn=export_current_json,
            inputs=None,
            outputs=[export_message, export_file],
        )
        export_csv_button.click(
            fn=export_current_csv,
            inputs=None,
            outputs=[export_message, export_file],
        )
        clear_history_button.click(
            fn=clear_history,
            inputs=None,
            outputs=[export_message, history_box],
        )
    return demo


def build_argument_parser() -> argparse.ArgumentParser:
    """创建 Web 启动参数解析器，默认地址保持 127.0.0.1:7860。"""
    parser = argparse.ArgumentParser(description="数学公式校验 Gradio Web 界面")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=7860, help="监听端口")
    return parser


def main(argv: list[str] | None = None) -> int:
    """解析启动参数并启动 Gradio 界面。"""
    args = build_argument_parser().parse_args(argv)
    if not 1 <= args.port <= 65535:
        print("[web] 错误：端口必须在 1 到 65535 之间")
        return 2
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        print("[警告] 未设置 DEEPSEEK_API_KEY：界面可启动，但调用模型会失败。")
    demo = build_demo()
    # 串行队列：一次只跑一张图，避免并发争抢 OCR 缓存与 API 配额。
    demo.queue(default_concurrency_limit=1, max_size=16)
    demo.launch(
        server_name=args.host,
        server_port=args.port,
        inbrowser=False,
        quiet=False,
    )
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
