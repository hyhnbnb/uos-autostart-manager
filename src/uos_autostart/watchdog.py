# -*- coding: utf-8 -*-
"""掉线自动重启（守护）。

按固定间隔（默认 20 分钟，可在设置中修改）检查所有「启用」且勾选了
「掉线自动重启」的条目：如果对应程序未在运行，则调用 ``Launcher`` 重新启动。
守护线程是 daemon，随管理器进程退出而结束；每轮都会重新读取配置文件，
因此界面上修改开关/间隔后无需重启管理器即可生效。
"""

import threading
import time
from typing import Any, Dict

from . import processes
from .launcher import Launcher, get_logger
from .model import (
    DEFAULT_WATCHDOG_INTERVAL,
    MAX_WATCHDOG_INTERVAL,
    MIN_WATCHDOG_INTERVAL,
)

THREAD_NAME = "uos-autostart-watchdog"


def clamp_interval_minutes(value: Any) -> int:
    """把任意输入收敛到 1..1440 分钟。"""
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return DEFAULT_WATCHDOG_INTERVAL
    if minutes < MIN_WATCHDOG_INTERVAL:
        return MIN_WATCHDOG_INTERVAL
    if minutes > MAX_WATCHDOG_INTERVAL:
        return MAX_WATCHDOG_INTERVAL
    return minutes


def _empty_result() -> Dict[str, Any]:
    return {
        "enabled": False,
        "interval_minutes": DEFAULT_WATCHDOG_INTERVAL,
        "checked": 0,
        "running": [],
        "restarted": [],
        "failed": [],
        "errors": [],
    }


class Watchdog(object):
    """周期性检查并重新拉起掉线的应用。"""

    def __init__(self, store, launcher=None, probe=None, sleep_func=None,
                 logger=None, interval_seconds=None):
        self.store = store
        self.launcher = launcher if launcher is not None else Launcher(store)
        self._probe = probe if probe is not None else processes.list_processes
        self._sleep = sleep_func if sleep_func is not None else time.sleep
        self.logger = logger if logger is not None else get_logger()
        self._interval_override = (
            float(interval_seconds) if interval_seconds else None
        )
        self._stop_event = threading.Event()
        self._thread = None
        self.last_result = _empty_result()
        self.rounds = 0

    # ---- 配置 -------------------------------------------------------
    def load_config(self):
        try:
            return self.store.load()
        except Exception as exc:  # 配置损坏时按默认值处理
            self.logger.error("守护读取配置失败：%s", exc)
            return None

    def interval_minutes(self, cfg=None) -> int:
        if cfg is None:
            cfg = self.load_config()
        if cfg is None:
            return DEFAULT_WATCHDOG_INTERVAL
        return clamp_interval_minutes(getattr(cfg, "watchdog_interval_minutes",
                                              DEFAULT_WATCHDOG_INTERVAL))

    def interval_seconds(self, cfg=None) -> float:
        if self._interval_override:
            return self._interval_override
        return float(self.interval_minutes(cfg) * 60)

    # ---- 单轮检查 ---------------------------------------------------
    def run_once(self) -> Dict[str, Any]:
        """执行一轮检查；返回统计信息，绝不抛异常。"""
        result = _empty_result()
        cfg = self.load_config()
        if cfg is None:
            result["errors"].append("读取配置失败")
            self.last_result = result
            return result

        result["enabled"] = bool(getattr(cfg, "watchdog_enabled", True))
        result["interval_minutes"] = self.interval_minutes(cfg)
        if not result["enabled"]:
            self.last_result = result
            return result

        try:
            targets = cfg.watchdog_items()
        except Exception as exc:
            result["errors"].append("读取守护条目失败：%s" % (exc,))
            self.last_result = result
            return result
        result["checked"] = len(targets)
        if not targets:
            self.last_result = result
            return result

        try:
            entries = self._probe()
        except Exception as exc:
            entries = []
            result["errors"].append("读取进程列表失败：%s" % (exc,))
        if entries is None:
            entries = []

        for item in targets:
            label = item.name or item.id
            try:
                if processes.is_process_running(item.command, entries):
                    result["running"].append(item.id)
                    self.logger.debug("守护：%s 正在运行", label)
                    continue
                self.logger.info("守护：未检测到「%s」在运行，正在重新启动……", label)
                if self.launcher.start_item(item):
                    result["restarted"].append(item.id)
                    self.logger.info("守护：已重新启动「%s」", label)
                else:
                    result["failed"].append(item.id)
                    self.logger.warning("守护：重新启动「%s」失败", label)
            except Exception as exc:
                result["failed"].append(item.id)
                result["errors"].append("处理「%s」失败：%s" % (label, exc))
                self.logger.error("守护：处理「%s」异常：%s", label, exc)

        if result["restarted"]:
            self.logger.info("守护：本轮共重新启动 %d 个应用", len(result["restarted"]))
        self.rounds += 1
        self.last_result = result
        return result

    # ---- 线程 -------------------------------------------------------
    def _loop(self):
        # 先等待一个完整间隔再检查：避免与桌面环境自身的开机自启动竞争。
        while not self._stop_event.is_set():
            interval = self.interval_seconds()
            if interval <= 0:
                interval = 60.0
            self.logger.debug("守护：%d 秒后执行下一次检查", int(interval))
            if self._stop_event.wait(interval):
                break
            try:
                self.run_once()
            except Exception as exc:  # 守护线程不能因单轮异常退出
                self.logger.error("守护：检查过程出现未预期异常：%s", exc)

    def start(self):
        """启动后台守护线程；重复调用返回同一线程。"""
        if self._thread is not None and self._thread.is_alive():
            return self._thread
        self._stop_event.clear()
        thread = threading.Thread(target=self._loop, name=THREAD_NAME)
        thread.daemon = True
        self._thread = thread
        thread.start()
        self.logger.info(
            "掉线守护已启动：每 %d 分钟检查一次（可在设置中修改）",
            self.interval_minutes(),
        )
        return thread

    def stop(self, timeout=2.0):
        """停止守护线程。"""
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)
        self._thread = None
        self.logger.info("掉线守护已停止")

    def is_running(self) -> bool:
        return bool(self._thread is not None and self._thread.is_alive())

    def wait(self, seconds):
        """可被 stop() 打断的等待；返回 True 表示收到停止信号。"""
        return self._stop_event.wait(float(seconds))

    def sleep(self, seconds):
        """使用注入的 sleep 函数等待（测试用）。"""
        self._sleep(float(seconds))
