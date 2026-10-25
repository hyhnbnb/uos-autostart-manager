# -*- coding: utf-8 -*-
"""PyQt5 图形界面。

兼容 Python 3.7 / PyQt5 5.11（统信 UOS 20 自带的 python3-pyqt5），只使用
Qt5 全版本通用的 API。模块顶层对 PyQt5 的导入做了保护：缺少 PyQt5 时本模块
仍可被 import，``available()`` 返回 False，``app.py`` 会自动回退到 tkinter。

``MainWindow`` 可以在不进入事件循环、不创建托盘的情况下构造并调用
``refresh()``，方便自动化冒烟测试（QT_QPA_PLATFORM=offscreen）。
"""

import os
import sys

from . import autostart, desktop, launcher, paths
from . import watchdog as watchdog_module
from .model import AutostartItem, Config

try:  # pragma: no cover - 取决于运行环境
    from PyQt5 import QtCore, QtGui, QtWidgets

    _QT_OK = True
except Exception:  # ImportError 或 Qt 绑定异常
    QtCore = None
    QtGui = None
    QtWidgets = None
    _QT_OK = False

COLUMNS = ("启用", "名称", "启动方式", "延迟", "守护", "命令", "最近启动")

_LEVEL_MARKS = {"ok": "[正常]", "warn": "[警告]", "error": "[错误]"}


class _FallbackObject(object):
    """PyQt5 缺失时的占位基类，保证本模块始终可 import。"""


def _base_class(name):
    if _QT_OK:
        return getattr(QtWidgets, name)
    return _FallbackObject


_WindowBase = _base_class("QMainWindow")
_DialogBase = _base_class("QDialog")
_TrayBase = _base_class("QSystemTrayIcon")


# ---------------------------------------------------------------------------
# 应用 / 图标 / 托盘可用性
# ---------------------------------------------------------------------------
def available():
    """PyQt5 是否可用。"""
    return bool(_QT_OK)


def create_app(argv=None):
    """创建（或复用）QApplication。"""
    if not _QT_OK:
        raise RuntimeError("未安装 PyQt5，无法创建 Qt 应用")
    args = list(argv) if argv else []
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    app = QtWidgets.QApplication(args)
    try:
        app.setApplicationName(paths.APP_NAME)
        app.setApplicationVersion(paths.VERSION)
        app.setApplicationDisplayName(paths.APP_NAME)
    except Exception:
        pass
    try:
        if hasattr(app, "setDesktopFileName"):
            app.setDesktopFileName(paths.APP_ID)
    except Exception:
        pass
    try:
        app.setWindowIcon(create_icon())
    except Exception:
        pass
    return app


def create_icon():
    """返回 QIcon：有图标文件用图标，否则画一个。"""
    if not _QT_OK:
        raise RuntimeError("未安装 PyQt5，无法创建图标")
    try:
        icon_path = paths.icon_path()
    except Exception:
        icon_path = None
    if icon_path:
        try:
            icon = QtGui.QIcon(icon_path)
            if not icon.isNull():
                return icon
        except Exception:
            pass
    pixmap = QtGui.QPixmap(64, 64)
    try:
        pixmap.fill(QtCore.Qt.transparent)
    except Exception:
        pass
    painter = QtGui.QPainter(pixmap)
    try:
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        except Exception:
            pass
        try:
            painter.setBrush(QtGui.QBrush(QtGui.QColor("#2b7de9")))
            painter.setPen(QtCore.Qt.NoPen)
            painter.drawRoundedRect(4, 4, 56, 56, 14, 14)
        except Exception:
            pass
        try:
            painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff")))
            font = QtGui.QFont()
            font.setPixelSize(34)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(pixmap.rect(), QtCore.Qt.AlignCenter, "启")
        except Exception:
            pass
    finally:
        try:
            painter.end()
        except Exception:
            pass
    return QtGui.QIcon(pixmap)


def tray_available():
    """系统托盘是否可用（无托盘时静默降级为普通窗口）。"""
    if not _QT_OK:
        return False
    try:
        return bool(QtWidgets.QSystemTrayIcon.isSystemTrayAvailable())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------
