# -*- coding: utf-8 -*-
"""工单1：OCR 与公式校验结果的持久化缓存。"""
from __future__ import annotations

import copy
import hashlib
import os
import pickle
import tempfile
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

# 新增：缓存目录由本文件位置推导，避免测试导入时依赖 config 和 API Key。
BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / ".cache"
OCR_CACHE_FILE = "ocr.pkl"
VERIFY_CACHE_FILE = "verify.pkl"
# 工单1：翻译结果单独持久化，防止同一输入多次调用结果漂移。
TRANSLATE_CACHE_FILE = "translate.pkl"
# 工单1（w11）：解释层结果单独持久化，避免同一错例重复消耗模型预算。
EXPLAIN_CACHE_FILE = "explain.pkl"
# 支持的缓存类别，便于统一遍历。
CACHE_KINDS = ("ocr", "verify", "translate", "explain")


def _is_disabled() -> bool:
    """读取开关；只认明确的 0，避免空值或普通文本误关闭缓存。"""
    return os.environ.get("MATH_AGENT_CACHE", "1").strip() == "0"


def _cache_dir() -> Path:
    """每次动态读取目录，方便测试把缓存指向临时目录。"""
    return Path(CACHE_DIR)


def _cache_file(cache_kind: str) -> Path:
    file_names = {
        "ocr": OCR_CACHE_FILE,
        "verify": VERIFY_CACHE_FILE,
        "translate": TRANSLATE_CACHE_FILE,
        "explain": EXPLAIN_CACHE_FILE,
    }
    file_name = file_names.get(cache_kind, VERIFY_CACHE_FILE)
    return _cache_dir() / file_name


