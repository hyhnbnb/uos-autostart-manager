# -*- coding: utf-8 -*-
"""应用标识常量与 XDG 路径工具。

本模块不做 I/O 副作用（``ensure_dirs()`` 除外）。所有路径函数都在调用时
即时读取环境变量，因此测试可以通过 ``HOME`` / ``XDG_*`` 环境变量把全部
读写重定向到临时目录。
"""

import glob
import os
import sys
from datetime import datetime
from typing import List, Optional

APP_ID: str = "uos-autostart-manager"
APP_NAME: str = "统信 UOS 开机自启动管理器"
APP_SHORT: str = "UOS 自启动管理器"
VERSION: str = "1.1.0"
MAINTAINER: str = "UOS Autostart Manager <uos-autostart@example.com>"

_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _abs(path):
    """展开 ``~`` 并返回绝对路径字符串。"""
    return os.path.abspath(os.path.expanduser(os.fspath(path)))


def _package_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _xdg_base(env_name, fallback_parts):
    value = os.environ.get(env_name)
    if value:
        return _abs(value)
    return os.path.join(home(), *fallback_parts)


def home() -> str:
    """用户主目录：环境变量 HOME → USERPROFILE → ``expanduser("~")``。"""
    for name in ("HOME", "USERPROFILE"):
        value = os.environ.get(name)
        if value:
            return _abs(value)
    return _abs(os.path.expanduser("~"))


def config_dir() -> str:
    return os.path.join(_xdg_base("XDG_CONFIG_HOME", (".config",)), APP_ID)


def config_path() -> str:
    return os.path.join(config_dir(), "config.json")


def data_dir() -> str:
    return os.path.join(_xdg_base("XDG_DATA_HOME", (".local", "share")), APP_ID)


def log_dir() -> str:
    return os.path.join(data_dir(), "logs")


def log_path() -> str:
    return os.path.join(log_dir(), "manager.log")


def state_dir() -> str:
    return os.path.join(data_dir(), "state")


def autostart_dir() -> str:
    return os.path.join(_xdg_base("XDG_CONFIG_HOME", (".config",)), "autostart")


def applications_dirs() -> List[str]:
    """应用目录列表：只返回已存在的目录，顺序固定并去重。"""
    result = []
    seen = set()

    def _add(directory):
        absolute = _abs(directory)
        key = os.path.normcase(absolute)
        if key in seen:
            return
        if os.path.isdir(absolute):
            seen.add(key)
            result.append(absolute)

    _add(os.path.join(home(), ".local", "share", "applications"))
    data_dirs = os.environ.get("XDG_DATA_DIRS", "")
    for part in data_dirs.split(os.pathsep):
        part = part.strip()
        if part:
            _add(os.path.join(part, "applications"))
    _add("/usr/local/share/applications")
    _add("/usr/share/applications")
    return result


def system_autostart_path() -> str:
    return "/etc/xdg/autostart/%s.desktop" % APP_ID


def manager_bin_path() -> str:
    return "/usr/bin/%s" % APP_ID


def is_installed() -> bool:
    return os.path.isfile(manager_bin_path())


def assets_dir() -> Optional[str]:
    """源码运行时的 assets/ 目录；不存在则返回 None。"""
    package_dir = _package_dir()
    candidates = [
        os.path.join(os.path.dirname(os.path.dirname(package_dir)), "assets"),
        os.path.join(os.path.dirname(package_dir), "assets"),
    ]
    for candidate in candidates:
        if os.path.isdir(candidate):
            return _abs(candidate)
    return None


def icon_paths() -> List[str]:
    """已存在的图标候选：源码 assets 优先（大尺寸 PNG 优先，SVG 兜底），其次系统图标主题。"""
    result = []
    base = assets_dir()
    if base:
        candidates = [os.path.join(base, "icon-%d.png" % size)
                      for size in reversed(_ICON_SIZES)]
        candidates.append(os.path.join(base, "icon.png"))
        candidates.append(os.path.join(base, "icon.svg"))
        for candidate in candidates:
            if os.path.isfile(candidate) and candidate not in result:
                result.append(candidate)
    for ext in ("png", "svg"):
        pattern = "/usr/share/icons/hicolor/*/apps/%s.%s" % (APP_ID, ext)
        try:
            found = sorted(glob.glob(pattern))
        except OSError:
            found = []
        for candidate in found:
            if os.path.isfile(candidate) and candidate not in result:
                result.append(candidate)
    return result


def icon_path() -> Optional[str]:
    candidates = icon_paths()
    if candidates:
        return candidates[0]
    return None


def icon_name() -> str:
    return APP_ID


def module_root() -> str:
    if is_installed():
        return "/usr/lib/%s" % APP_ID
    return os.path.dirname(_package_dir())


def manager_launch_command() -> List[str]:
    if is_installed():
        return [manager_bin_path(), "--tray"]
    return [
        "env",
        "PYTHONPATH=%s" % module_root(),
        sys.executable,
        "-m",
        "uos_autostart",
        "--tray",
    ]


def ensure_dirs() -> None:
    for directory in (config_dir(), data_dir(), log_dir(), state_dir(), autostart_dir()):
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError:
            pass


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()
