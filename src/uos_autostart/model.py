# -*- coding: utf-8 -*-
"""数据模型：AutostartItem（单个自启动项）与 Config（整体配置）。"""

import secrets
from dataclasses import dataclass, field, fields
from typing import Any, Dict, List, Optional

from .paths import now_iso

VALID_MODES = ("manager", "xdg")
MODE_MANAGER = "manager"
MODE_XDG = "xdg"
MIN_DELAY = 0
MAX_DELAY = 600
MIN_WATCHDOG_INTERVAL = 1
MAX_WATCHDOG_INTERVAL = 1440
DEFAULT_WATCHDOG_INTERVAL = 20


def _as_str(value):
    if value is None:
        return ""
    return str(value)


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp_delay(value):
    delay = _as_int(value, 0)
    if delay < MIN_DELAY:
        return MIN_DELAY
    if delay > MAX_DELAY:
        return MAX_DELAY
    return delay


def _clamp_watchdog_interval(value):
    minutes = _as_int(value, DEFAULT_WATCHDOG_INTERVAL)
    if minutes < MIN_WATCHDOG_INTERVAL:
        return MIN_WATCHDOG_INTERVAL
    if minutes > MAX_WATCHDOG_INTERVAL:
        return MAX_WATCHDOG_INTERVAL
    return minutes


def _as_mode(value):
    mode = _as_str(value).strip()
    if mode in VALID_MODES:
        return mode
    return MODE_MANAGER


@dataclass
class AutostartItem:
    """一个开机自启动条目。"""

    id: str
    name: str
    command: str
    workdir: str = ""
    icon: str = ""
    comment: str = ""
    enabled: bool = True
    watchdog: bool = False
    delay: int = 0
    mode: str = "manager"
    terminal: bool = False
    created_at: str = ""
    last_run_at: str = ""
    run_count: int = 0

    @classmethod
    def new(cls, name: str, command: str, **kwargs) -> "AutostartItem":
        """创建新条目：生成 id / created_at，归一化 delay，忽略未知参数。"""
        known = set(item_field.name for item_field in fields(cls))
        values = {}
        for key, value in kwargs.items():
            if key in known and key not in ("id", "created_at", "name", "command"):
                values[key] = value
        item = cls(
            id=secrets.token_hex(6),
            name=_as_str(name),
            command=_as_str(command),
            **values
        )
        if not item.id:
            item.id = secrets.token_hex(6)
        item.name = _as_str(item.name)
        item.command = _as_str(item.command)
        item.workdir = _as_str(item.workdir)
        item.icon = _as_str(item.icon)
        item.comment = _as_str(item.comment)
        item.enabled = bool(item.enabled)
        item.watchdog = bool(item.watchdog)
        item.delay = _clamp_delay(item.delay)
        item.mode = _as_mode(item.mode)
        item.terminal = bool(item.terminal)
        item.created_at = _as_str(item.created_at) or now_iso()
        item.last_run_at = _as_str(item.last_run_at)
        item.run_count = _as_int(item.run_count, 0)
        return item

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": _as_str(self.id),
            "name": _as_str(self.name),
            "command": _as_str(self.command),
            "workdir": _as_str(self.workdir),
            "icon": _as_str(self.icon),
            "comment": _as_str(self.comment),
            "enabled": bool(self.enabled),
            "watchdog": bool(self.watchdog),
            "delay": _clamp_delay(self.delay),
            "mode": _as_mode(self.mode),
            "terminal": bool(self.terminal),
            "created_at": _as_str(self.created_at),
            "last_run_at": _as_str(self.last_run_at),
            "run_count": _as_int(self.run_count, 0),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AutostartItem":
        """容错构造：缺字段用默认值，脏数据尽量修正而不抛异常。"""
        if not isinstance(data, dict):
            data = {}
        item_id = _as_str(data.get("id")).strip()
        if not item_id:
            item_id = secrets.token_hex(6)
        return cls(
            id=item_id,
            name=_as_str(data.get("name")),
            command=_as_str(data.get("command")),
            workdir=_as_str(data.get("workdir")),
            icon=_as_str(data.get("icon")),
            comment=_as_str(data.get("comment")),
            enabled=bool(data.get("enabled", True)),
            watchdog=bool(data.get("watchdog", False)),
            delay=_clamp_delay(data.get("delay", 0)),
            mode=_as_mode(data.get("mode")),
            terminal=bool(data.get("terminal", False)),
            created_at=_as_str(data.get("created_at")),
            last_run_at=_as_str(data.get("last_run_at")),
            run_count=_as_int(data.get("run_count", 0)),
        )

    def validate(self) -> List[str]:
        """返回中文错误信息列表；空列表表示合法。"""
        errors = []
        if not _as_str(self.name).strip():
            errors.append("名称不能为空")
        if not _as_str(self.command).strip():
            errors.append("命令不能为空")
        try:
            delay = int(self.delay)
        except (TypeError, ValueError):
            delay = -1
        if delay < MIN_DELAY or delay > MAX_DELAY:
            errors.append("启动延迟必须是 0 到 %d 秒之间的整数" % MAX_DELAY)
        if _as_str(self.mode) not in VALID_MODES:
            errors.append("启动方式无效，只能是 manager 或 xdg")
        return errors

    def display_mode(self) -> str:
        if _as_str(self.mode) == MODE_XDG:
            return "系统直接启动"
        return "管理器启动"

    def display_delay(self) -> str:
        delay = _clamp_delay(self.delay)
        if delay <= 0:
            return "立即"
        return "延迟 %d 秒" % delay

    def display_watchdog(self) -> str:
        return "开启" if bool(self.watchdog) else "关闭"


