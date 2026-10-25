# -*- coding: utf-8 -*-
""".desktop 文件的解析、生成与应用发现。"""

import os
import re
import shlex
import shutil
from dataclasses import dataclass
from typing import Dict, List, Optional

from . import paths
from .model import AutostartItem

_FIELD_CODE_RE = re.compile(r"%[fFuUickdDnNvm%]")
_WHITESPACE_RE = re.compile(r"\s+")
_EXEC_QUOTE_CHARS = set(" \t\n\r\"'`$\\<>~|&;*?#()[]{}!")
_EXEC_ESCAPE_CHARS = set('"`$\\')
_DEFAULT_COMMENT = "由 UOS 自启动管理器托管"


@dataclass
class AppEntry:
    """从 .desktop 文件发现的一个已安装应用。"""

    desktop_id: str
    name: str
    command: str
    icon: str = ""
    comment: str = ""
    path: str = ""
    terminal: bool = False
    categories: str = ""

    def display_label(self) -> str:
        if self.comment:
            return "%s — %s" % (self.name, self.comment)
        return self.name


def parse_desktop_file(path: str) -> Dict[str, str]:
    """只解析 [Desktop Entry] 组；文件不存在或读取失败返回空字典。"""
    data = {}
    group = None
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
            lines = handle.readlines()
    except (OSError, IOError):
        return {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            group = line[1:-1].strip()
            continue
        if group != "Desktop Entry":
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def strip_field_codes(command: str) -> str:
    """去掉 %f %F %u %U %i %c %k %d %D %n %N %v %m %% 并压缩空白。"""
    if command is None:
        return ""
    text = _FIELD_CODE_RE.sub(" ", str(command))
    return _WHITESPACE_RE.sub(" ", text).strip()


def split_command(command: str) -> List[str]:
    """shlex 切分命令行；解析异常时退化为 str.split()。"""
    if command is None:
        return []
    text = str(command).strip()
    if not text:
        return []
    try:
        return shlex.split(text, posix=True)
    except ValueError:
        return text.split()


def quote_exec_arg(arg: str) -> str:
    """按 Desktop Entry 规范给 Exec 参数加引号并转义。"""
    text = "" if arg is None else str(arg)
    if not text:
        return '""'
    needs_quote = False
    for char in text:
        if char in _EXEC_QUOTE_CHARS:
            needs_quote = True
            break
    if not needs_quote:
        return text
    parts = ['"']
    for char in text:
        if char in _EXEC_ESCAPE_CHARS:
            parts.append("\\")
        parts.append(char)
    parts.append('"')
    return "".join(parts)


def exec_line(argv: List[str]) -> str:
    if not argv:
        return ""
    return " ".join(quote_exec_arg(arg) for arg in argv)


def looks_like_executable(name: str) -> bool:
    """含路径分隔符时检查文件存在且可执行；否则在 PATH 中查找。"""
    text = "" if name is None else str(name).strip()
    if not text:
        return False
    has_separator = os.sep in text
    if os.altsep:
        has_separator = has_separator or os.altsep in text
    if has_separator or text.startswith("~"):
        candidate = os.path.expanduser(text)
        if not os.path.isfile(candidate):
            return False
        if os.name == "nt":
            return True
        return os.access(candidate, os.X_OK)
    return shutil.which(text) is not None


def sanitize_value(value: str) -> str:
    """去掉换行（替换为空格）、去首尾空白、压缩内部连续空白。"""
    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ")
    return _WHITESPACE_RE.sub(" ", text).strip()


# SPEC 0.7 提到的内部名，保留别名以便其他模块引用。
_sanitize_value = sanitize_value


def _clamp_delay(value) -> int:
    try:
        delay = int(value)
    except (TypeError, ValueError):
        return 0
    if delay < 0:
        return 0
    if delay > 600:
        return 600
    return delay


def build_item_desktop(item: AutostartItem) -> str:
    """生成单个应用条目（xdg 模式）的 .desktop 内容；命令非法时返回 ""。"""
    name = sanitize_value(getattr(item, "name", ""))
    comment = sanitize_value(getattr(item, "comment", "")) or _DEFAULT_COMMENT
    # 防御性清理字段码：模型里可能仍残留 %U/%F 等，写盘时必须去掉。
    argv = split_command(strip_field_codes(getattr(item, "command", "")))
    exec_value = exec_line(argv)
    if not exec_value:
        return ""
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Version=1.0",
        "Name=%s" % name,
        "Comment=%s" % comment,
        "Exec=%s" % exec_value,
    ]
    icon = sanitize_value(getattr(item, "icon", ""))
    if icon:
        lines.append("Icon=%s" % icon)
    workdir = sanitize_value(getattr(item, "workdir", ""))
    if workdir:
        lines.append("Path=%s" % workdir)
    lines.append("Terminal=%s" % ("true" if bool(getattr(item, "terminal", False)) else "false"))
    lines.append("Hidden=false")
    lines.append("X-GNOME-Autostart-enabled=true")
    lines.append("X-GNOME-Autostart-Delay=%d" % _clamp_delay(getattr(item, "delay", 0)))
    lines.append("X-UOS-Autostart-Manager=true")
    return "\n".join(lines) + "\n"