class MainWindow(_WindowBase):
    """主窗口：表格列出全部自启动项，工具栏提供增删改与启动操作。"""

    def __init__(self, store, options=None, parent=None):
        super(MainWindow, self).__init__()
        self.store = store
        self.options = dict(options or {})
        self.cfg = Config()
        self._items = []
        self._updating = False
        self._force_quit = False
        self._tray = None
        self._tray_hint_shown = False
        self._build_ui()
        self.refresh()

    # ---- 构建界面 -------------------------------------------------
    def _build_ui(self):
        self.setWindowTitle(paths.APP_NAME)
        self.resize(1024, 620)
        try:
            if QtWidgets.QApplication.instance() is not None:
                self.setWindowIcon(create_icon())
        except Exception:
            pass

        self._build_menus()

        central = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(central)
        try:
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(6)
        except Exception:
            pass

        self._build_toolbar(layout)

        self.hint_label = QtWidgets.QLabel("点击「添加应用」从已安装程序中选择", central)
        self.hint_label.setVisible(False)
        try:
            self.hint_label.setStyleSheet(
                "QLabel { background: #fff7d6; border: 1px solid #e8d27a;"
                " color: #7a5d00; padding: 6px; }"
            )
        except Exception:
            pass
        layout.addWidget(self.hint_label)

        self._build_table(central, layout)
        self.setCentralWidget(central)
        self._build_status_bar()

    def _make_button(self, text, handler):
        button = QtWidgets.QPushButton(text, self)
        try:
            button.clicked.connect(handler)
        except Exception:
            pass
        return button

    def _build_toolbar(self, layout):
        row = QtWidgets.QHBoxLayout()
        try:
            row.setSpacing(6)
        except Exception:
            pass
        specs = (
            ("btn_add_app", "添加应用", self.add_application),
            ("btn_add_command", "添加命令", self.add_command),
            ("btn_edit", "编辑", self.edit_item),
            ("btn_delete", "删除", self.delete_item),
            ("btn_move_up", "上移", self.move_up),
            ("btn_move_down", "下移", self.move_down),
            ("btn_start_all", "全部启动", self.start_all),
            ("btn_settings", "设置", self.open_settings),
        )
        for attr, text, handler in specs:
            button = self._make_button(text, handler)
            setattr(self, attr, button)
            row.addWidget(button)
        row.addStretch(1)
        self.btn_start_selected = self._make_button("启动选中项", self.start_selected)
        row.addWidget(self.btn_start_selected)
        layout.addLayout(row)

    def _build_table(self, parent, layout):
        self.table = QtWidgets.QTableWidget(0, len(COLUMNS), parent)
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        try:
            self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        except Exception:
            pass
        try:
            self.table.setAlternatingRowColors(True)
            self.table.setWordWrap(False)
            self.table.verticalHeader().setVisible(False)
            header = self.table.horizontalHeader()
            header.setStretchLastSection(False)
            header.setSectionResizeMode(5, QtWidgets.QHeaderView.Stretch)
        except Exception:
            pass
        for column, width in ((0, 56), (1, 180), (2, 110), (3, 90), (4, 76), (6, 150)):
            try:
                self.table.setColumnWidth(column, width)
            except Exception:
                pass
        try:
            self.table.itemChanged.connect(self._on_item_changed)
            self.table.itemSelectionChanged.connect(self._update_buttons)
            self.table.itemDoubleClicked.connect(self._on_item_double_clicked)
            self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            self.table.customContextMenuRequested.connect(self._on_context_menu)
        except Exception:
            pass
        layout.addWidget(self.table, 1)

    def _build_status_bar(self):
        try:
            bar = self.statusBar()
            self.status_label = QtWidgets.QLabel("", self)
            self.backend_label = QtWidgets.QLabel("Qt", self)
            bar.addWidget(self.status_label, 1)
            bar.addPermanentWidget(self.backend_label)
        except Exception:
            self.status_label = None
            self.backend_label = None

    def _build_menus(self):
        try:
            menu_bar = self.menuBar()
        except Exception:
            return
        file_menu = menu_bar.addMenu("文件")
        action_open_dir = QtWidgets.QAction("打开配置目录", self)
        action_open_dir.triggered.connect(self.open_config_dir)
        file_menu.addAction(action_open_dir)
        action_check = QtWidgets.QAction("立即检测掉线应用", self)
        action_check.triggered.connect(self.check_now)
        file_menu.addAction(action_check)
        file_menu.addSeparator()
        action_quit = QtWidgets.QAction("退出", self)
        action_quit.triggered.connect(self.quit_app)
        file_menu.addAction(action_quit)

        help_menu = menu_bar.addMenu("帮助")
        action_doctor = QtWidgets.QAction("环境诊断", self)
        action_doctor.triggered.connect(self.show_doctor)
        help_menu.addAction(action_doctor)
        action_about = QtWidgets.QAction("关于", self)
        action_about.triggered.connect(self.show_about)
        help_menu.addAction(action_about)

    # ---- 刷新 -----------------------------------------------------
    def refresh(self):
        """从 store 重新加载并刷新表格 / 状态栏。不弹窗、不需要托盘。"""
        try:
            cfg = self.store.load()
        except Exception as exc:
            cfg = Config()
            self._flash("读取配置失败：%s" % (exc,))
        self.cfg = cfg
        self._items = list(cfg.items)
        self._updating = True
        try:
            try:
                self.table.blockSignals(True)
            except Exception:
                pass
            self.table.clearContents()
            self.table.setRowCount(len(self._items))
            for row, item in enumerate(self._items):
                self._fill_row(row, item)
        finally:
            try:
                self.table.blockSignals(False)
            except Exception:
                pass
            self._updating = False
        try:
            self.hint_label.setVisible(len(self._items) == 0)
        except Exception:
            pass
        self._update_buttons()
        self._update_status()

    def _fill_row(self, row, item):
        check_item = QtWidgets.QTableWidgetItem("")
        check_item.setFlags(
            QtCore.Qt.ItemIsUserCheckable
            | QtCore.Qt.ItemIsEnabled
            | QtCore.Qt.ItemIsSelectable
        )
        check_item.setCheckState(
            QtCore.Qt.Checked if item.enabled else QtCore.Qt.Unchecked
        )
        try:
            check_item.setToolTip(item.comment or item.name)
        except Exception:
            pass
        self.table.setItem(row, 0, check_item)

        watchdog_item = QtWidgets.QTableWidgetItem("")
        watchdog_item.setFlags(
            QtCore.Qt.ItemIsUserCheckable
            | QtCore.Qt.ItemIsEnabled
            | QtCore.Qt.ItemIsSelectable
        )
        watchdog_item.setCheckState(
            QtCore.Qt.Checked if getattr(item, "watchdog", False) else QtCore.Qt.Unchecked
        )
        try:
            watchdog_item.setToolTip(
                "勾选后按设置的间隔定时检测，发现未运行会自动重新启动"
            )
        except Exception:
            pass
        self.table.setItem(row, 4, watchdog_item)

        values = (
            item.name,
            item.display_mode(),
            item.display_delay(),
            item.command,
            item.last_run_at or "—",
        )
        for offset, text in zip((1, 2, 3, 5, 6), values):
            cell = QtWidgets.QTableWidgetItem(str(text))
            cell.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            self.table.setItem(row, offset, cell)

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
        text = "共 %d 项，已启用 %d 项 · 守护 %d 项 · 配置：%s" % (
            total,
            enabled,
            guarded,
            config_path,
        )
        if self.status_label is not None:
            try:
                self.status_label.setText(text)
            except Exception:
                pass

    def _flash(self, message, timeout=6000):
        try:
            self.statusBar().showMessage(str(message), int(timeout))
        except Exception:
            pass

    # ---- 选择 / 按钮状态 -----------------------------------------
    def _current_row(self):
        try:
            row = self.table.currentRow()
        except Exception:
            return -1
        if row is None:
            return -1
        try:
            return int(row)
        except Exception:
            return -1

    def _select_row(self, row):
        if 0 <= row < len(self._items):
            try:
                self.table.selectRow(row)
            except Exception:
                pass

    def _update_buttons(self):
        row = self._current_row()
        has_selection = 0 <= row < len(self._items)
        for name, enabled in (
            ("btn_edit", has_selection),
            ("btn_delete", has_selection),
            ("btn_start_selected", has_selection),
            ("btn_move_up", has_selection and row > 0),
            ("btn_move_down", has_selection and row < len(self._items) - 1),
        ):
            button = getattr(self, name, None)
            if button is None:
                continue
            try:
                button.setEnabled(bool(enabled))
            except Exception:
                pass

    def _on_item_double_clicked(self, _item):
        self.edit_item()

    # ---- 勾选持久化（启用 / 掉线守护） -----------------------------
    def _on_item_changed(self, item):
        if self._updating or item is None:
            return
        try:
            column = item.column()
            row = item.row()
        except Exception:
            return
        if column not in (0, 4):
            return
        if not (0 <= row < len(self._items)):
            return
        target = self._items[row]
        checked = item.checkState() == QtCore.Qt.Checked

        if column == 4:  # 「守护」列：掉线自动重启
            if bool(getattr(target, "watchdog", False)) == bool(checked):
                return
            target.watchdog = bool(checked)
            try:
                self.store.update(target)
            except Exception as exc:
                self._flash("保存失败：%s" % (exc,))
                self.refresh()
                return
            self._update_status()
            self._flash(
                "已%s「%s」的掉线自动重启"
                % ("开启" if checked else "关闭", target.name)
            )
            return

        enabled = checked
        if bool(target.enabled) == bool(enabled):
            return
        target.enabled = bool(enabled)
        try:
            self.store.update(target)
        except Exception as exc:
            self._flash("保存失败：%s" % (exc,))
            self.refresh()
            return
        self._sync_silent()
        self._update_status()
        self._flash("已%s「%s」" % ("启用" if enabled else "停用", target.name))

    def _sync_silent(self):
        """同步 XDG 自启动文件；失败只提示，不打断界面。"""
        try:
            cfg = self.store.load()
            result = autostart.sync(cfg)
        except Exception as exc:
            self._flash("同步自启动文件失败：%s" % (exc,))
            return
        if isinstance(result, dict):
            errors = result.get("errors") or []
            if errors:
                self._flash("同步自启动文件时出现问题：%s" % (errors[0],))

    # ---- 添加 / 编辑 / 删除 ---------------------------------------
    def add_application(self):
        try:
            entries = desktop.discover_applications()
        except Exception as exc:
            entries = []
            self._flash("读取已安装应用失败：%s" % (exc,))
        if not entries:
            try:
                QtWidgets.QMessageBox.information(
                    self,
                    "没有可用应用",
                    "没有找到可用的已安装应用。\n可以改用「添加命令」手动填写启动命令。",
                )
            except Exception:
                pass
            return

        picker = AppPickerDialog(entries, parent=self)
        try:
            accepted = picker.exec_() == QtWidgets.QDialog.Accepted
        except Exception:
            accepted = False
        if not accepted:
            return
        entry = picker.selected_entry()
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
        self._run_item_dialog(dialog, editing=False)

    def add_command(self):
        dialog = ItemDialog(self)
        self._run_item_dialog(dialog, editing=False)

    def edit_item(self):
        row = self._current_row()
        if not (0 <= row < len(self._items)):
            return
        item = self._items[row]
        dialog = ItemDialog(self, item=item)
        self._run_item_dialog(dialog, editing=True)

    def _run_item_dialog(self, dialog, editing):
        try:
            accepted = dialog.exec_() == QtWidgets.QDialog.Accepted
        except Exception:
            accepted = False
        if not accepted or dialog.result_item is None:
            return
        self._save_item(dialog.result_item, editing)

    def _save_item(self, item, editing):
        try:
            if editing:
                if not self.store.update(item):
                    self.store.add(item)
            else:
                self.store.add(item)
        except Exception as exc:
            try:
                QtWidgets.QMessageBox.critical(
                    self, "保存失败", "无法保存配置项：%s" % (exc,)
                )
            except Exception:
                pass
            return
        self._sync_silent()
        self.refresh()

    def delete_item(self):
        row = self._current_row()
        if not (0 <= row < len(self._items)):
            return
        item = self._items[row]
        try:
            reply = QtWidgets.QMessageBox.question(
                self,
                "确认删除",
                "确定要删除自启动项「%s」吗？" % (item.name,),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
        except Exception:
            reply = QtWidgets.QMessageBox.No
        if reply != QtWidgets.QMessageBox.Yes:
            return
        try:
            removed = self.store.remove(item.id)
        except Exception as exc:
            self._flash("删除失败：%s" % (exc,))
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
            self._flash("移动失败：%s" % (exc,))
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
            self._flash("启动失败：%s" % (exc,))
        self.refresh()
        self._select_row(row)
        if ok:
            self._flash("已启动：%s" % (item.name,))
        else:
            self._flash("启动失败：%s" % (item.name,))

    def start_all(self):
        try:
            cfg = self.store.load()
            runner = self._make_launcher()
            runner.start_all_async(cfg, include_xdg=True)
        except Exception as exc:
            self._flash("启动失败：%s" % (exc,))
            return
        self._flash("正在后台启动全部应用…")
        try:
            QtCore.QTimer.singleShot(1500, self.refresh)
        except Exception:
            pass

    # ---- 设置 / 菜单动作 -----------------------------------------
    def open_settings(self):
        try:
            dialog = SettingsDialog(self.store, parent=self)
            accepted = dialog.exec_() == QtWidgets.QDialog.Accepted
        except Exception as exc:
            self._flash("打开设置失败：%s" % (exc,))
            return
        if accepted:
            self.refresh()

    def open_config_dir(self):
        try:
            paths.ensure_dirs()
        except Exception:
            pass
        target = paths.config_dir()
        opened = False
        try:
            opened = bool(
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(target))
            )
        except Exception:
            opened = False
        if not opened:
            self._flash("无法打开配置目录，请手动访问：%s" % (target,))

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
                "%s %s"
                % (_LEVEL_MARKS.get(level, "[信息]"), entry.get("message", ""))
            )
        try:
            QtWidgets.QMessageBox.information(self, "环境诊断", "\n".join(lines))
        except Exception:
            pass

    def show_about(self):
        text = "\n".join(
            [
                paths.APP_NAME,
                "版本：%s" % (paths.VERSION,),
                "维护者：%s" % (paths.MAINTAINER,),
                "",
                "图形化管理 Linux 桌面开机自启动应用：",
                "从已安装应用中选择、自定义命令、设置启动延迟与顺序；",
                "支持掉线自动重启（定时检测，未运行则重新启动）。",
            ]
        )
        try:
            QtWidgets.QMessageBox.about(self, "关于", text)
        except Exception:
            pass

    def check_now(self):
        """立即执行一轮掉线检测，结果用状态栏提示。"""
        try:
            guard = watchdog_module.Watchdog(self.store)
            result = guard.run_once()
        except Exception as exc:
            self._flash("掉线检测失败：%s" % (exc,))
            return
        if not isinstance(result, dict):
            self._flash("掉线检测未返回有效结果")
            return
        try:
            self.refresh()
        except Exception:
            pass
        if not result.get("enabled"):
            self._flash("掉线守护全局开关已关闭，已跳过本次检测")
            return
        checked = int(result.get("checked") or 0)
        restarted = len(result.get("restarted") or [])
        failed = len(result.get("failed") or [])
        if checked == 0:
            self._flash("没有勾选「掉线自动重启」的条目")
            return
        message = "掉线检测完成：检查 %d 项，重新启动 %d 项" % (checked, restarted)
        if failed:
            message += "，失败 %d 项（详见日志）" % (failed,)
        self._flash(message)

    def _on_context_menu(self, pos):
        try:
            index = self.table.indexAt(pos)
        except Exception:
            return
        if not index.isValid():
            return
        self._select_row(index.row())
        menu = QtWidgets.QMenu(self.table)
        action_start = menu.addAction("启动选中项")
        action_edit = menu.addAction("编辑")
        action_delete = menu.addAction("删除")
        menu.addSeparator()
        action_all = menu.addAction("全部启动")
        chosen = None
        try:
            chosen = menu.exec_(self.table.viewport().mapToGlobal(pos))
        except Exception:
            chosen = None
        if chosen is action_start:
            self.start_selected()
        elif chosen is action_edit:
            self.edit_item()
        elif chosen is action_delete:
            self.delete_item()
        elif chosen is action_all:
            self.start_all()

    # ---- 托盘 / 关闭 ---------------------------------------------
    def set_tray(self, tray):
        self._tray = tray

    def show_normal(self):
        try:
            self.show()
            try:
                self.setWindowState(
                    self.windowState() & ~QtCore.Qt.WindowMinimized
                )
            except Exception:
                pass
            self.raise_()
            self.activateWindow()
        except Exception:
            pass

    def quit_app(self):
        self._force_quit = True
        if self._tray is not None:
            try:
                self._tray.hide()
            except Exception:
                pass
            self._tray = None
        try:
            self.close()
        except Exception:
            pass
        try:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                app.quit()
        except Exception:
            pass

    def closeEvent(self, event):
        if self._force_quit:
            try:
                event.accept()
            except Exception:
                pass
            return
        close_to_tray = True
        try:
            close_to_tray = bool(self.store.load().close_to_tray)
        except Exception:
            close_to_tray = True
        if close_to_tray and self._tray is not None:
            try:
                event.ignore()
            except Exception:
                pass
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                try:
                    self._tray.showMessage(
                        paths.APP_NAME,
                        "窗口已最小化到托盘，双击托盘图标可重新打开。",
                        QtWidgets.QSystemTrayIcon.Information,
                        4000,
                    )
                except Exception:
                    pass
            return
        try:
            event.accept()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 添加应用：从已安装应用列表选择
