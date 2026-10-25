# -*- coding: utf-8 -*-
"""XDG 自启动文件同步（~/.config/autostart 下的 .desktop 文件）。

所有写盘操作都带异常兜底：sync() 把错误收集到返回值的 errors 中，
不向 GUI 抛异常。
"""

import logging
import os
from typing import Any, Dict, List

from . import desktop, paths
from .model import AutostartItem, Config

LOG = logging.getLogger("uos_autostart")

MANAGER_DESKTOP_FILE = "uos-autostart-manager.desktop"
ITEM_PREFIX = "uos-am-"


def manager_override_path() -> str:
    return os.path.join(paths.autostart_dir(), MANAGER_DESKTOP_FILE)


def item_file_path(item_id: str) -> str:
    return os.path.join(paths.autostart_dir(), desktop.desktop_file_name(item_id))


def _write_desktop(path: str, content: str, mode: int = 0o644) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = "%s.tmp.%d" % (path, os.getpid())
    try:
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        try:
            os.chmod(tmp_path, mode)
        except OSError:
            pass
        os.replace(tmp_path, path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise


def write_item_file(item: AutostartItem) -> str:
    """写入条目对应的自启动文件，返回路径；命令非法或写入失败返回 ""。"""
    content = desktop.build_item_desktop(item)
    if not content:
        return ""
    path = item_file_path(item.id)
    try:
        _write_desktop(path, content)
    except Exception as exc:
        LOG.error("写入自启动文件失败 %s：%s", path, exc)
        return ""
    return path


def remove_item_file(item_id: str) -> bool:
    path = item_file_path(item_id)
    try:
        os.remove(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        LOG.error("删除自启动文件失败 %s：%s", path, exc)
        return False
    return True


def sync(cfg: Config, dry_run: bool = False) -> Dict[str, Any]:
    """把 enabled 且 mode="xdg" 的条目的文件写入 ~/.config/autostart。

    返回 {"written": [...], "removed": [...], "errors": [...]}；绝不抛异常，
    也绝不触碰管理器自启动文件。
    """
    result = {"written": [], "removed": [], "errors": []}
    autostart_dir = paths.autostart_dir()
    desired = {}
    for item in getattr(cfg, "items", None) or []:
        if not isinstance(item, AutostartItem):
            continue
        if not item.enabled or item.mode != "xdg":
            continue
        desired[item_file_path(item.id)] = item

    if not dry_run:
        try:
            os.makedirs(autostart_dir, exist_ok=True)
        except OSError as exc:
            result["errors"].append("无法创建自启动目录 %s：%s" % (autostart_dir, exc))

    for path, item in desired.items():
        content = desktop.build_item_desktop(item)
        if not content:
            result["errors"].append(
                "条目「%s」的命令无效，未生成自启动文件" % (item.name or item.id)
            )
            continue
        if dry_run:
            result["written"].append(path)
            continue
        written_path = write_item_file(item)
        if written_path:
            result["written"].append(written_path)
        else:
            result["errors"].append("写入自启动文件失败：%s" % path)

    try:
        names = sorted(os.listdir(autostart_dir))
    except OSError:
        names = []
    for name in names:
        if not name.startswith(ITEM_PREFIX) or not name.endswith(".desktop"):
            continue
        path = os.path.join(autostart_dir, name)
        if path in desired:
            continue
        if dry_run:
            result["removed"].append(path)
            continue
        try:
            os.remove(path)
            result["removed"].append(path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            result["errors"].append("删除过期的自启动文件失败 %s：%s" % (path, exc))
    return result


def set_manager_autostart(enabled: bool) -> bool:
    """总是写用户级覆盖文件；enabled=True 写 Hidden=false，否则 Hidden=true。"""
    content = desktop.build_manager_desktop(
        paths.manager_launch_command(), hidden=not bool(enabled)
    )
    path = manager_override_path()
    try:
        _write_desktop(path, content)
    except Exception as exc:
        LOG.error("写入管理器自启动文件失败 %s：%s", path, exc)
        return False
    return True


def manager_autostart_enabled() -> bool:
    path = manager_override_path()
    if os.path.isfile(path):
        data = desktop.parse_desktop_file(path)
        hidden = str(data.get("Hidden", "")).strip().lower() == "true"
        return not hidden
    return os.path.isfile(paths.system_autostart_path())


def audit(cfg: Config) -> List[Dict[str, str]]:
    """返回诊断信息列表：[{"level": "ok"|"warn"|"error", "message": "..."}]。"""
    from .launcher import command_available  # 延迟导入，避免循环依赖

    findings = []

    def _add(level, message):
        findings.append({"level": level, "message": message})

    try:
        if manager_autostart_enabled():
            _add("ok", "管理器开机自启动已启用")
        else:
            _add("warn", "管理器开机自启动未启用，可在“设置”中开启")
    except Exception as exc:
        _add("error", "检查管理器自启动状态失败：%s" % exc)

    items = getattr(cfg, "items", None) or []
    checked = 0
    for item in items:
        if not isinstance(item, AutostartItem):
            continue
        checked += 1
        label = item.name or item.id
        if not item.enabled:
            _add("ok", "「%s」已禁用，跳过检查" % label)
            continue
        argv = desktop.split_command(item.command)
        if not argv:
            _add("error", "「%s」的命令为空，无法启动" % label)
        else:
            ok, detail = command_available(item.command)
            if ok:
                _add("ok", "「%s」命令可用：%s" % (label, detail))
            else:
                _add("warn", "「%s」命令不可用：%s" % (label, detail))
        if item.mode == "xdg":
            path = item_file_path(item.id)
            if not os.path.isfile(path):
                _add("error", "「%s」的系统自启动文件缺失，请执行同步" % label)
                continue
            data = desktop.parse_desktop_file(path)
            if str(data.get("Hidden", "")).strip().lower() == "true":
                _add("error", "「%s」的系统自启动文件被标记为隐藏" % label)
            elif str(data.get("X-GNOME-Autostart-enabled", "")).strip().lower() == "false":
                _add("warn", "「%s」的系统自启动未被桌面环境启用" % label)
            else:
                _add("ok", "「%s」系统自启动文件正常" % label)
    if checked == 0:
        _add("warn", "尚未添加任何自启动项")
    return findings