def build_manager_desktop(command: List[str], hidden: bool) -> str:
    """生成管理器自启动条目的 .desktop 内容。"""
    hidden = bool(hidden)
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Version=1.0",
        "Name=%s" % paths.APP_NAME,
        "Comment=管理开机自启动应用",
        "Exec=%s" % exec_line(command or []),
        "Icon=%s" % paths.icon_name(),
        "Terminal=false",
        "Hidden=%s" % ("true" if hidden else "false"),
        "X-GNOME-Autostart-enabled=%s" % ("false" if hidden else "true"),
        "X-UOS-Autostart-Manager=true",
    ]
    return "\n".join(lines) + "\n"


def desktop_file_name(item_id: str) -> str:
    return "uos-am-%s.desktop" % item_id


def _is_true(value) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in ("true", "1", "yes")


def _desktop_paths_in_dir(directory: str) -> List[str]:
    """列出目录及其一层子目录中的 *.desktop 文件。"""
    result = []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return result
    for name in names:
        full_path = os.path.join(directory, name)
        try:
            if os.path.isdir(full_path):
                try:
                    sub_names = sorted(os.listdir(full_path))
                except OSError:
                    continue
                for sub_name in sub_names:
                    if not sub_name.endswith(".desktop"):
                        continue
                    sub_path = os.path.join(full_path, sub_name)
                    if os.path.isfile(sub_path):
                        result.append(sub_path)
            elif name.endswith(".desktop") and os.path.isfile(full_path):
                result.append(full_path)
        except OSError:
            continue
    return result


def _app_entry_from_file(file_path: str) -> Optional[AppEntry]:
    data = parse_desktop_file(file_path)
    if not data:
        return None
    if data.get("Type", "").strip() != "Application":
        return None
    if _is_true(data.get("NoDisplay")):
        return None
    if _is_true(data.get("Hidden")):
        return None
    exec_value = strip_field_codes(data.get("Exec", ""))
    try_exec = strip_field_codes(data.get("TryExec", ""))
    command = exec_value or try_exec
    if not command:
        return None
    desktop_id = os.path.basename(file_path)
    name = sanitize_value(data.get("Name", ""))
    if not name:
        name = desktop_id
        if name.endswith(".desktop"):
            name = name[: -len(".desktop")]
    return AppEntry(
        desktop_id=desktop_id,
        name=name,
        command=command,
        icon=sanitize_value(data.get("Icon", "")),
        comment=sanitize_value(data.get("Comment", "")),
        path=os.path.abspath(file_path),
        terminal=_is_true(data.get("Terminal")),
        categories=sanitize_value(data.get("Categories", "")),
    )


def discover_applications(extra_dirs: Optional[List[str]] = None) -> List[AppEntry]:
    """发现已安装应用：extra_dirs 优先，然后 paths.applications_dirs()。"""
    directories = []
    for directory in extra_dirs or []:
        if directory and directory not in directories:
            directories.append(directory)
    for directory in paths.applications_dirs():
        if directory not in directories:
            directories.append(directory)
    entries = []
    seen_ids = set()
    for directory in directories:
        if not os.path.isdir(directory):
            continue
        for file_path in _desktop_paths_in_dir(directory):
            entry = _app_entry_from_file(file_path)
            if entry is None:
                continue
            if entry.desktop_id in seen_ids:
                continue
            seen_ids.add(entry.desktop_id)
            entries.append(entry)
    entries.sort(key=lambda entry: entry.name.lower())
    return entries