# ---------------------------------------------------------------------------
class AppPickerDialog(_DialogBase):
    """列出 desktop.discover_applications() 结果，支持搜索与"仅显示可执行"。"""

    def __init__(self, entries, parent=None):
        super(AppPickerDialog, self).__init__(parent)
        self._entries = list(entries or [])
        self._exec_cache = {}
        self.setWindowTitle("添加应用")
        self.resize(680, 480)
        self.setModal(True)

        layout = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        self.search_edit = QtWidgets.QLineEdit(self)
        self.search_edit.setPlaceholderText("搜索应用名称或命令…")
        self.only_exec_check = QtWidgets.QCheckBox("仅显示可执行", self)
        top.addWidget(self.search_edit, 1)
        top.addWidget(self.only_exec_check)
        layout.addLayout(top)

        self.list_widget = QtWidgets.QListWidget(self)
        self.list_widget.setSelectionMode(
            QtWidgets.QAbstractItemView.SingleSelection
        )
        layout.addWidget(self.list_widget, 1)

        for index, entry in enumerate(self._entries):
            item = QtWidgets.QListWidgetItem(self._display_label(entry))
            try:
                item.setData(QtCore.Qt.UserRole, index)
                item.setToolTip(entry.command)
            except Exception:
                pass
            self.list_widget.addItem(item)

        try:
            self.search_edit.textChanged.connect(self._apply_filter)
            self.only_exec_check.stateChanged.connect(self._apply_filter)
            self.list_widget.itemDoubleClicked.connect(
                lambda _item: self._accept_selection()
            )
        except Exception:
            pass

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        self.ok_button = QtWidgets.QPushButton("确定", self)
        self.cancel_button = QtWidgets.QPushButton("取消", self)
        try:
            self.ok_button.clicked.connect(self._accept_selection)
            self.cancel_button.clicked.connect(self.reject)
            self.ok_button.setDefault(True)
        except Exception:
            pass
        buttons.addWidget(self.ok_button)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

    @staticmethod
    def _display_label(entry):
        try:
            return entry.display_label()
        except Exception:
            return str(getattr(entry, "name", "")) or str(entry)

    def _entry_for_item(self, item):
        """QListWidgetItem 不可哈希，映射改存索引；并保留按行号兜底。"""
        if item is None:
            return None
        index = None
        try:
            index = item.data(QtCore.Qt.UserRole)
        except Exception:
            index = None
        try:
            index = int(index)
        except (TypeError, ValueError):
            index = None
        if index is not None and 0 <= index < len(self._entries):
            return self._entries[index]
        try:
            row = self.list_widget.row(item)
        except Exception:
            return None
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

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

    def _apply_filter(self, *_args):
        keyword = ""
        try:
            keyword = self.search_edit.text().strip().lower()
        except Exception:
            keyword = ""
        only_exec = False
        try:
            only_exec = bool(self.only_exec_check.isChecked())
        except Exception:
            only_exec = False
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            entry = self._entry_for_item(item)
            if entry is None:
                item.setHidden(False)
                continue
            haystack = "%s %s %s" % (
                getattr(entry, "name", ""),
                getattr(entry, "command", ""),
                getattr(entry, "desktop_id", ""),
            )
            visible = True
            if keyword and keyword not in haystack.lower():
                visible = False
            if visible and only_exec and not self._is_executable(entry):
                visible = False
            item.setHidden(not visible)

    def selected_entry(self):
        try:
            item = self.list_widget.currentItem()
        except Exception:
            item = None
        if item is None:
            return None
        return self._entry_for_item(item)

    def _accept_selection(self):
        if self.selected_entry() is None:
            try:
                QtWidgets.QMessageBox.information(
                    self, "提示", "请先在列表中选择一个应用。"
                )
            except Exception:
                pass
            return
        self.accept()


