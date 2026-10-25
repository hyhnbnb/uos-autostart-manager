# -*- coding: utf-8 -*-
"""tkinter 兜底界面。

功能子集但完整可用：列表、添加命令、添加应用（简易列表）、编辑、删除、
上移/下移、启用勾选（双击切换或右键菜单）、全部启动、设置（简化）、退出。

本模块顶层不导入 tkinter，``available()`` 内部才尝试导入，因此缺少 tkinter
时也不会影响 ``import uos_autostart.app`` 以及 CLI 模式。
"""

import os
import sys

from . import autostart, desktop, launcher, paths
from . import watchdog as watchdog_module
from .model import AutostartItem, Config

# (列标识, 表头, 初始宽度)
COLUMNS = (
    ("enabled", "启用", 70),
    ("name", "名称", 220),
    ("mode", "启动方式", 120),
    ("delay", "延迟", 90),
    ("watchdog", "守护", 70),
    ("command", "命令", 420),
    ("last", "最近启动", 160),
)

_LEVEL_MARKS = {"ok": "[正常]", "warn": "[警告]", "error": "[错误]"}


def available():
    """tkinter 是否可用。"""
    try:
        __import__("tkinter")
    except Exception:
        return False
    return True


def _load_tk():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    return tk, ttk, filedialog, messagebox


def _safe_str(value):
    return "" if value is None else str(value)


# ---------------------------------------------------------------------------
# 通用对话框基类
# ---------------------------------------------------------------------------
class _Dialog(object):
    """Tk 模态对话框基类：子类实现 build() 与 on_save()。"""

    title = ""
    ok_text = "保存"
    cancel_text = "取消"

    def __init__(self, window):
        self.window = window
        self._tk = window._tk
        self._ttk = window._ttk
        self._filedialog = window._filedialog
        self._messagebox = window._messagebox
        self.top = self._tk.Toplevel(window.root)
        self.top.title(self.title or paths.APP_NAME)
        self.top.transient(window.root)
        self.top.protocol("WM_DELETE_WINDOW", self.cancel)
        self.body = self._tk.Frame(self.top, padx=10, pady=10)
        self.body.pack(fill="both", expand=True)
        self.build(self.body)
        button_row = self._tk.Frame(self.top)
        button_row.pack(fill="x", padx=10, pady=(0, 10))
        self._ttk.Button(
            button_row, text=self.cancel_text, command=self.cancel
        ).pack(side="right", padx=4)
        self._ttk.Button(button_row, text=self.ok_text, command=self.on_save).pack(
            side="right", padx=4
        )
        try:
            self.top.resizable(True, True)
        except Exception:
            pass

    def build(self, body):  # pragma: no cover - 由子类实现
        raise NotImplementedError

    def on_save(self):  # pragma: no cover - 由子类实现
        self.top.destroy()

    def cancel(self):
        self.top.destroy()

    def show_modal(self):
        try:
            self.top.wait_visibility()
        except Exception:
            pass
        try:
            self.top.grab_set()
        except Exception:
            pass
        try:
            self.top.focus_set()
            self.top.wait_window()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 条目表单