@lru_cache(maxsize=256)
def _hash_image_file(path_text: str, mtime_ns: int, size: int) -> str:
    """热点缓存图片内容哈希；mtime 和 size 变化时自动换用新缓存项。"""
    del mtime_ns, size
    digest = hashlib.sha256()
    with open(path_text, "rb") as image_file:
        for chunk in iter(lambda: image_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _image_key(image_path: Any, scope: str = "") -> str:
    """按图片内容生成 key，不以路径作为最终缓存 key。

    scope 用于区分同一张图片的不同识别提示词/引擎组合，避免串用结果。
    """
    path_text = str(Path(image_path).resolve())
    stat_result = Path(path_text).stat()
    content_hash = _hash_image_file(
        path_text, stat_result.st_mtime_ns, stat_result.st_size)
    if not scope:
        return content_hash
    return hashlib.sha256(
        f"{content_hash}\x00{scope}".encode("utf-8")).hexdigest()


@lru_cache(maxsize=1024)
def _verify_key(latex: str, code_or_expr: str, scope: str = "") -> str:
    """按纯文本输入生成稳定的校验 key；scope 区分校验模式。"""
    payload = f"{latex}\x00{code_or_expr}\x00{scope}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@lru_cache(maxsize=1024)
def _translate_key(source_text: str, direction: str = "",
                   scope: str = "") -> str:
    """按翻译方向 + 模型/提示词版本 + 原文生成稳定 key。

    原文不做归一化：只有完全相同的历史输入才允许命中，避免把不同代码
    的翻译结果互相串用。
    """
    payload = f"{direction}\x00{scope}\x00{source_text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class MathAgentCache:
    """管理两类 pickle 缓存，所有公开方法都不会向主流程抛异常。"""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {
            kind: {} for kind in CACHE_KINDS}
        self._loaded: dict[str, bool] = {
            kind: False for kind in CACHE_KINDS}
        self._stats: dict[str, dict[str, int]] = {
            kind: {"hits": 0, "misses": 0, "writes": 0, "errors": 0}
            for kind in CACHE_KINDS
        }
        self._lock = threading.RLock()

    def _record(self, cache_kind: str, field: str) -> None:
        self._stats[cache_kind][field] += 1

    def _backup_corrupt_file(self, cache_kind: str) -> None:
        """把损坏文件改名留存，同名冲突时追加微秒和序号。"""
        source = _cache_file(cache_kind)
        stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{time.time_ns() % 1_000_000:06d}"
        backup = source.with_name(f"{source.name}.corrupt.{stamp}")
        suffix = 1
        while backup.exists():
            backup = source.with_name(f"{source.name}.corrupt.{stamp}.{suffix}")
            suffix += 1
        try:
            os.replace(source, backup)
        except Exception:
            self._record(cache_kind, "errors")

    def _load_locked(self, cache_kind: str) -> None:
        """按需加载指定缓存；坏文件只备份并重建，不影响调用方。"""
        if self._loaded[cache_kind]:
            return
        self._loaded[cache_kind] = True
        cache_path = _cache_file(cache_kind)
        if not cache_path.exists():
            return
        try:
            with open(cache_path, "rb") as cache_file:
                loaded_data = pickle.load(cache_file)
            if not isinstance(loaded_data, dict):
                raise ValueError("缓存文件顶层不是字典")
            self._data[cache_kind] = loaded_data
        except Exception:
            self._record(cache_kind, "errors")
            self._backup_corrupt_file(cache_kind)
            self._data[cache_kind] = {}

    def _save_locked(self, cache_kind: str) -> None:
        """先写同目录临时文件，再原子替换正式缓存文件。"""
        cache_path = _cache_file(cache_kind)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = ""
        try:
            file_descriptor, temp_path = tempfile.mkstemp(
                prefix=f".{cache_path.name}.",
                suffix=".tmp",
                dir=str(cache_path.parent),
            )
            with os.fdopen(file_descriptor, "wb") as temp_file:
                pickle.dump(self._data[cache_kind], temp_file,
                            protocol=pickle.HIGHEST_PROTOCOL)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, cache_path)
            temp_path = ""
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass

    def _get(self, cache_kind: str, key: Optional[str]) -> Any:
        if _is_disabled() or key is None:
            return None
        with self._lock:
            try:
                self._load_locked(cache_kind)
                if key not in self._data[cache_kind]:
                    self._record(cache_kind, "misses")
                    return None
                result = copy.deepcopy(self._data[cache_kind][key])
                self._record(cache_kind, "hits")
                return result
            except Exception:
                self._record(cache_kind, "errors")
                return None

    def _put(self, cache_kind: str, key: Optional[str], result: Any) -> bool:
        if _is_disabled() or key is None:
            return False
        with self._lock:
            try:
                self._load_locked(cache_kind)
                self._data[cache_kind][key] = copy.deepcopy(result)
                self._save_locked(cache_kind)
                self._record(cache_kind, "writes")
                return True
            except Exception:
                self._record(cache_kind, "errors")
                return False

    def get_ocr(self, image_path: Any, scope: str = "") -> Optional[Any]:
        """读取 OCR 缓存；任何失败都视为未命中。"""
        if _is_disabled():
            return None
        try:
            return self._get("ocr", _image_key(image_path, scope))
        except Exception:
            with self._lock:
                self._record("ocr", "errors")
            return None

    def put_ocr(self, image_path: Any, result: Any, scope: str = "") -> bool:
        """写入 OCR 缓存；任何失败都只返回 False。"""
        if _is_disabled():
            return False
        try:
            return self._put("ocr", _image_key(image_path, scope), result)
        except Exception:
            with self._lock:
                self._record("ocr", "errors")
            return False

    def get_verify(self, latex: str, code: str,
                   scope: str = "") -> Optional[Any]:
        """读取校验缓存；key 只使用 LaTeX 和纯文本代码/表达式。"""
        if _is_disabled():
            return None
        try:
            return self._get(
                "verify", _verify_key(str(latex), str(code), scope))
        except Exception:
            with self._lock:
                self._record("verify", "errors")
            return None

    def put_verify(self, latex: str, code: str, result: Any,
                   scope: str = "") -> bool:
        """写入校验缓存；任何失败都只返回 False。"""
        if _is_disabled():
            return False
        try:
            return self._put(
                "verify",
                _verify_key(str(latex), str(code), scope),
                result,
            )
        except Exception:
            with self._lock:
                self._record("verify", "errors")
            return False

    def get_translate(self, source_text: str, direction: str = "",
                      scope: str = "") -> Optional[Any]:
        """读取翻译缓存；key 只使用方向、scope 与原文。"""
        if _is_disabled():
            return None
        try:
            return self._get(
                "translate",
                _translate_key(str(source_text), str(direction), str(scope)),
            )
        except Exception:
            with self._lock:
                self._record("translate", "errors")
            return None

    def put_translate(self, source_text: str, result: Any,
                      direction: str = "", scope: str = "") -> bool:
        """写入翻译缓存；任何失败都只返回 False。"""
        if _is_disabled():
            return False
        try:
            return self._put(
                "translate",
                _translate_key(str(source_text), str(direction), str(scope)),
                result,
            )
        except Exception:
            with self._lock:
                self._record("translate", "errors")
            return False

    # 新增（工单1 / w11）：解释层缓存。key 复用「公式 + 代码 + scope」结构，
    # scope 里含模型、提示词版本与证据摘要，避免不同证据共用同一解释。
    def get_explain(self, latex: str, code: str,
                    scope: str = "") -> Optional[Any]:
        if _is_disabled():
            return None
        try:
            return self._get(
                "explain", _verify_key(str(latex), str(code), str(scope)))
        except Exception:
            with self._lock:
                self._record("explain", "errors")
            return None

    def put_explain(self, latex: str, code: str, result: Any,
                    scope: str = "") -> bool:
        if _is_disabled():
            return False
        try:
            return self._put(
                "explain",
                _verify_key(str(latex), str(code), str(scope)),
                result,
            )
        except Exception:
            with self._lock:
                self._record("explain", "errors")
            return False

    def stats(self) -> dict:
        """返回独立副本，调用方修改不会污染内部计数。"""
        with self._lock:
            ocr_stats = dict(self._stats["ocr"])
            verify_stats = dict(self._stats["verify"])
            translate_stats = dict(self._stats["translate"])
        total = {
            "hits": (ocr_stats["hits"] + verify_stats["hits"]
                     + translate_stats["hits"]),
            "misses": (ocr_stats["misses"] + verify_stats["misses"]
                       + translate_stats["misses"]),
            "writes": (ocr_stats["writes"] + verify_stats["writes"]
                       + translate_stats["writes"]),
            "errors": (ocr_stats["errors"] + verify_stats["errors"]
                       + translate_stats["errors"]),
        }
        return {
            "disabled": _is_disabled(),
            "ocr": ocr_stats,
            "verify": verify_stats,
            "translate": translate_stats,
            # 新增（w11）：解释层独立计数，不并入下面的总命中，保持旧口径稳定。
            "explain": dict(self._stats["explain"]),
            "total": total,
            # 新增：顶层别名便于报告直接读取 report.cache.hits。
            **total,
        }

    def format_stats(self, stats_data: Optional[dict] = None) -> str:
        """生成适合 Markdown 和终端展示的单行统计。"""
        current = stats_data if stats_data is not None else self.stats()
        if current.get("disabled"):
            return "已禁用"
        ocr_stats = current.get("ocr", {})
        verify_stats = current.get("verify", {})
        translate_stats = current.get("translate", {})
        total_stats = current.get("total", {})
        return (
            f"OCR 命中 {ocr_stats.get('hits', 0)}、未命中 "
            f"{ocr_stats.get('misses', 0)}、写入 {ocr_stats.get('writes', 0)}、"
            f"错误 {ocr_stats.get('errors', 0)}；校验命中 "
            f"{verify_stats.get('hits', 0)}、未命中 {verify_stats.get('misses', 0)}、"
            f"写入 {verify_stats.get('writes', 0)}、错误 "
            f"{verify_stats.get('errors', 0)}；翻译命中 "
            f"{translate_stats.get('hits', 0)}、未命中 "
            f"{translate_stats.get('misses', 0)}、写入 "
            f"{translate_stats.get('writes', 0)}；总计命中 "
            f"{total_stats.get('hits', 0)}"
        )

    def clear(self) -> None:
        """清空内存数据并删除持久化文件，供维护和测试使用。"""
        with self._lock:
            self._data = {kind: {} for kind in CACHE_KINDS}
            self._loaded = {kind: False for kind in CACHE_KINDS}
            for cache_kind in CACHE_KINDS:
                try:
                    _cache_file(cache_kind).unlink(missing_ok=True)
                except Exception:
                    self._record(cache_kind, "errors")