# ---------------------------------------------------------------------------
# 添加 / 编辑条目表单
# ---------------------------------------------------------------------------
class ItemDialog(_DialogBase):
    """自启动条目表单：名称 / 命令 / 参数 / 工作目录 / 图标 / 延迟 / 方式 / 终端 / 备注。"""

    def __init__(self, parent=None, item=None, defaults=None):
        super(ItemDialog, self).__init__(parent)
        self.item = item
        self._defaults = dict(defaults or {})
        self.result_item = None
        self.setWindowTitle("编辑自启动项" if item is not None else "添加自启动项")
        self.setModal(True)
        self.resize(560, 460)
        self._build_ui()
        self._load_values()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        layout.addLayout(form)

        self.name_edit = QtWidgets.QLineEdit(self)
        form.addRow("名称", self.name_edit)

        command_row = QtWidgets.QHBoxLayout()
        self.command_edit = QtWidgets.QLineEdit(self)
        self.command_edit.setPlaceholderText("例如 /usr/bin/firefox")
        browse_command = QtWidgets.QPushButton("浏览…", self)
        try:
            browse_command.clicked.connect(self._browse_command)
        except Exception:
            pass
        command_row.addWidget(self.command_edit, 1)
        command_row.addWidget(browse_command)
        command_widget = QtWidgets.QWidget(self)
        command_widget.setLayout(command_row)
        form.addRow("命令", command_widget)

        self.args_edit = QtWidgets.QLineEdit(self)
        self.args_edit.setPlaceholderText("可选，拼接在命令后面")
        form.addRow("参数", self.args_edit)

        workdir_row = QtWidgets.QHBoxLayout()
        self.workdir_edit = QtWidgets.QLineEdit(self)
        browse_workdir = QtWidgets.QPushButton("浏览…", self)
        try:
            browse_workdir.clicked.connect(self._browse_workdir)
        except Exception:
            pass
        workdir_row.addWidget(self.workdir_edit, 1)
        workdir_row.addWidget(browse_workdir)
        workdir_widget = QtWidgets.QWidget(self)
        workdir_widget.setLayout(workdir_row)
        form.addRow("工作目录", workdir_widget)

        self.icon_edit = QtWidgets.QLineEdit(self)
        self.icon_edit.setPlaceholderText("图标名或绝对路径，可留空")
        form.addRow("图标", self.icon_edit)

        self.delay_spin = QtWidgets.QSpinBox(self)
        self.delay_spin.setRange(0, 600)
        self.delay_spin.setSuffix(" 秒")
        form.addRow("启动延迟", self.delay_spin)

        self.mode_combo = QtWidgets.QComboBox(self)
        self.mode_combo.addItem("管理器启动", "manager")
        self.mode_combo.addItem("系统直接启动", "xdg")
        form.addRow("启动方式", self.mode_combo)

        self.terminal_check = QtWidgets.QCheckBox("在终端中运行", self)
        form.addRow("终端", self.terminal_check)

        self.watchdog_check = QtWidgets.QCheckBox(
            "掉线自动重启（到达检测间隔时，未运行则重新启动）", self
        )
        form.addRow("守护", self.watchdog_check)

        self.comment_edit = QtWidgets.QLineEdit(self)
        self.comment_edit.setPlaceholderText("可选，用于说明这个条目")
        form.addRow("备注", self.comment_edit)

        layout.addStretch(1)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        self.save_button = QtWidgets.QPushButton("保存", self)
        self.cancel_button = QtWidgets.QPushButton("取消", self)
        try:
            self.save_button.clicked.connect(self._on_save)
            self.cancel_button.clicked.connect(self.reject)
            self.save_button.setDefault(True)
        except Exception:
            pass
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

    def _search_dirs(self):
        candidates = []
        for candidate in ("/usr/bin", "/usr/local/bin"):
            if os.path.isdir(candidate):
                candidates.append(candidate)
        try:
            candidates.append(os.path.expanduser("~"))
        except Exception:
            pass
        return candidates[0] if candidates else os.getcwd()

    def _browse_command(self):
        try:
            chosen, _unused = QtWidgets.QFileDialog.getOpenFileName(
                self, "选择程序", self._search_dirs(), "所有文件 (*)"
            )
        except Exception:
            return
        if chosen:
            self.command_edit.setText(chosen)

    def _browse_workdir(self):
        try:
            chosen = QtWidgets.QFileDialog.getExistingDirectory(
                self, "选择工作目录", self._search_dirs()
            )
        except Exception:
            return
        if chosen:
            self.workdir_edit.setText(chosen)

    def _load_values(self):
        if self.item is not None:
            self.name_edit.setText(self.item.name)
            self.command_edit.setText(self.item.command)
            self.workdir_edit.setText(self.item.workdir)
            self.icon_edit.setText(self.item.icon)
            self.comment_edit.setText(self.item.comment)
            self.terminal_check.setChecked(bool(self.item.terminal))
            self.watchdog_check.setChecked(bool(getattr(self.item, "watchdog", False)))
            try:
                self.delay_spin.setValue(max(0, min(600, int(self.item.delay))))
            except Exception:
                self.delay_spin.setValue(0)
            self._select_mode(self.item.mode)
            return
        defaults = self._defaults
        self.name_edit.setText(str(defaults.get("name") or ""))
        self.command_edit.setText(str(defaults.get("command") or ""))
        self.workdir_edit.setText(str(defaults.get("workdir") or ""))
        self.icon_edit.setText(str(defaults.get("icon") or ""))
        self.comment_edit.setText(str(defaults.get("comment") or ""))
        self.terminal_check.setChecked(bool(defaults.get("terminal")))
        self.watchdog_check.setChecked(bool(defaults.get("watchdog")))
        self.delay_spin.setValue(max(0, min(600, int(defaults.get("delay") or 0))))
        self._select_mode("manager")

    def _select_mode(self, mode):
        index = 0
        try:
            found = self.mode_combo.findData("xdg" if mode == "xdg" else "manager")
            if found is not None and int(found) >= 0:
                index = int(found)
        except Exception:
            index = 0
        try:
            self.mode_combo.setCurrentIndex(index)
        except Exception:
            pass

    def _current_mode(self):
        try:
            data = self.mode_combo.currentData()
        except Exception:
            data = None
        if data == "xdg" or data == "manager":
            return data
        try:
            return "xdg" if self.mode_combo.currentIndex() == 1 else "manager"
        except Exception:
            return "manager"

    def _on_save(self):
        name = self.name_edit.text().strip()
        command = self.command_edit.text().strip()
        extra_args = self.args_edit.text().strip()
        if extra_args:
            command = (command + " " + extra_args).strip()
        try:
            command = desktop.strip_field_codes(command)
        except Exception:
            pass
        workdir = self.workdir_edit.text().strip()
        icon = self.icon_edit.text().strip()
        comment = self.comment_edit.text().strip()
        try:
            delay = int(self.delay_spin.value())
        except Exception:
            delay = 0
        delay = max(0, min(600, delay))
        mode = self._current_mode()
        terminal = bool(self.terminal_check.isChecked())
        watchdog = bool(self.watchdog_check.isChecked())

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
            try:
                QtWidgets.QMessageBox.warning(
                    self, "无法保存", "\n".join([str(one) for one in errors])
                )
            except Exception:
                pass
            return
        self.result_item = new_item
        self.accept()