# ---------------------------------------------------------------------------
class ItemDialog(_Dialog):
    """自启动条目表单。"""

    title = "编辑自启动项"

    def __init__(self, window, item=None, defaults=None):
        self.item = item
        self._defaults = dict(defaults or {})
        self.result_item = None
        super(ItemDialog, self).__init__(window)

    def _label_row(self, parent, text):
        row = self._tk.Frame(parent)
        row.pack(fill="x", pady=2)
        self._ttk.Label(row, text=text, width=12, anchor="w").pack(side="left")
        return row

    def build(self, body):
        self.name_var = self._tk.StringVar()
        self.cmd_var = self._tk.StringVar()
        self.args_var = self._tk.StringVar()
        self.workdir_var = self._tk.StringVar()
        self.icon_var = self._tk.StringVar()
        self.delay_var = self._tk.StringVar(value="0")
        self.mode_var = self._tk.StringVar(value="管理器启动")
        self.terminal_var = self._tk.BooleanVar(value=False)
        self.watchdog_var = self._tk.BooleanVar(value=False)
        self.comment_var = self._tk.StringVar()

        row = self._label_row(body, "名称")
        self._ttk.Entry(row, textvariable=self.name_var).pack(
            side="left", fill="x", expand=True
        )

        row = self._label_row(body, "命令")
        self._ttk.Entry(row, textvariable=self.cmd_var).pack(
            side="left", fill="x", expand=True
        )
        self._ttk.Button(row, text="浏览…", width=6, command=self._browse_command).pack(
            side="left", padx=(4, 0)
        )

        row = self._label_row(body, "参数")
        self._ttk.Entry(row, textvariable=self.args_var).pack(
            side="left", fill="x", expand=True
        )

        row = self._label_row(body, "工作目录")
        self._ttk.Entry(row, textvariable=self.workdir_var).pack(
            side="left", fill="x", expand=True
        )
        self._ttk.Button(
            row, text="浏览…", width=6, command=self._browse_workdir
        ).pack(side="left", padx=(4, 0))

        row = self._label_row(body, "图标")
        self._ttk.Entry(row, textvariable=self.icon_var).pack(
            side="left", fill="x", expand=True
        )

        row = self._label_row(body, "启动延迟")
        self._tk.Spinbox(
            row, from_=0, to=600, width=8, textvariable=self.delay_var
        ).pack(side="left")
        self._ttk.Label(row, text="秒").pack(side="left", padx=4)

        row = self._label_row(body, "启动方式")
        self._ttk.Combobox(
            row,
            values=("管理器启动", "系统直接启动"),
            state="readonly",
            width=18,
            textvariable=self.mode_var,
        ).pack(side="left")

        row = self._label_row(body, "终端")
        self._ttk.Checkbutton(
            row, text="在终端中运行", variable=self.terminal_var
        ).pack(side="left")

        row = self._label_row(body, "守护")
        self._ttk.Checkbutton(
            row,
            text="掉线自动重启（定时检测，未运行则重新启动）",
            variable=self.watchdog_var,
        ).pack(side="left")

        row = self._label_row(body, "备注")
        self._ttk.Entry(row, textvariable=self.comment_var).pack(
            side="left", fill="x", expand=True
        )

        self._load_values()
        try:
            self.top.title(self.title)
        except Exception:
            pass

    def _load_values(self):
        if self.item is not None:
            self.title = "编辑自启动项"
            self.name_var.set(self.item.name)
            self.cmd_var.set(self.item.command)
            self.workdir_var.set(self.item.workdir)
            self.icon_var.set(self.item.icon)
            self.comment_var.set(self.item.comment)
            self.terminal_var.set(bool(self.item.terminal))
            self.watchdog_var.set(bool(getattr(self.item, "watchdog", False)))
            try:
                delay = max(0, min(600, int(self.item.delay)))
            except Exception:
                delay = 0
            self.delay_var.set(str(delay))
            self.mode_var.set(
                "系统直接启动" if self.item.mode == "xdg" else "管理器启动"
            )
            return
        self.title = "添加自启动项"
        defaults = self._defaults
        self.name_var.set(_safe_str(defaults.get("name")))
        self.cmd_var.set(_safe_str(defaults.get("command")))
        self.workdir_var.set(_safe_str(defaults.get("workdir")))
        self.icon_var.set(_safe_str(defaults.get("icon")))
        self.comment_var.set(_safe_str(defaults.get("comment")))
        self.terminal_var.set(bool(defaults.get("terminal")))
        self.watchdog_var.set(bool(defaults.get("watchdog")))
        try:
            delay = max(0, min(600, int(defaults.get("delay") or 0)))
        except Exception:
            delay = 0
        self.delay_var.set(str(delay))
        self.mode_var.set("管理器启动")

    def _initial_dir(self):
        for candidate in ("/usr/bin", "/usr/local/bin"):
            if os.path.isdir(candidate):
                return candidate
        try:
            return os.path.expanduser("~")
        except Exception:
            return os.getcwd()

    def _browse_command(self):
        try:
            chosen = self._filedialog.askopenfilename(
                parent=self.top, title="选择程序", initialdir=self._initial_dir()
            )
        except Exception:
            return
        if chosen:
            self.cmd_var.set(chosen)

    def _browse_workdir(self):
        try:
            chosen = self._filedialog.askdirectory(
                parent=self.top, title="选择工作目录", initialdir=self._initial_dir()
            )
        except Exception:
            return
        if chosen:
            self.workdir_var.set(chosen)

    def on_save(self):
        name = self.name_var.get().strip()
        command = self.cmd_var.get().strip()
        extra_args = self.args_var.get().strip()
        if extra_args:
            command = (command + " " + extra_args).strip()
        try:
            command = desktop.strip_field_codes(command)
        except Exception:
            pass
        try:
            delay = int(self.delay_var.get())
        except Exception:
            delay = 0
        delay = max(0, min(600, delay))
        mode = "xdg" if self.mode_var.get() == "系统直接启动" else "manager"
        terminal = bool(self.terminal_var.get())
        watchdog = bool(self.watchdog_var.get())
        workdir = self.workdir_var.get().strip()
        icon = self.icon_var.get().strip()
        comment = self.comment_var.get().strip()

        if self.item is not None:
            try:
                merged = dict(self.item.to_dict())
            except Exception:
                merged = {}
            merged.update(
                {
                    "name": name,
                    "command": command,
                    "workdir": workdir,
                    "icon": icon,
                    "comment": comment,
                    "delay": delay,
                    "mode": mode,
                    "terminal": terminal,
                    "watchdog": watchdog,
                }
            )
            try:
                new_item = AutostartItem.from_dict(merged)
            except Exception:
                new_item = AutostartItem.new(name, command)
        else:
            try:
                new_item = AutostartItem.new(
                    name,
                    command,
                    workdir=workdir,
                    icon=icon,
                    comment=comment,
                    delay=delay,
                    mode=mode,
                    terminal=terminal,
                    watchdog=watchdog,
                )
            except Exception:
                new_item = AutostartItem.new(name, command)

        try:
            errors = new_item.validate()
        except Exception:
            errors = []
        if errors:
            self._messagebox.warning(
                "无法保存", "\n".join([str(one) for one in errors]), parent=self.top
            )
            return
        self.result_item = new_item
        self.top.destroy()