# 模块级单例：主流程统一使用这一份计数和内存数据。
cache = MathAgentCache()


def get_ocr(image_path: Any, scope: str = "") -> Optional[Any]:
    return cache.get_ocr(image_path, scope)


def put_ocr(image_path: Any, result: Any, scope: str = "") -> bool:
    return cache.put_ocr(image_path, result, scope)


def get_verify(latex: str, code: str, scope: str = "") -> Optional[Any]:
    return cache.get_verify(latex, code, scope)


def put_verify(latex: str, code: str, result: Any,
               scope: str = "") -> bool:
    return cache.put_verify(latex, code, result, scope)


def get_translate(source_text: str, direction: str = "",
                  scope: str = "") -> Optional[Any]:
    return cache.get_translate(source_text, direction, scope)


def put_translate(source_text: str, result: Any, direction: str = "",
                  scope: str = "") -> bool:
    return cache.put_translate(source_text, result, direction, scope)


def get_explain(latex: str, code: str, scope: str = "") -> Optional[Any]:
    return cache.get_explain(latex, code, scope)


def put_explain(latex: str, code: str, result: Any,
                scope: str = "") -> bool:
    return cache.put_explain(latex, code, result, scope)


def stats() -> dict:
    return cache.stats()


def format_stats(stats_data: Optional[dict] = None) -> str:
    return cache.format_stats(stats_data)


def clear() -> None:
    cache.clear()