@dataclass
class Config:
    """全局配置（JSON 顶层结构）。"""

    version: int = 1
    manager_autostart: bool = True
    start_minimized: bool = False
    close_to_tray: bool = True
    global_delay: int = 0
    watchdog_enabled: bool = True
    watchdog_interval_minutes: int = DEFAULT_WATCHDOG_INTERVAL
    items: List[AutostartItem] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        serialized_items = []
        for item in self.items:
            if isinstance(item, AutostartItem):
                serialized_items.append(item.to_dict())
        return {
            "version": 1,
            "manager_autostart": bool(self.manager_autostart),
            "start_minimized": bool(self.start_minimized),
            "close_to_tray": bool(self.close_to_tray),
            "global_delay": _clamp_delay(self.global_delay),
            "watchdog_enabled": bool(self.watchdog_enabled),
            "watchdog_interval_minutes": _clamp_watchdog_interval(
                self.watchdog_interval_minutes
            ),
            "items": serialized_items,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        if not isinstance(data, dict):
            data = {}
        items = []
        raw_items = data.get("items")
        if isinstance(raw_items, list):
            for entry in raw_items:
                if isinstance(entry, dict):
                    items.append(AutostartItem.from_dict(entry))
        return cls(
            version=1,
            manager_autostart=bool(data.get("manager_autostart", True)),
            start_minimized=bool(data.get("start_minimized", False)),
            close_to_tray=bool(data.get("close_to_tray", True)),
            global_delay=_clamp_delay(data.get("global_delay", 0)),
            watchdog_enabled=bool(data.get("watchdog_enabled", True)),
            watchdog_interval_minutes=_clamp_watchdog_interval(
                data.get("watchdog_interval_minutes", DEFAULT_WATCHDOG_INTERVAL)
            ),
            items=items,
        )

    def find(self, item_id: str) -> Optional[AutostartItem]:
        for item in self.items:
            if item.id == item_id:
                return item
        return None

    def enabled_items(self) -> List[AutostartItem]:
        return [item for item in self.items if item.enabled]

    def watchdog_items(self) -> List[AutostartItem]:
        """需要掉线守护且已启用的条目。"""
        return [item for item in self.items if item.enabled and bool(item.watchdog)]

    def index_of(self, item_id: str) -> int:
        for index, item in enumerate(self.items):
            if item.id == item_id:
                return index
        return -1

    def remove_item(self, item_id: str) -> bool:
        index = self.index_of(item_id)
        if index < 0:
            return False
        del self.items[index]
        return True

    def move_item(self, item_id: str, delta: int) -> bool:
        if delta not in (-1, 1):
            return False
        index = self.index_of(item_id)
        if index < 0:
            return False
        target = index + delta
        if target < 0 or target >= len(self.items):
            return False
        item = self.items.pop(index)
        self.items.insert(target, item)
        return True