# ---------------------------------------------------------------------------
# 添加应用：简易列表
# ---------------------------------------------------------------------------
class AppPickerDialog(_Dialog):
    """从 desktop.discover_applications() 的结果中挑一个应用。"""

    title = "添加应用"
    ok_text = "确定"

    def __init__(self, window, entries):
        self.entries = list(entries or [])
        self._visible_entries = []
        self._exec_cache = {}
        self.result_entry = None
        super(AppPickerDialog, self).__init__(window)

    def build(self, body):
        top = self._tk.Frame(body)
        top.pack(fill="x")
        self._ttk.Label(top, text="搜索").pack(side="left")
        self.search_var = self._tk.StringVar()
        self._ttk.Entry(top, textvariable=self.search_var).pack(
            side="left", fill="x", expand=True, padx=4
        )
        self.only_exec_var = self._tk.BooleanVar(value=False)
        self._ttk.Checkbutton(
            top,
            text="仅显示可执行",
            variable=self.only_exec_var,
            command=self._apply_filter,
        ).pack(side="left", padx=4)
        try:
            self.search_var.trace_add("write", lambda *args: self._apply_filter())
        except Exception:
            try:
                self.search_var.trace("w", lambda *args: self._apply_filter())
            except Exception:
                pass

        frame = self._tk.Frame(body)
        frame.pack(fill="both", expand=True, pady=4)
        self.listbox = self._tk.Listbox(frame, height=14, exportselection=False)
        scrollbar = self._ttk.Scrollbar(
            frame, orient="vertical", command=self.listbox.yview
        )
        self.listbox.configure(yscrollcommand=scrollbar.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.listbox.bind("<Double-1>", lambda _event: self.on_save())
        self._apply_filter()

    def _is_executable(self, entry):
        key = getattr(entry, "desktop_id", "") or id(entry)
        if key in self._exec_cache:
            return self._exec_cache[key]
        result = False
        try:
            argv = desktop.split_command(entry.command)
            if argv:
                result = bool(desktop.looks_like_executable(argv[0]))
        except Exception:
            result = False
        self._exec_cache[key] = result
        return result

    def _apply_filter(self):
        keyword = ""
        try:
            keyword = self.search_var.get().strip().lower()
        except Exception:
            keyword = ""
        only_exec = False
        try:
            only_exec = bool(self.only_exec_var.get())
        except Exception:
            only_exec = False
        self.listbox.delete(0, "end")
        self._visible_entries = []
        for entry in self.entries:
            label = ""
            try:
                label = entry.display_label()
            except Exception:
                label = _safe_str(getattr(entry, "name", ""))
            haystack = "%s %s %s" % (
                label,
                _safe_str(getattr(entry, "command", "")),
                _safe_str(getattr(entry, "desktop_id", "")),
            )
            if keyword and keyword not in haystack.lower():
                continue
            if only_exec and not self._is_executable(entry):
                continue
            self.listbox.insert("end", label)
            self._visible_entries.append(entry)

    def on_save(self):
        try:
            selection = self.listbox.curselection()
        except Exception:
            selection = ()
        if not selection:
            self._messagebox.information(
                "提示", "请先在列表中选择一个应用。", parent=self.top
            )
            return
        try:
            index = int(selection[0])
        except Exception:
            index = -1
        if not (0 <= index < len(self._visible_entries)):
            return
        self.result_entry = self._visible_entries[index]
        self.top.destroy()


# ---------------------------------------------------------------------------
# 设置（简化）
# ---------------------------------------------------------------------------
class SettingsDialog(_Dialog):
    """设置：管理器自启、最小化、关闭到托盘、全局等待。"""

    title = "设置"

    def __init__(self, window, store):
        self.store = store
        try:
            self.cfg = store.load()
        except Exception:
            self.cfg = Config()
        self.saved = False
        super(SettingsDialog, self).__init__(window)

    def build(self, body):
        self.manager_var = self._tk.BooleanVar(value=bool(self.cfg.manager_autostart))
        self.minimized_var = self._tk.BooleanVar(value=bool(self.cfg.start_minimized))
        self.close_tray_var = self._tk.BooleanVar(
            value=bool(self.cfg.close_to_tray)
        )
        self.delay_var = self._tk.StringVar()
        try:
            delay = max(0, min(600, int(self.cfg.global_delay)))
        except Exception:
            delay = 0
        self.delay_var.set(str(delay))

        self._ttk.Checkbutton(
            body, text="管理器开机自启动", variable=self.manager_var
        ).pack(anchor="w", pady=2)
        self._ttk.Checkbutton(
            body, text="开机时最小化到托盘", variable=self.minimized_var
        ).pack(anchor="w", pady=2)
        self._ttk.Checkbutton(
            body, text="关闭窗口时最小化到托盘", variable=self.close_tray_var
        ).pack(anchor="w", pady=2)

        row = self._tk.Frame(body)
        row.pack(fill="x", pady=2)
        self._ttk.Label(row, text="全部启动前等待").pack(side="left")
        self._tk.Spinbox(
            row, from_=0, to=600, width=8, textvariable=self.delay_var
        ).pack(side="left", padx=4)
        self._ttk.Label(row, text="秒").pack(side="left")

        button_row = self._tk.Frame(body)
        button_row.pack(fill="x", pady=6)
        self._ttk.Button(
            button_row, text="立即启用/停用管理器自启", command=self._apply_autostart
        ).pack(side="left")

        # 掉线守护
        self.watchdog_var = self._tk.BooleanVar(
            value=bool(getattr(self.cfg, "watchdog_enabled", True))
        )
        self._ttk.Checkbutton(
            body,
            text="启用掉线自动重启（全局开关）",
            variable=self.watchdog_var,
        ).pack(anchor="w", pady=2)
        self.interval_var = self._tk.StringVar()
        try:
            interval = max(
                1, min(1440, int(getattr(self.cfg, "watchdog_interval_minutes", 20)))
            )
        except Exception:
            interval = 20
        self.interval_var.set(str(interval))
        interval_row = self._tk.Frame(body)
        interval_row.pack(fill="x", pady=2)
        self._ttk.Label(interval_row, text="检测间隔").pack(side="left")
        self._tk.Spinbox(
            interval_row, from_=1, to=1440, width=8, textvariable=self.interval_var
        ).pack(side="left", padx=4)
        self._ttk.Label(interval_row, text="分钟（默认 20）").pack(side="left")

        hint = "配置目录：%s" % (paths.config_dir(),)
        self._ttk.Label(body, text=hint, foreground="#555555").pack(
            anchor="w", pady=(8, 0)
        )

    def _apply_autostart(self):
        enabled = bool(self.manager_var.get())
        ok = False
        try:
            ok = bool(autostart.set_manager_autostart(enabled))
        except Exception:
            ok = False
        try:
            self.store.set_manager_autostart(enabled)
        except Exception:
            pass
        try:
            actual = autostart.manager_autostart_enabled()
            self.manager_var.set(bool(actual))
        except Exception:
            pass
        if ok:
            self._messagebox.information(
                "已生效",
                "管理器开机自启动已%s。" % ("启用" if enabled else "停用"),
                parent=self.top,
            )
        else:
            self._messagebox.warning(
                "未生效", "写入自启动文件失败，请检查目录权限。", parent=self.top
            )

    def on_save(self):
        try:
            if bool(self.manager_var.get()) != bool(self.cfg.manager_autostart):
                self.store.set_manager_autostart(bool(self.manager_var.get()))
            self.store.set_option("start_minimized", bool(self.minimized_var.get()))
            self.store.set_option("close_to_tray", bool(self.close_tray_var.get()))
            try:
                delay = int(self.delay_var.get())
            except Exception:
                delay = 0
            self.store.set_option(
                "global_delay", max(0, min(600, delay))
            )
            try:
                interval = int(self.interval_var.get())
            except Exception:
                interval = 20
            self.store.set_option(
                "watchdog_interval_minutes", max(1, min(1440, interval))
            )
            self.store.set_option(
                "watchdog_enabled", bool(self.watchdog_var.get())
            )
        except Exception as exc:
            self._messagebox.warning(
                "保存失败", "无法保存设置：%s" % (exc,), parent=self.top
            )
            return
        try:
            autostart.set_manager_autostart(bool(self.manager_var.get()))
        except Exception:
            pass
        self.saved = True
        self.top.destroy()


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------
class MainWindow(object):
    """tkinter 主窗口。属性 ``self.tree`` / ``self.status_text`` 供测试使用。"""

    def __init__(self, store, options=None, root=None):
        self.store = store
        self.options = dict(options or {})
        self.cfg = Config()
        self._items = []
        self._force_quit = False

        tk, ttk, filedialog, messagebox = _load_tk()
        self._tk = tk
        self._ttk = ttk
        self._filedialog = filedialog
        self._messagebox = messagebox

        self.root = root if root is not None else tk.Tk()
        self.root.title(paths.APP_NAME)
        try:
            self.root.geometry("1020x580")
        except Exception:
            pass
        try:
            self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        except Exception:
            pass
        self._build_ui()
        self.refresh()

    # ---- 构建 -----------------------------------------------------
    def _button_specs(self):
        return (
            ("add_app", "添加应用", self.add_application),
            ("add_command", "添加命令", self.add_command),
            ("edit", "编辑", self.edit_item),
            ("delete", "删除", self.delete_item),
            ("move_up", "上移", self.move_up),
            ("move_down", "下移", self.move_down),
            ("start_all", "全部启动", self.start_all),
            ("settings", "设置", self.open_settings),
        )

    def _build_ui(self):
        outer = self._tk.Frame(self.root)
        outer.pack(fill="both", expand=True)

        toolbar = self._tk.Frame(outer)
        toolbar.pack(side="top", fill="x", padx=6, pady=6)
        self.buttons = {}
        for key, text, handler in self._button_specs():
            button = self._ttk.Button(toolbar, text=text, command=handler)
            button.pack(side="left", padx=2)
            self.buttons[key] = button

        self.hint_label = self._ttk.Label(
            outer,
            text="点击「添加应用」从已安装程序中选择",
            foreground="#7a5d00",
        )
        self.hint_label.pack(side="top", fill="x", padx=6)

        table_frame = self._tk.Frame(outer)
        table_frame.pack(side="top", fill="both", expand=True, padx=6, pady=4)

        columns = tuple(column[0] for column in COLUMNS)
        self.tree = self._ttk.Treeview(
            table_frame, columns=columns, show="headings", selectmode="browse"
        )
        for key, title, width in COLUMNS:
            self.tree.heading(key, text=title)
            try:
                self.tree.column(
                    key, width=width, anchor="w", stretch=(key == "command")
                )
            except Exception:
                self.tree.column(key, width=width, anchor="w")
        scrollbar = self._ttk.Scrollbar(
            table_frame, orient="vertical", command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        try:
            self.tree.bind("<<TreeviewSelect>>", lambda _event: self._update_buttons())
        except Exception:
            pass

        status = self._tk.Frame(self.root)
        status.pack(side="bottom", fill="x")
        self.status_text = self._tk.StringVar(value="")
        self.status_label = self._ttk.Label(
            status, textvariable=self.status_text, anchor="w"
        )
        self.status_label.pack(side="left", padx=6)
        self.backend_label = self._ttk.Label(status, text="Tk")
        self.backend_label.pack(side="right", padx=6)

        menubar = self._tk.Menu(self.root)
        file_menu = self._tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="打开配置目录", command=self.open_config_dir)
        file_menu.add_command(label="立即检测掉线应用", command=self.check_now)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.quit_app)
        menubar.add_cascade(label="文件", menu=file_menu)
        help_menu = self._tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="环境诊断", command=self.show_doctor)
        help_menu.add_command(label="关于", command=self.show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)
        try:
            self.root.configure(menu=menubar)
        except Exception:
            pass

    # ---- 状态 -----------------------------------------------------
    def _set_status(self, text):
        try:
            self.status_text.set(str(text))
        except Exception:
            pass

    def _update_status(self):
        total = len(self._items)
        enabled = len([one for one in self._items if one.enabled])
        guarded = len(
            [one for one in self._items if one.enabled and bool(getattr(one, "watchdog", False))]
        )
        try:
            config_path = self.store.path
        except Exception:
            config_path = paths.config_path()
        self._set_status(
            "共 %d 项，已启用 %d 项 · 守护 %d 项 · 配置：%s"
            % (total, enabled, guarded, config_path)
        )

    def _current_row(self):
        try:
            selection = self.tree.selection()
        except Exception:
            return -1
        if not selection:
            return -1
        try:
            return int(selection[0])
        except Exception:
            return -1

    def _select_row(self, row):
        if 0 <= row < len(self._items):
            try:
                iid = str(row)
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self.tree.see(iid)
            except Exception:
                pass

    def _update_buttons(self):
        row = self._current_row()
        has_selection = 0 <= row < len(self._items)
        states = {
            "edit": has_selection,
            "delete": has_selection,
            "move_up": has_selection and row > 0,
            "move_down": has_selection and row < len(self._items) - 1,
        }
        for key, enabled in states.items():
            button = self.buttons.get(key)
            if button is None:
                continue
            try:
                button.configure(state="normal" if enabled else "disabled")
            except Exception:
                pass

    # ---- 刷新 -----------------------------------------------------
    def refresh(self):
        """从 store 重载并刷新列表 / 状态栏。"""
        try:
            cfg = self.store.load()
        except Exception as exc:
            cfg = Config()
            self._set_status("读取配置失败：%s" % (exc,))
        self.cfg = cfg
        self._items = list(cfg.items)

        try:
            for iid in self.tree.get_children():
                self.tree.delete(iid)
        except Exception:
            pass
        for index, item in enumerate(self._items):
            try:
                self.tree.insert(
                    "", "end", iid=str(index), values=self._row_values(item)
                )
            except Exception:
                pass

        try:
            if self._items:
                self.hint_label.pack_forget()
            else:
                self.hint_label.pack(
                    side="top", fill="x", padx=6, before=self.tree.master
                )
        except Exception:
            pass
        self._update_buttons()
        self._update_status()

    def _row_values(self, item):
        return (
            "是" if item.enabled else "否",
            item.name,
            item.display_mode(),
            item.display_delay(),
            "是" if bool(getattr(item, "watchdog", False)) else "否",
            item.command,
            item.last_run_at or "—",
        )

    def _sync_silent(self):
        try:
            result = autostart.sync(self.store.load())
        except Exception as exc:
            self._set_status("同步自启动文件失败：%s" % (exc,))
            return
        if isinstance(result, dict):
            errors = result.get("errors") or []
            if errors:
                self._set_status("同步自启动文件时出现问题：%s" % (errors[0],))

    # ---- 启用勾选 -------------------------------------------------
    def _row_at_event(self, event):
        try:
            iid = self.tree.identify_row(event.y)
        except Exception:
            return -1
        if not iid:
            return -1
        try:
            return int(iid)
        except Exception:
            return -1

    def _on_double_click(self, event):
        row = self._row_at_event(event)
        if row < 0:
            return
        self.toggle_enabled(row)

    def toggle_enabled(self, row):
        if not (0 <= row < len(self._items)):
            return
        item = self._items[row]
        new_value = not bool(item.enabled)
        item.enabled = new_value
        try:
            self.store.update(item)
        except Exception as exc:
            self._set_status("保存失败：%s" % (exc,))
            self.refresh()
            return
        self._sync_silent()
        self.refresh()
        self._select_row(row)
        self._set_status(
            "已%s「%s」" % ("启用" if new_value else "停用", item.name)
        )

    def toggle_watchdog(self, row):
        """切换该条目的「掉线自动重启」。"""
        if not (0 <= row < len(self._items)):
            return
        item = self._items[row]
        new_value = not bool(getattr(item, "watchdog", False))
        item.watchdog = new_value
        try:
            self.store.update(item)
        except Exception as exc:
            self._set_status("保存失败：%s" % (exc,))
            self.refresh()
            return
        self.refresh()
        self._select_row(row)
        self._set_status(
            "已%s「%s」的掉线自动重启"
            % ("开启" if new_value else "关闭", item.name)
        )

    def check_now(self):
        """立即执行一轮掉线检测。"""
        try:
            guard = watchdog_module.Watchdog(self.store)
            result = guard.run_once()
        except Exception as exc:
            self._set_status("掉线检测失败：%s" % (exc,))
            return
        try:
            self.refresh()
        except Exception:
            pass
        if not isinstance(result, dict):
            return
        if not result.get("enabled"):
            self._set_status("掉线守护全局开关已关闭，已跳过本次检测")
            return
        checked = int(result.get("checked") or 0)
        restarted = len(result.get("restarted") or [])
        failed = len(result.get("failed") or [])
        if checked == 0:
            self._set_status("没有勾选「掉线自动重启」的条目")
            return
        message = "掉线检测完成：检查 %d 项，重新启动 %d 项" % (checked, restarted)
        if failed:
            message += "，失败 %d 项（详见日志）" % (failed,)
        self._set_status(message)

    # ---- 右键菜单 -------------------------------------------------
    def _on_right_click(self, event):
        row = self._row_at_event(event)
        if row < 0:
            return
        self._select_row(row)
        item = self._items[row]
        menu = self._tk.Menu(self.root, tearoff=0)
        menu.add_command(
            label="停用" if item.enabled else "启用",
            command=lambda: self.toggle_enabled(row),
        )
        menu.add_command(
            label="关闭掉线守护" if getattr(item, "watchdog", False) else "开启掉线守护",
            command=lambda: self.toggle_watchdog(row),
        )
        menu.add_command(label="编辑", command=self.edit_item)
        menu.add_command(label="删除", command=self.delete_item)
        menu.add_separator()
        menu.add_command(label="立即检测掉线应用", command=self.check_now)
        menu.add_command(label="启动选中项", command=self.start_selected)
        menu.add_command(label="全部启动", command=self.start_all)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        except Exception:
            pass
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    # ---- 添加 / 编辑 / 删除 ---------------------------------------
    def add_application(self):
        try:
            entries = desktop.discover_applications()
        except Exception as exc:
            entries = []
            self._set_status("读取已安装应用失败：%s" % (exc,))
        if not entries:
            self._messagebox.information(
                "没有可用应用",
                "没有找到可用的已安装应用，可以改用「添加命令」。",
                parent=self.root,
            )
            return
        picker = AppPickerDialog(self, entries)
        picker.show_modal()
        entry = picker.result_entry
        if entry is None:
            return
        dialog = ItemDialog(
            self,
            defaults={
                "name": entry.name,
                "command": entry.command,
                "icon": entry.icon,
                "comment": entry.comment,
                "terminal": bool(entry.terminal),
            },
        )
        dialog.show_modal()
        if dialog.result_item is not None:
            self._save_item(dialog.result_item, editing=False)

    def add_command(self):
        dialog = ItemDialog(self)
        dialog.show_modal()
        if dialog.result_item is not None:
            self._save_item(dialog.result_item, editing=False)

    def edit_item(self):
        row = self._current_row()
        if not (0 <= row < len(self._items)):
            return
        item = self._items[row]
        dialog = ItemDialog(self, item=item)
        dialog.show_modal()
        if dialog.result_item is not None:
            self._save_item(dialog.result_item, editing=True)

    def _save_item(self, item, editing):
        try:
            if editing:
                if not self.store.update(item):
                    self.store.add(item)
            else:
                self.store.add(item)
        except Exception as exc:
            self._messagebox.warning(
                "保存失败", "无法保存配置项：%s" % (exc,), parent=self.root
            )
            return
        self._sync_silent()
        self.refresh()

    def delete_item(self):
        row = self._current_row()
        if not (0 <= row < len(self._items)):
            return
        item = self._items[row]
        confirmed = False
        try:
            confirmed = bool(
                self._messagebox.askyesno(
                    "确认删除",
                    "确定要删除自启动项「%s」吗？" % (item.name,),
                    parent=self.root,
                )
            )
        except Exception:
            confirmed = False
        if not confirmed:
            return
        try:
            removed = self.store.remove(item.id)
        except Exception as exc:
            self._set_status("删除失败：%s" % (exc,))
            return
        if removed:
            self._sync_silent()
            self.refresh()

    # ---- 排序 -----------------------------------------------------
    def move_up(self):
        self._move(-1)

    def move_down(self):
        self._move(1)

    def _move(self, delta):
        row = self._current_row()
        if not (0 <= row < len(self._items)):
            return
        item = self._items[row]
        try:
            moved = self.store.move(item.id, delta)
        except Exception as exc:
            self._set_status("移动失败：%s" % (exc,))
            return
        if moved:
            self.refresh()
            self._select_row(row + delta)

    # ---- 启动 -----------------------------------------------------
    def _make_launcher(self):
        return launcher.Launcher(
            self.store, dry_run=bool(self.options.get("dry_run", False))
        )

    def start_selected(self):
        row = self._current_row()
        if not (0 <= row < len(self._items)):
            return
        item = self._items[row]
        ok = False
        try:
            ok = bool(self._make_launcher().start_item(item))
        except Exception as exc:
            self._set_status("启动失败：%s" % (exc,))
        self.refresh()
        self._select_row(row)
        if ok:
            self._set_status("已启动：%s" % (item.name,))
        else:
            self._set_status("启动失败：%s" % (item.name,))

    def start_all(self):
        try:
            runner = self._make_launcher()
            runner.start_all_async(self.store.load(), include_xdg=True)
        except Exception as exc:
            self._set_status("启动失败：%s" % (exc,))
            return
        self._set_status("正在后台启动全部应用…")
        try:
            self.root.after(1500, self.refresh)
        except Exception:
            pass

    # ---- 设置 / 菜单 ---------------------------------------------
    def open_settings(self):
        dialog = SettingsDialog(self, self.store)
        dialog.show_modal()
        if dialog.saved:
            self.refresh()

    def open_config_dir(self):
        try:
            paths.ensure_dirs()
        except Exception:
            pass
        self._set_status("配置目录：%s" % (paths.config_dir(),))
        try:
            self._messagebox.showinfo(
                "配置目录", paths.config_dir(), parent=self.root
            )
        except Exception:
            pass

    def show_doctor(self):
        lines = ["%s %s" % (paths.APP_NAME, paths.VERSION), ""]
        lines.append("配置文件：%s" % (paths.config_path(),))
        lines.append("自启动目录：%s" % (paths.autostart_dir(),))
        lines.append("日志文件：%s" % (paths.log_path(),))
        try:
            manager_auto = autostart.manager_autostart_enabled()
            lines.append("管理器自启动：%s" % ("已启用" if manager_auto else "未启用",))
        except Exception:
            lines.append("管理器自启动：未知")
        try:
            cfg = self.store.load()
            if bool(getattr(cfg, "watchdog_enabled", True)):
                lines.append(
                    "掉线守护：已启用 · 每 %d 分钟检测一次 · 守护条目 %d 项"
                    % (int(cfg.watchdog_interval_minutes), len(cfg.watchdog_items()))
                )
            else:
                lines.append("掉线守护：已停用（全局开关关闭）")
        except Exception:
            lines.append("掉线守护：未知")
        lines.append("")
        try:
            entries = autostart.audit(self.store.load())
        except Exception as exc:
            entries = []
            lines.append("诊断失败：%s" % (exc,))
        if not entries:
            lines.append("未发现明显问题。")
        for entry in entries:
            if not isinstance(entry, dict):
                lines.append(str(entry))
                continue
            level = str(entry.get("level", "ok"))
            lines.append(
                "%s %s" % (_LEVEL_MARKS.get(level, "[信息]"), entry.get("message", ""))
            )
        try:
            self._messagebox.showinfo("环境诊断", "\n".join(lines), parent=self.root)
        except Exception:
            pass

    def show_about(self):
        text = "\n".join(
            [
                paths.APP_NAME,
                "版本：%s" % (paths.VERSION,),
                "维护者：%s" % (paths.MAINTAINER,),
                "",
                "图形化管理 Linux 桌面开机自启动应用。",
                "支持掉线自动重启（定时检测，未运行则重新启动）。",
            ]
        )
        try:
            self._messagebox.showinfo("关于", text, parent=self.root)
        except Exception:
            pass

    # ---- 关闭 -----------------------------------------------------
    def on_close(self):
        self._force_quit = True
        try:
            self.root.destroy()
        except Exception:
            pass

    def quit_app(self):
        self._force_quit = True
        try:
            self.root.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 模块入口
# ---------------------------------------------------------------------------
def main(store, options=None):
    """启动 tkinter 图形界面，返回进程退出码。"""
    options = dict(options or {})
    try:
        window = MainWindow(store, options)
    except Exception as exc:
        sys.stderr.write("无法启动 Tk 图形界面：%s\n" % (exc,))
        return 2
    if bool(options.get("minimized")):
        try:
            window.root.withdraw()
        except Exception:
            pass
    try:
        window.root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            window.root.destroy()
        except Exception:
            pass
    return 0
