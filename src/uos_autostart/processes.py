# -*- coding: utf-8 -*-
"""进程探测：判断被托管的图形/后台程序当前是否仍在运行。

优先使用 Linux 的 ``/proc``（统信 UOS 正常路径）读取完整命令行；
其他平台退化为 ``tasklist``（Windows）或 ``ps``（macOS/BSD 等），
仅用于开发与自动化测试。核心模块不依赖任何第三方库。

匹配规则（尽量贴近用户在「命令」里填写的程序）：
1. 绝对路径命令：进程命令行中出现该路径即视为在运行；
2. 相对命令/程序名：进程 argv[0] 的 basename 与之相同即视为在运行；
3. 跳过本管理器自身及其直接父进程，避免"守护自己"造成误判。
"""

import csv
import io
import os
import subprocess
import sys
from typing import Iterable, List, Optional, Tuple

from . import desktop

ProcessEntry = Tuple[int, str]

_OWN_MARKERS = ("uos_autostart", "uos-autostart-manager")
_MAX_CMDLINE = 4096


def _decode(data):
    if isinstance(data, str):
        return data
    for encoding in ("utf-8", "mbcs", "latin-1"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace")


def _list_linux() -> List[ProcessEntry]:
    entries = []
    try:
        names = os.listdir("/proc")
    except OSError:
        return entries
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        try:
            with open("/proc/%d/cmdline" % pid, "rb") as handle:
                raw = handle.read(_MAX_CMDLINE)
        except OSError:
            continue
        if raw:
            parts = [_decode(part) for part in raw.split(b"\x00") if part]
            cmdline = " ".join(parts)
        else:
            try:
                cmdline = os.readlink("/proc/%d/exe" % pid)
            except OSError:
                continue
        if cmdline:
            entries.append((pid, cmdline))
    return entries


def _list_windows() -> List[ProcessEntry]:
    entries = []
    try:
        completed = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return entries
    if completed.returncode != 0:
        return entries
    text = _decode(completed.stdout or b"")
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2:
            continue
        try:
            pid = int(row[1].strip())
        except (TypeError, ValueError):
            continue
        image = (row[0] or "").strip().strip('"')
        if image:
            entries.append((pid, image))
    return entries


def _list_posix() -> List[ProcessEntry]:
    entries = []
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return entries
    if completed.returncode != 0:
        return entries
    for line in _decode(completed.stdout or b"").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        try:
            pid = int(parts[0])
        except (TypeError, ValueError):
            continue
        cmdline = parts[1] if len(parts) > 1 else ""
        if cmdline:
            entries.append((pid, cmdline))
    return entries


def list_processes() -> List[ProcessEntry]:
    """返回 [(pid, cmdline 字符串)]；失败时返回空列表，绝不抛异常。"""
    try:
        if sys.platform.startswith("linux") and os.path.isdir("/proc"):
            return _list_linux()
        if sys.platform.startswith("win"):
            return _list_windows()
        return _list_posix()
    except Exception:  # 进程表读取失败不应影响主流程
        return []


def _is_own_process(pid: int, cmdline: str) -> bool:
    try:
        if pid in (os.getpid(), os.getppid()):
            return True
    except Exception:
        pass
    lowered = cmdline.lower()
    for marker in _OWN_MARKERS:
        if marker in lowered:
            return True
    return False


def target_argv(command: str) -> List[str]:
    """把条目命令规范成 argv（去掉 %U 等字段码并安全切分）。"""
    try:
        cleaned = desktop.strip_field_codes(command)
        return desktop.split_command(cleaned)
    except Exception:
        return []


def command_basename(command: str) -> str:
    argv = target_argv(command)
    if not argv:
        return ""
    first = argv[0].strip().strip('"').strip("'")
    return os.path.basename(first)


def is_process_running(
    command: str,
    processes: Optional[Iterable[ProcessEntry]] = None,
) -> bool:
    """判断 ``command`` 对应的程序是否正在运行。"""
    argv = target_argv(command)
    if not argv:
        return False
    first = argv[0].strip().strip('"').strip("'")
    if not first:
        return False
    base = os.path.basename(first).lower()
    if not base:
        return False
    absolute = first if os.path.isabs(first) else ""
    entries = list(processes) if processes is not None else list_processes()
    absolute_lower = absolute.lower()
    for pid, cmdline in entries:
        if not cmdline:
            continue
        text = str(cmdline).strip()
        if not text:
            continue
        if _is_own_process(pid, text):
            continue
        lowered = text.lower()
        if absolute_lower and absolute_lower in lowered:
            return True
        tokens = lowered.split(None, 1)
        if not tokens:
            continue
        head = tokens[0].strip('"').strip("'")
        if os.path.basename(head) == base:
            return True
    return False


def pid_alive(pid: int) -> bool:
    """检查 pid 是否存活（用于诊断；Windows 用 OpenProcess 兜底）。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            process_query_limited_information = 0x1000
            still_active = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(
                process_query_limited_information, False, pid
            )
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                return bool(ok) and code.value == still_active
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