# ---------------------------------------------------------------------------
# 设置对话框
# ---------------------------------------------------------------------------
class SettingsDialog(_DialogBase):
    """设置：管理器自启、最小化、关闭到托盘、全局等待。"""

    def __init__(self, store, parent=None):
        super(SettingsDialog, self).__init__(parent)
        self.store = store
        self.setWindowTitle("设置")
        self.setModal(True)
        self.resize(520, 430)
        try:
            self.cfg = store.load()
        except Exception:
            self.cfg = Config()
        self._build_ui()
        self._load_values()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        layout.addLayout(form)

        self.cb_manager = QtWidgets.QCheckBox("管理器开机自启动", self)
        self.cb_minimized = QtWidgets.QCheckBox("开机时最小化到托盘", self)
        self.cb_close_tray = QtWidgets.QCheckBox("关闭窗口时最小化到托盘", self)
        self.delay_spin = QtWidgets.QSpinBox(self)
        self.delay_spin.setRange(0, 600)
        self.delay_spin.setSuffix(" 秒")
        form.addRow("自启动", self.cb_manager)
        form.addRow("启动行为", self.cb_minimized)
        form.addRow("关闭行为", self.cb_close_tray)
        form.addRow("全部启动前等待", self.delay_spin)

        self.cb_watchdog = QtWidgets.QCheckBox("启用掉线自动重启（全局开关）", self)
        self.watchdog_spin = QtWidgets.QSpinBox(self)
        self.watchdog_spin.setRange(1, 1440)
        self.watchdog_spin.setSuffix(" 分钟")
        try:
            self.watchdog_spin.setToolTip(
                "每隔该时长检测一次已勾选「守护」的应用，未运行则重新启动（默认 20 分钟）"
            )
            self.cb_watchdog.setToolTip(
                "关闭后暂停所有条目的掉线检测与自动重启，各条目自身的勾选会保留"
            )
        except Exception:
            pass
        form.addRow("掉线守护", self.cb_watchdog)
        form.addRow("检测间隔", self.watchdog_spin)

        row = QtWidgets.QHBoxLayout()
        self.btn_open_dir = QtWidgets.QPushButton("打开配置目录", self)
        self.btn_apply_autostart = QtWidgets.QPushButton(
            "立即启用/停用管理器自启", self
        )
        try:
            self.btn_open_dir.clicked.connect(self.open_config_dir)
            self.btn_apply_autostart.clicked.connect(self.apply_manager_autostart)
        except Exception:
            pass
        row.addWidget(self.btn_open_dir)
        row.addWidget(self.btn_apply_autostart)
        layout.addLayout(row)
        layout.addStretch(1)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        self.save_button = QtWidgets.QPushButton("保存", self)
        self.cancel_button = QtWidgets.QPushButton("取消", self)
        try:
            self.save_button.clicked.connect(self._on_save)
            self.cancel_button.clicked.connect(self.reject)
            self.save_button.setDefault(True)
        except Exception:
            pass
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

    def _load_values(self):
        cfg = self.cfg
        self.cb_manager.setChecked(bool(cfg.manager_autostart))
        self.cb_minimized.setChecked(bool(cfg.start_minimized))
        self.cb_close_tray.setChecked(bool(cfg.close_to_tray))
        try:
            self.delay_spin.setValue(max(0, min(600, int(cfg.global_delay))))
        except Exception:
            self.delay_spin.setValue(0)
        try:
            self.cb_watchdog.setChecked(bool(getattr(cfg, "watchdog_enabled", True)))
            self.watchdog_spin.setValue(
                max(1, min(1440, int(getattr(cfg, "watchdog_interval_minutes", 20))))
            )
        except Exception:
            self.cb_watchdog.setChecked(True)
            self.watchdog_spin.setValue(20)
        try:
            actual = autostart.manager_autostart_enabled()
            self.cb_manager.setChecked(bool(actual))
        except Exception:
            pass

    def open_config_dir(self):
        try:
            paths.ensure_dirs()
        except Exception:
            pass
        try:
            opened = bool(
                QtGui.QDesktopServices.openUrl(
                    QtCore.QUrl.fromLocalFile(paths.config_dir())
                )
            )
        except Exception:
            opened = False
        if not opened:
            try:
                QtWidgets.QMessageBox.information(
                    self, "配置目录", "配置目录：%s" % (paths.config_dir(),)
                )
            except Exception:
                pass

    def apply_manager_autostart(self):
        enabled = bool(self.cb_manager.isChecked())
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
            self.cb_manager.setChecked(bool(actual))
        except Exception:
            pass
        try:
            if ok:
                QtWidgets.QMessageBox.information(
                    self,
                    "已生效",
                    "管理器开机自启动已%s。" % ("启用" if enabled else "停用",),
                )
            else:
                QtWidgets.QMessageBox.warning(
                    self, "未生效", "写入自启动文件失败，请检查目录权限。"
                )
        except Exception:
            pass

    def _on_save(self):
        try:
            self.store.set_option("manager_autostart", bool(self.cb_manager.isChecked()))
            self.store.set_option("start_minimized", bool(self.cb_minimized.isChecked()))
            self.store.set_option("close_to_tray", bool(self.cb_close_tray.isChecked()))
            self.store.set_option("global_delay", int(self.delay_spin.value()))
            self.store.set_option("watchdog_enabled", bool(self.cb_watchdog.isChecked()))
            self.store.set_option("watchdog_interval_minutes", int(self.watchdog_spin.value()))
        except Exception as exc:
            try:
                QtWidgets.QMessageBox.warning(
                    self, "保存失败", "无法保存设置：%s" % (exc,)
                )
            except Exception:
                pass
            return
        try:
            autostart.set_manager_autostart(bool(self.cb_manager.isChecked()))
        except Exception:
            pass
        self.accept()


