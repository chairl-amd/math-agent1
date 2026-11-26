# -*- coding: utf-8 -*-
"""agent/circuit_breaker.py — 跨进程持久化的混合校验熔断器。

工单1 目标：
1. 熔断状态落到 cache/circuit_state.json，进程重启后仍然有效；
2. 多个进程（benchmark 子进程、Web 界面、命令行）共享同一份状态；
3. 读写通过文件锁串行化，避免并发写坏 JSON；
4. 状态文件损坏、目录不可写、锁超时等异常一律降级为“不熔断”，
   绝不让熔断器本身成为新的故障点。

设计要点：
- 每次操作都在“加锁 → 读盘 → 改状态 → 原子写回”内完成，
  因此进程 A 记录的连续超时，进程 B 下一次读取就能看到；
- 优先使用 filelock 库；未安装时回退到内置的 O_EXCL 锁文件实现，
  保证在最小依赖环境里同样可用；
- 保留旧版 _HybridCircuitBreaker 的 snapshot() 键名，兼容既有报告。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

# ---------- 路径与配置 ----------
BASE_DIR = Path(__file__).resolve().parent.parent
# 工单要求的状态目录：D:\math-agent\cache\
DEFAULT_STATE_DIR = BASE_DIR / "cache"
DEFAULT_STATE_FILE = DEFAULT_STATE_DIR / "circuit_state.json"
DEFAULT_LOCK_FILE = DEFAULT_STATE_DIR / "circuit_state.lock"

STATE_VERSION = 1
# 历史事件最多保留条数，避免状态文件无限膨胀。
MAX_HISTORY = 40
# 内置锁的最长等待时间与轮询间隔（秒）。
LOCK_TIMEOUT = 10.0
LOCK_POLL_INTERVAL = 0.02
# 锁文件超过该秒数仍未释放，视为残留锁并强制回收。
LOCK_STALE_SECONDS = 30.0


def _env_float(name: str, default: float) -> float:
    """读取浮点型环境变量，非法值回退默认值。"""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    """读取整型环境变量，非法值回退默认值。"""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return default


def resolve_state_file() -> Path:
    """允许用环境变量把状态文件指向临时目录，便于测试隔离。"""
    override = os.environ.get("MATH_AGENT_CIRCUIT_STATE", "").strip()
    return Path(override) if override else DEFAULT_STATE_FILE


def resolve_lock_file(state_file: Optional[Path] = None) -> Path:
    """锁文件默认与状态文件同目录同名前缀。"""
    override = os.environ.get("MATH_AGENT_CIRCUIT_LOCK", "").strip()
    if override:
        return Path(override)
    target = state_file or resolve_state_file()
    return target.with_suffix(target.suffix + ".lock")


def _breaker_disabled() -> bool:
    """MATH_AGENT_CIRCUIT_DISABLED=1 时彻底关闭熔断（只用于排障）。"""
    return os.environ.get(
        "MATH_AGENT_CIRCUIT_DISABLED", "0").strip() in {"1", "true", "True"}


# ---------- 文件锁：优先 filelock，缺失时用内置实现 ----------
@contextmanager
def _builtin_file_lock(lock_path: Path, timeout: float = LOCK_TIMEOUT
                       ) -> Iterator[None]:
    """基于 O_EXCL 的跨进程互斥锁；持有者崩溃后可按 mtime 回收。"""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.1, timeout)
    handle = None
    while handle is None:
        try:
            handle = os.open(str(lock_path),
                             os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            # 残留锁回收：超过 LOCK_STALE_SECONDS 未更新则删除重试。
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                age = 0.0
            if age > LOCK_STALE_SECONDS:
                try:
                    lock_path.unlink()
                    logger.warning("回收残留熔断锁：%s", lock_path)
                    continue
                except OSError:
                    pass
            if time.monotonic() >= deadline:
                raise TimeoutError(f"等待熔断锁超时：{lock_path}")
            time.sleep(LOCK_POLL_INTERVAL)
        except OSError as exc:
            # 目录不可写等场景直接放行，熔断器不应阻断主流程。
            logger.warning("熔断锁不可用（%s），跳过加锁：%s", exc, lock_path)
            yield
            return
    try:
        try:
            os.write(handle, f"{os.getpid()} {time.time():.6f}".encode("ascii"))
        except OSError:
            pass
        yield
    finally:
        try:
            os.close(handle)
        finally:
            try:
                lock_path.unlink()
            except OSError:
                pass


def _filelock_context(lock_path: Path, timeout: float = LOCK_TIMEOUT):
    """返回可用的锁上下文管理器，优先使用 filelock 库。"""
    try:
        from filelock import FileLock  # 局部导入，避免硬依赖
    except Exception:  # pragma: no cover - 依赖缺失时的回退分支
        return _builtin_file_lock(lock_path, timeout)
    try:
        return FileLock(str(lock_path), timeout=timeout)
    except Exception:  # pragma: no cover - 旧版本签名差异
        return _builtin_file_lock(lock_path, timeout)


# ---------- 持久化熔断器 ----------
class PersistentCircuitBreaker:
    """熔断状态存放在 JSON 文件中，可被多个进程共享。"""

    def __init__(self, threshold: int = 2, *, provider: str = "hybrid",
                 state_file: Optional[Path] = None,
                 lock_file: Optional[Path] = None,
                 timeout: float = LOCK_TIMEOUT) -> None:
        configured_threshold = _env_int(
            "HETERO_CIRCUIT_BREAKER_THRESHOLD", threshold)
        if configured_threshold < 1:
            configured_threshold = max(1, threshold)
        self.threshold = configured_threshold
        self.provider = provider
        self.state_file = Path(state_file) if state_file else resolve_state_file()
        self.lock_file = (
            Path(lock_file) if lock_file else resolve_lock_file(self.state_file))
        self.timeout = max(0.1, float(timeout))
        # 进程内可重入锁：同一进程的线程先互斥，再争抢文件锁。
        self._thread_lock = threading.RLock()
        # 进程内统计：即使状态文件不可写，也能从 snapshot() 读到本次运行的计数。
        self._local = {"consecutive_timeouts": 0, "open": False}

    # ----- 状态读写 -----
    def _default_state(self) -> dict[str, Any]:
        return {
            "version": STATE_VERSION,
            "provider": self.provider,
            "open": False,
            "consecutive_timeouts": 0,
            "threshold": self.threshold,
            "opened_at": None,
            "updated_at": None,
            "updated_by_pid": None,
            "history": [],
        }

    def _read_state_unlocked(self) -> dict[str, Any]:
        """读取状态文件；缺失或损坏时返回默认状态（视为未熔断）。"""
        try:
            raw_text = self.state_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return self._default_state()
        except OSError as exc:
            logger.warning("熔断状态读取失败，按未熔断处理：%s", exc)
            return self._default_state()
        try:
            data = json.loads(raw_text)
        except (ValueError, TypeError):
            logger.warning("熔断状态文件不是合法 JSON，已忽略：%s",
                           self.state_file)
            return self._default_state()
        if not isinstance(data, dict):
            return self._default_state()
        state = self._default_state()
        state.update({
            "open": bool(data.get("open", False)),
            "consecutive_timeouts": int(
                data.get("consecutive_timeouts", 0) or 0),
            "threshold": int(data.get("threshold", self.threshold)
                             or self.threshold),
            "opened_at": data.get("opened_at"),
            "updated_at": data.get("updated_at"),
            "updated_by_pid": data.get("updated_by_pid"),
        })
        history = data.get("history")
        if isinstance(history, list):
            state["history"] = [item for item in history if isinstance(item, dict)]
        return state

    def _write_state_unlocked(self, state: dict[str, Any]) -> bool:
        """先写同目录临时文件再原子替换，避免读到半截 JSON。"""
        state["version"] = STATE_VERSION
        state["provider"] = self.provider
        state["threshold"] = self.threshold
        state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        state["updated_by_pid"] = os.getpid()
        history = state.get("history")
        if isinstance(history, list) and len(history) > MAX_HISTORY:
            state["history"] = history[-MAX_HISTORY:]
        temp_path = ""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            temp_path = (
                self.state_file.parent
                / f".{self.state_file.name}.{os.getpid()}.tmp"
            )
            with open(temp_path, "w", encoding="utf-8") as state_handle:
                json.dump(state, state_handle, ensure_ascii=False, indent=2)
                state_handle.flush()
                os.fsync(state_handle.fileno())
            os.replace(temp_path, self.state_file)
            temp_path = ""
            return True
        except Exception as exc:
            logger.warning("熔断状态写入失败：%s", exc)
            return False
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    @contextmanager
    def _locked_state(self) -> Iterator[Optional[dict[str, Any]]]:
        """加锁后读取最新状态；锁不可用时返回 None 表示只更新进程内计数。"""
        if _breaker_disabled():
            yield None
            return
        with self._thread_lock:
            try:
                context = _filelock_context(self.lock_file, self.timeout)
            except Exception as exc:  # pragma: no cover - 极端环境
                logger.warning("熔断锁创建失败，跳过文件锁：%s", exc)
                yield None
                return
            acquired = False
            try:
                try:
                    context.__enter__()
                    acquired = True
                except Exception as exc:
                    # 锁超时等场景：只维护进程内状态，保持主流程可用。
                    logger.warning("熔断锁获取失败，退化为进程内状态：%s", exc)
                    yield None
                    return
                yield self._read_state_unlocked()
            finally:
                if acquired:
                    try:
                        context.__exit__(None, None, None)
                    except Exception:  # pragma: no cover
                        pass

    def _append_history(self, state: dict[str, Any], event: str,
                        **extra: Any) -> None:
        history = state.setdefault("history", [])
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "event": event,
            "pid": os.getpid(),
        }
        record.update(extra)
        history.append(record)
        if len(history) > MAX_HISTORY:
            del history[:-MAX_HISTORY]

    # ----- 对外接口（与旧 _HybridCircuitBreaker 一致） -----
    def is_open(self) -> bool:
        """任一进程打开熔断即视为打开；状态文件不可读时按未熔断处理。"""
        if _breaker_disabled():
            return False
        with self._locked_state() as state:
            if state is None:
                return bool(self._local["open"])
            opened = bool(state.get("open"))
            self._local["open"] = opened
            return opened

    def record_success(self) -> None:
        """一次成功调用清零连续超时并关闭熔断，写回共享状态。"""
        if _breaker_disabled():
            return
        opened_now = False
        with self._locked_state() as state:
            if state is None:
                self._local["consecutive_timeouts"] = 0
                self._local["open"] = False
                return
            was_open = bool(state.get("open"))
            state["consecutive_timeouts"] = 0
            state["open"] = False
            state["opened_at"] = None
            self._append_history(
                state, "success" if not was_open else "recovered")
            self._write_state_unlocked(state)
            opened_now = was_open
        self._local["consecutive_timeouts"] = 0
        self._local["open"] = False
        if opened_now:
            logger.info("[HYBRID_TIMEOUT_FALLBACK] 异构校验成功，熔断已恢复")

    def record_timeout(self) -> None:
        """累计一次超时；达到阈值则打开熔断并立即对其他进程可见。"""
        if _breaker_disabled():
            return
        opened_now = False
        threshold = self.threshold
        consecutive = 0
        with self._locked_state() as state:
            if state is None:
                self._local["consecutive_timeouts"] += 1
                consecutive = self._local["consecutive_timeouts"]
                if consecutive >= threshold and not self._local["open"]:
                    self._local["open"] = True
                    opened_now = True
            else:
                threshold = int(state.get("threshold") or self.threshold) or self.threshold
                consecutive = int(state.get("consecutive_timeouts", 0) or 0) + 1
                state["consecutive_timeouts"] = consecutive
                self._append_history(
                    state, "timeout", consecutive=consecutive,
                    threshold=threshold)
                if consecutive >= threshold and not state.get("open"):
                    state["open"] = True
                    state["opened_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                    opened_now = True
                    self._append_history(
                        state, "opened", consecutive=consecutive,
                        threshold=threshold)
                self._write_state_unlocked(state)
        self._local["consecutive_timeouts"] = consecutive
        if opened_now:
            self._local["open"] = True
            logger.warning(
                "[HYBRID_TIMEOUT_FALLBACK] 连续 %s 次异构校验超时，"
                "已熔断 hybrid，后续走 SymPy（状态已持久化到 %s）",
                threshold, self.state_file)

    def snapshot(self) -> dict[str, Any]:
        """返回状态摘要；键名兼容旧实现的 open/is_open/consecutive_timeouts。"""
        with self._locked_state() as state:
            if state is None:
                opened = bool(self._local["open"])
                consecutive = int(self._local["consecutive_timeouts"])
                return {
                    "open": opened,
                    "is_open": opened,
                    "consecutive_timeouts": consecutive,
                    "threshold": self.threshold,
                    "provider": self.provider,
                    "state_file": str(self.state_file),
                    "persistent": False,
                }
            opened = bool(state.get("open"))
            return {
                "open": opened,
                "is_open": opened,
                "consecutive_timeouts": int(
                    state.get("consecutive_timeouts", 0) or 0),
                "threshold": int(state.get("threshold") or self.threshold),
                "provider": state.get("provider") or self.provider,
                "state_file": str(self.state_file),
                "persistent": True,
                "opened_at": state.get("opened_at"),
                "updated_at": state.get("updated_at"),
                "updated_by_pid": state.get("updated_by_pid"),
                "history_tail": (state.get("history") or [])[-5:],
            }

    def reset(self) -> None:
        """显式复位（人工排障或测试用），并把复位动作记入历史。"""
        if _breaker_disabled():
            return
        with self._locked_state() as state:
            if state is None:
                self._local = {"consecutive_timeouts": 0, "open": False}
                return
            state["consecutive_timeouts"] = 0
            state["open"] = False
            state["opened_at"] = None
            self._append_history(state, "reset")
            self._write_state_unlocked(state)
        self._local = {"consecutive_timeouts": 0, "open": False}
        logger.info("[HYBRID_TIMEOUT_FALLBACK] 熔断状态已人工复位")

    # ----- 便捷别名 -----
    def record_failure(self, *, is_timeout: bool = False) -> None:
        """仅超时计入熔断；其他异常只记录历史，不计入连续超时。"""
        if is_timeout:
            self.record_timeout()
            return
        if _breaker_disabled():
            return
        with self._locked_state() as state:
            if state is None:
                return
            self._append_history(state, "error")
            self._write_state_unlocked(state)


def main() -> int:
    """命令行排障入口：python -m agent.circuit_breaker [--reset|--status]"""
    import argparse

    parser = argparse.ArgumentParser(description="混合校验熔断状态查看/复位")
    parser.add_argument("--reset", action="store_true", help="复位熔断状态")
    parser.add_argument("--status", action="store_true", help="查看状态（默认）")
    args = parser.parse_args()
    breaker = PersistentCircuitBreaker()
    if args.reset:
        breaker.reset()
    print(json.dumps(breaker.snapshot(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
