# -*- coding: utf-8 -*-
"""应用启动器：日志初始化、命令可用性探测与（受控）外部进程启动。

安全约束：只使用 subprocess.Popen(argv, shell=False)，不拼接 shell 字符串。
"""

import logging
import logging.handlers
import os
import shutil
import subprocess
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from . import desktop, paths
from .model import AutostartItem, Config
from .paths import now_iso
from .store import ConfigStore

LOGGER_NAME = "uos_autostart"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_LOG_HANDLER = None
_DISPLAY_ENV_KEYS = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY")


def setup_logging(log_file: Optional[str] = None, level: str = "INFO") -> logging.Logger:
    """初始化日志；重复调用不会重复添加 handler；失败时退化为 StreamHandler。"""
    global _LOG_HANDLER
    logger = logging.getLogger(LOGGER_NAME)
    level_value = getattr(logging, str(level).upper(), logging.INFO)
    logger.setLevel(level_value)
    logger.propagate = False
    if _LOG_HANDLER is not None:
        return logger
    target = log_file or paths.log_path()
    handler = None
    try:
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            target, maxBytes=1024 * 1024, backupCount=2, encoding="utf-8"
        )
    except Exception:
        handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
    handler.setLevel(level_value)
    logger.addHandler(handler)
    _LOG_HANDLER = handler
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def command_available(command: str) -> Tuple[bool, str]:
    """返回 (是否可用, 解析到的路径或错误说明)。"""
    argv = desktop.split_command(desktop.strip_field_codes(command))
    if not argv:
        return (False, "命令为空")
    first = argv[0]
    has_separator = os.sep in first
    if os.altsep:
        has_separator = has_separator or os.altsep in first
    if has_separator or first.startswith("~"):
        candidate = os.path.expanduser(first)
        if not os.path.isfile(candidate):
            return (False, "文件不存在：%s" % candidate)
        if os.name != "nt" and not os.access(candidate, os.X_OK):
            return (False, "文件不可执行：%s" % candidate)
        return (True, candidate)
    found = shutil.which(first)
    if found:
        return (True, found)
    return (False, "未在 PATH 中找到可执行文件：%s" % first)


class Launcher(object):
    """按配置顺序启动自启动项。"""

    def __init__(
        self,
        store: ConfigStore,
        dry_run: bool = False,
        sleep_func: Callable[[float], None] = time.sleep,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.store = store
        self.dry_run = bool(dry_run)
        self._sleep = sleep_func if sleep_func is not None else time.sleep
        self.logger = logger if logger is not None else get_logger()

    def _open_log_target(self):
        if self.dry_run:
            return subprocess.DEVNULL, None
        try:
            target = paths.log_path()
            parent = os.path.dirname(target)
            if parent:
                os.makedirs(parent, exist_ok=True)
            handle = open(target, "a", encoding="utf-8", errors="replace")
            return handle, handle
        except Exception:
            return subprocess.DEVNULL, None

    def start_item(self, item: AutostartItem) -> bool:
        if not isinstance(item, AutostartItem):
            return False
        argv = desktop.split_command(desktop.strip_field_codes(item.command))
        if not argv:
            self.logger.warning("条目「%s」命令为空，跳过启动", item.name or item.id)
            return False
        label = item.name or item.id
        if self.dry_run:
            self.logger.info("（dry-run）将启动「%s」：%s", label, " ".join(argv))
            return True
        env = dict(os.environ)
        for key in _DISPLAY_ENV_KEYS:
            if key in os.environ:
                env[key] = os.environ[key]
        stdout_target, handle = self._open_log_target()
        popen_kwargs = {
            "cwd": item.workdir or None,
            "stdin": subprocess.DEVNULL,
            "stdout": stdout_target,
            "stderr": subprocess.STDOUT,
            "close_fds": True,
            "env": env,
        }
        started = False
        try:
            try:
                popen_kwargs["start_new_session"] = True
                subprocess.Popen(argv, **popen_kwargs)
            except (TypeError, ValueError):
                popen_kwargs.pop("start_new_session", None)
                subprocess.Popen(argv, **popen_kwargs)
            started = True
        except OSError as exc:
            self.logger.error("启动「%s」失败：%s", label, exc)
        finally:
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
        if not started:
            return False
        self.logger.info("已启动「%s」：%s", label, " ".join(argv))
        try:
            item.last_run_at = now_iso()
            item.run_count = int(item.run_count or 0) + 1
            self.store.update(item)
        except Exception as exc:
            self.logger.warning("更新条目运行信息失败：%s", exc)
        return True

    @staticmethod
    def _safe_delay(value) -> float:
        try:
            delay = float(value)
        except (TypeError, ValueError):
            return 0.0
        if delay < 0:
            return 0.0
        if delay > 3600:
            return 3600.0
        return delay

    @staticmethod
    def _safe_scale(value) -> float:
        try:
            scale = float(value)
        except (TypeError, ValueError):
            return 1.0
        if scale < 0:
            return 0.0
        return scale

    def start_all(
        self,
        cfg: Optional[Config] = None,
        include_xdg: bool = True,
        delay_scale: float = 1.0,
    ) -> Dict[str, Any]:
        if cfg is None:
            try:
                cfg = self.store.load()
            except Exception:
                cfg = Config()
        scale = self._safe_scale(delay_scale)
        started = []
        failed = []
        skipped = []
        global_delay = self._safe_delay(getattr(cfg, "global_delay", 0)) * scale
        if global_delay > 0:
            self._sleep(global_delay)
        for item in getattr(cfg, "items", None) or []:
            if not isinstance(item, AutostartItem):
                continue
            if not item.enabled:
                skipped.append(item.id)
                continue
            if item.mode == "xdg" and not include_xdg:
                skipped.append(item.id)
                continue
            delay = self._safe_delay(item.delay) * scale
            if delay > 0:
                self._sleep(delay)
            try:
                ok = self.start_item(item)
            except Exception as exc:
                self.logger.error("启动「%s」异常：%s", item.name or item.id, exc)
                ok = False
            if ok:
                started.append(item.id)
            else:
                failed.append(item.id)
        return {"started": started, "failed": failed, "skipped": skipped}

    def start_all_async(
        self,
        cfg: Optional[Config] = None,
        include_xdg: bool = False,
        delay_scale: float = 1.0,
    ) -> threading.Thread:
        thread = threading.Thread(
            target=self.start_all,
            kwargs={
                "cfg": cfg,
                "include_xdg": include_xdg,
                "delay_scale": delay_scale,
            },
            name="uos-autostart-launcher",
            daemon=True,
        )
        thread.start()
        return thread