# ---------------------------------------------------------------------------
# 托盘图标
# ---------------------------------------------------------------------------
class TrayIcon(_TrayBase):
    """托盘图标：显示主窗口 / 立即启动全部应用 / 打开配置目录 / 退出。"""

    def __init__(self, window, parent=None):
        icon = create_icon()
        _TrayBase.__init__(self, icon, parent if parent is not None else window)
        self.window = window
        self._menu = QtWidgets.QMenu()
        action_show = self._menu.addAction("显示主窗口")
        action_start = self._menu.addAction("立即启动全部应用")
        action_check = self._menu.addAction("立即检测掉线应用")
        action_open = self._menu.addAction("打开配置目录")
        self._menu.addSeparator()
        action_quit = self._menu.addAction("退出")
        self._actions = (action_show, action_start, action_check, action_open, action_quit)
        try:
            action_show.triggered.connect(self._show_window)
            action_start.triggered.connect(self._start_all)
            action_check.triggered.connect(self._check_now)
            action_open.triggered.connect(self._open_config_dir)
            action_quit.triggered.connect(self._quit)
        except Exception:
            pass
        try:
            self.setToolTip(paths.APP_NAME)
            self.setContextMenu(self._menu)
            self.activated.connect(self._on_activated)
        except Exception:
            pass

    def _show_window(self, *_args):
        try:
            self.window.show_normal()
        except Exception:
            pass

    def _start_all(self, *_args):
        try:
            self.window.start_all()
        except Exception:
            pass

    def _check_now(self, *_args):
        try:
            self.window.check_now()
        except Exception:
            pass

    def _open_config_dir(self, *_args):
        try:
            self.window.open_config_dir()
        except Exception:
            pass

    def _quit(self, *_args):
        try:
            self.window.quit_app()
        except Exception:
            pass

    def _on_activated(self, reason):
        try:
            if reason == QtWidgets.QSystemTrayIcon.DoubleClick:
                self.window.show_normal()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 模块入口
# ---------------------------------------------------------------------------
def main(store, options=None):
    """启动 Qt 图形界面，返回进程退出码。"""
    if not _QT_OK:
        sys.stderr.write("未安装 PyQt5，无法启动 Qt 图形界面。\n")
        return 2
    options = dict(options or {})
    app = create_app([])
    window = MainWindow(store, options)

    tray = None
    if tray_available():
        try:
            tray = TrayIcon(window)
            window.set_tray(tray)
            tray.show()
        except Exception:
            tray = None
            window.set_tray(None)

    if bool(options.get("minimized")) and tray is not None:
        # 开机自启：直接驻留托盘，不打扰用户
        window.hide()
    else:
        window.show()

    try:
        return int(app.exec_())
    except KeyboardInterrupt:
        return 0
