# -*- coding: utf-8 -*-
"""配置持久化：ConfigStore。

只负责 JSON 读写，不调用 autostart.sync（由 app / GUI 显式触发），
以避免模块循环依赖。内部使用 threading.RLock 保护，GUI 线程与启动线程
可以并发访问。
"""

import json
import logging
import os
import threading
from datetime import datetime
from typing import Any, List, Optional

from . import paths
from .model import AutostartItem, Config, MAX_DELAY, MAX_WATCHDOG_INTERVAL, MIN_WATCHDOG_INTERVAL

LOG = logging.getLogger("uos_autostart")

_OPTION_KEYS = (
    "manager_autostart",
    "start_minimized",
    "close_to_tray",
    "global_delay",
    "watchdog_enabled",
    "watchdog_interval_minutes",
)
_MAX_DELAY = MAX_DELAY


def _clamp_delay(value: Any) -> int:
    try:
        delay = int(value)
    except (TypeError, ValueError):
        return 0
    if delay < 0:
        return 0
    if delay > _MAX_DELAY:
        return _MAX_DELAY
    return delay


def _clamp_watchdog_interval(value: Any) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return 20
    if minutes < MIN_WATCHDOG_INTERVAL:
        return MIN_WATCHDOG_INTERVAL
    if minutes > MAX_WATCHDOG_INTERVAL:
        return MAX_WATCHDOG_INTERVAL
    return minutes


class ConfigStore(object):
    """config.json 的读写封装（原子写 + 损坏自动备份）。"""

    def __init__(self, path: Optional[str] = None) -> None:
        if path:
            self._path = os.path.abspath(path)
        else:
            self._path = paths.config_path()
        self._lock = threading.RLock()

    @property
    def path(self) -> str:
        return self._path

    def load(self) -> Config:
        with self._lock:
            if not os.path.isfile(self._path):
                return Config()
            try:
                # utf-8-sig：容忍用户用 Windows 编辑器保存时带上的 BOM
                with open(self._path, "r", encoding="utf-8-sig") as handle:
                    data = json.loads(handle.read())
            except (OSError, ValueError) as exc:
                self._quarantine_corrupt(exc)
                return Config()
            if not isinstance(data, dict):
                self._quarantine_corrupt(ValueError("配置文件顶层不是 JSON 对象"))
                return Config()
            try:
                return Config.from_dict(data)
            except Exception as exc:  # 极端脏数据也不应向 GUI 抛异常
                self._quarantine_corrupt(exc)
                return Config()

    def save(self, cfg: Config) -> None:
        with self._lock:
            parent = os.path.dirname(self._path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            text = json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2)
            tmp_path = "%s.tmp.%d" % (self._path, os.getpid())
            try:
                with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(text)
                    handle.write("\n")
                    handle.flush()
                    try:
                        os.fsync(handle.fileno())
                    except OSError:
                        pass
                try:
                    os.chmod(tmp_path, 0o600)
                except OSError:
                    pass
                os.replace(tmp_path, self._path)
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass

    def load_items(self) -> List[AutostartItem]:
        return self.load().items

    def get(self, item_id: str) -> Optional[AutostartItem]:
        return self.load().find(item_id)

    def add(self, item: AutostartItem) -> AutostartItem:
        with self._lock:
            cfg = self.load()
            cfg.items.append(item)
            self.save(cfg)
            return item

    def update(self, item: AutostartItem) -> bool:
        with self._lock:
            cfg = self.load()
            index = cfg.index_of(item.id)
            if index < 0:
                return False
            cfg.items[index] = item
            self.save(cfg)
            return True

    def remove(self, item_id: str) -> bool:
        with self._lock:
            cfg = self.load()
            removed = cfg.remove_item(item_id)
            if removed:
                self.save(cfg)
            return removed

    def move(self, item_id: str, delta: int) -> bool:
        with self._lock:
            cfg = self.load()
            moved = cfg.move_item(item_id, delta)
            if moved:
                self.save(cfg)
            return moved

    def set_option(self, key: str, value: Any) -> Config:
        with self._lock:
            cfg = self.load()
            if key not in _OPTION_KEYS:
                return cfg
            if key == "global_delay":
                cfg.global_delay = _clamp_delay(value)
            elif key == "watchdog_interval_minutes":
                cfg.watchdog_interval_minutes = _clamp_watchdog_interval(value)
            else:
                setattr(cfg, key, bool(value))
            self.save(cfg)
            return cfg

    def set_manager_autostart(self, enabled: bool) -> Config:
        return self.set_option("manager_autostart", bool(enabled))

    def reset(self) -> Config:
        with self._lock:
            cfg = Config()
            self.save(cfg)
            return cfg

    def _quarantine_corrupt(self, exc: Any) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = "%s.corrupt.%s" % (self._path, stamp)
        try:
            os.replace(self._path, backup_path)
            LOG.warning("配置文件损坏，已备份为 %s：%s", backup_path, exc)
        except OSError as error:
            LOG.error("配置文件损坏且无法备份（%s）：%s", error, exc)
