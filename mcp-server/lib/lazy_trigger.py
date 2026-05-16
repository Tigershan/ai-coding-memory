"""mcp-server.lib.lazy_trigger - 后台 lazy distill（redesign §6.4）

时机：
    MCP server 启动时调一次 maybe_trigger_background()。
    search_memory 工具调用时也可以调（覆盖"开了很久不动"场景）。

行为：
    1. 读 ~/.ai-memory/.last_distill 时间戳
    2. 距上次 < min_interval_hours → 跳过
    3. 当前小时 < min_hour（默认 22 点）且不是首次 → 跳过（避免 coding 高峰抢 LLM 配额）
       注：host_agent 模式下不抢用户 LLM 配额（任务包不调 LLM），可放宽此限制
    4. 尝试拿 fcntl 文件锁 ~/.ai-memory/.distill.lock；
       拿不到（其他 IDE 已在跑）→ 跳过
    5. fork detached 子进程跑 distill；锁文件 fd 传递给子进程持有
    6. 子进程完成后更新 .last_distill，释放锁

实现细节：
    - fork 用 subprocess.Popen + start_new_session=True，IDE 关闭不杀子进程
    - 文件锁用 stdlib fcntl.flock（Unix）；Windows 暂不支持（msvcrt.locking）
    - 失败静默写日志，不弹给 IDE

输出（执行过的话）：
    ~/.ai-memory/logs/lazy-trigger-<date>.log
    每条 distill 结果会按主入口的逻辑写到 logs/distill-<date>.log
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# fcntl 仅 Unix；Windows 后续兼容
try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.paths import (  # noqa: E402
    DATA_ROOT,
    DISTILL_LOCK_PATH,
    LAST_DISTILL_PATH,
    LOG_DIR,
)


DEFAULT_MIN_INTERVAL_HOURS = 24
DEFAULT_MIN_HOUR = 22  # 当地小时；< 此值不跑（host_agent 模式可放宽）


def maybe_trigger_background(
    *,
    min_interval_hours: int = DEFAULT_MIN_INTERVAL_HOURS,
    min_hour: int | None = DEFAULT_MIN_HOUR,
    range_arg: str = "yesterday",
    mode: str | None = None,
    force: bool = False,
) -> dict:
    """主入口。返回 dict：
    {
        "triggered": bool,
        "reason": str,
        "pid": int | None,
    }
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # 1. 间隔检查
    if not force:
        last = _read_last_distill_ts()
        if last and (time.time() - last) < min_interval_hours * 3600:
            return {"triggered": False, "reason": "interval-too-short", "pid": None}

    # 2. 时段检查
    if not force and min_hour is not None:
        now_hour = datetime.now().hour
        first_ever = _read_last_distill_ts() is None
        if not first_ever and now_hour < min_hour:
            return {"triggered": False, "reason": f"before-min-hour-{min_hour}", "pid": None}

    # 3. 文件锁
    if not _HAS_FCNTL:
        return {"triggered": False, "reason": "no-fcntl-support", "pid": None}

    lock_fd = _try_acquire_lock()
    if lock_fd is None:
        return {"triggered": False, "reason": "lock-held-elsewhere", "pid": None}

    # 4. fork distill 子进程
    pid = _spawn_distill(range_arg, mode, lock_fd)
    if pid is None:
        # spawn 失败，立刻释放锁
        _release_lock(lock_fd)
        return {"triggered": False, "reason": "spawn-failed", "pid": None}

    _log(f"triggered distill pid={pid} range={range_arg} mode={mode or 'auto'}")
    return {"triggered": True, "reason": "ok", "pid": pid}


# ==================== 内部 ====================

def _read_last_distill_ts() -> float | None:
    if not LAST_DISTILL_PATH.exists():
        return None
    try:
        return float(LAST_DISTILL_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _try_acquire_lock() -> int | None:
    """非阻塞拿锁；返回 fd（成功）或 None（被占用）"""
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(DISTILL_LOCK_PATH), os.O_RDWR | os.O_CREAT, 0o644)
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        os.close(fd)
        return None


def _release_lock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


def _spawn_distill(range_arg: str, mode: str | None, lock_fd: int) -> int | None:
    """fork detached 子进程跑 distill。
    锁 fd 传给子进程持有（pass_fds），子进程结束时 OS 自动释放锁。
    父进程关掉自己的 fd 拷贝（避免父进程持锁）。"""
    distill_script = PROJECT_ROOT / "distill" / "scripts" / "distill.py"
    if not distill_script.exists():
        _log(f"distill.py not found: {distill_script}")
        return None

    log_file = LOG_DIR / f"lazy-trigger-{datetime.now().date().isoformat()}.log"
    log_fh = open(log_file, "a", encoding="utf-8")
    log_fh.write(f"\n--- spawn at {datetime.now().isoformat(timespec='seconds')} ---\n")
    log_fh.flush()

    args = [sys.executable, str(distill_script), "--range", range_arg, "--verbose"]
    if mode:
        args += ["--mode", mode]

    env = os.environ.copy()
    # 子进程结束后写 .last_distill
    env["AI_MEMORY_LAZY_LOCK_FD"] = str(lock_fd)  # 仅记录，不依赖

    try:
        proc = subprocess.Popen(
            args,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,        # detach from session, IDE 关掉不会杀
            pass_fds=(lock_fd,),            # 子进程继承锁，自动 close_fds 其它
            env=env,
        )
    except OSError as e:
        _log(f"spawn failed: {e}")
        return None
    finally:
        log_fh.close()  # 子进程已经 inherit fd

    # 父进程关闭自己的 lock_fd（释放父进程对锁的引用；子进程仍持有）
    try:
        os.close(lock_fd)
    except OSError:
        pass

    # fork-and-forget：父进程不 wait。
    # 但需要一个看门进程负责子进程结束时更新 .last_distill —— 用 atexit 不够（detached）。
    # 简洁做法：再 spawn 一个轻量 watcher 子进程。
    # 实际上 distill.py 本身可以在结束时写 .last_distill —— 在主入口加：
    # （已在 distill.py main() 末尾加 _touch_last_distill）。
    return proc.pid


def _log(msg: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"lazy-trigger-{datetime.now().date().isoformat()}.log"
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n")
    except OSError:
        pass


def touch_last_distill() -> None:
    """distill 主入口在结束时调，更新 .last_distill 时间戳"""
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        LAST_DISTILL_PATH.write_text(f"{time.time()}\n", encoding="utf-8")
    except OSError:
        pass
