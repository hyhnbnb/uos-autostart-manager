# -*- coding: utf-8 -*-
"""文本子命令实现：``--list`` / ``--doctor`` / ``--start-all``。

本模块顶层不导入 PyQt5 / tkinter，保证在无图形环境（服务器、SSH）下也能工作。
所有输出面向用户，使用简体中文。
"""

import importlib
import os
import sys
import time

from . import autostart, launcher, paths
from . import watchdog as watchdog_module

_LEVEL_MARKS = {"ok": "[正常]", "warn": "[警告]", "error": "[错误]"}


def _line(text=""):
    sys.stdout.write("%s\n" % (text,))


def _error(text):
    sys.stderr.write("%s\n" % (text,))


def run_list(store):
    """文本列出配置项，返回进程退出码。"""
    try:
        cfg = store.load()
    except Exception as exc:  # 理论上 store 已自带容错，这里再兜一层
        _error("读取配置失败：%s" % (exc,))
        return 1

    _line("序号 状态 守护 名称 | 方式 | 延迟 | 命令")
    if not cfg.items:
        _line("（暂无配置项，运行 uos-autostart-manager 打开图形界面即可添加）")
    for index, item in enumerate(cfg.items, start=1):
        try:
            mode_text = item.display_mode()
            delay_text = item.display_delay()
        except Exception:
            mode_text = "管理器启动" if item.mode == "manager" else "系统直接启动"
            delay_text = "立即" if not item.delay else "延迟 %d 秒" % (item.delay,)
        _line(
            "%2d  %s  %s  %s | %s | %s | %s"
            % (
                index,
                "启用" if item.enabled else "停用",
                "守护" if bool(getattr(item, "watchdog", False)) else " -- ",
                item.name,
                mode_text,
                delay_text,
                item.command,
            )
        )

    try:
        enabled_count = len(cfg.enabled_items())
    except Exception:
        enabled_count = len([one for one in cfg.items if one.enabled])
    try:
        watchdog_count = len(cfg.watchdog_items())
    except Exception:
        watchdog_count = len(
            [one for one in cfg.items if one.enabled and getattr(one, "watchdog", False)]
        )
    _line("")
    _line(
        "共 %d 项，已启用 %d 项，掉线守护 %d 项 · 配置：%s"
        % (len(cfg.items), enabled_count, watchdog_count, store.path)
    )
    return 0


def run_doctor(store):
    """输出环境诊断信息（只读，不修改任何配置），返回进程退出码。"""
    _line("%s %s" % (paths.APP_NAME, paths.VERSION))
    _line("=" * 46)
    _line("Python：%s" % (sys.version.split()[0],))
    _line("平台：%s" % (sys.platform,))
    _line("配置目录：%s" % (paths.config_dir(),))
    _line(
        "配置文件：%s（%s）"
        % (
            paths.config_path(),
            "存在" if os.path.isfile(paths.config_path()) else "不存在",
        )
    )
    _line(
        "自启动目录：%s（%s）"
        % (
            paths.autostart_dir(),
            "存在" if os.path.isdir(paths.autostart_dir()) else "不存在",
        )
    )
    _line("数据目录：%s" % (paths.data_dir(),))
    _line("日志文件：%s" % (paths.log_path(),))
    _line("单实例锁：%s" % (os.path.join(paths.state_dir(), "manager.lock"),))
    _line("安装状态：%s" % ("已安装" if paths.is_installed() else "源码方式运行",))

    try:
        manager_auto = autostart.manager_autostart_enabled()
        _line("管理器自启动：%s" % ("已启用" if manager_auto else "未启用",))
    except Exception as exc:
        _line("管理器自启动：未知（%s）" % (exc,))

    try:
        cfg_head = store.load()
        interval = int(getattr(cfg_head, "watchdog_interval_minutes", 20))
        count = len(cfg_head.watchdog_items())
        if bool(getattr(cfg_head, "watchdog_enabled", True)):
            _line(
                "掉线守护：已启用 · 每 %d 分钟检测一次 · 守护条目 %d 项"
                % (interval, count)
            )
        else:
            _line("掉线守护：已停用（全局开关关闭，守护条目 %d 项）" % (count,))
    except Exception as exc:
        _line("掉线守护：未知（%s）" % (exc,))

    _line(
        "图形会话：DISPLAY=%s WAYLAND_DISPLAY=%s XDG_SESSION_TYPE=%s"
        % (
            os.environ.get("DISPLAY", "") or "（空）",
            os.environ.get("WAYLAND_DISPLAY", "") or "（空）",
            os.environ.get("XDG_SESSION_TYPE", "") or "（空）",
        )
    )

    _line("")
    _line("图形后端：")
    for label, module_name in (("Qt", "gui_qt"), ("Tk", "gui_tk")):
        usable = False
        reason = ""
        try:
            module = importlib.import_module(
                "." + module_name, __package__ or "uos_autostart"
            )
            usable = bool(module.available())
        except Exception as exc:  # 缺少依赖时也要给出可读结论
            reason = str(exc)
        suffix = ""
        if not usable and reason:
            suffix = "（%s）" % (reason,)
        _line("  %-4s %s%s" % (label, "可用" if usable else "不可用", suffix))

    _line("")
    try:
        cfg = store.load()
    except Exception as exc:
        _line("读取配置失败：%s" % (exc,))
        return 0

    try:
        entries = autostart.audit(cfg)
    except Exception as exc:
        entries = []
        _line("自启动诊断失败：%s" % (exc,))

    _line("自启动诊断：")
    if not entries:
        _line("  [正常] 未发现明显问题")
    for entry in entries:
        if not isinstance(entry, dict):
            _line("  [信息] %s" % (entry,))
            continue
        level = str(entry.get("level", "ok"))
        _line(
            "  %s %s"
            % (_LEVEL_MARKS.get(level, "[信息]"), entry.get("message", ""))
        )
    return 0


def _item_label(cfg, item_id):
    try:
        item = cfg.find(item_id)
    except Exception:
        item = None
    if item is not None:
        return item.name
    return str(item_id)


def run_start_all(store, dry_run=False):
    """无界面启动全部启用项（含 xdg 模式），返回进程退出码。"""
    try:
        cfg = store.load()
    except Exception as exc:
        _error("读取配置失败：%s" % (exc,))
        return 1

    try:
        enabled_items = cfg.enabled_items()
    except Exception:
        enabled_items = [one for one in cfg.items if one.enabled]

    if dry_run:
        _line("（演练模式：只打印将要启动的应用，不会真正创建进程）")
    _line("准备启动 %d 项已启用应用…" % (len(enabled_items),))
    if not enabled_items:
        _line("没有已启用的应用，无需启动。")
        return 0

    try:
        runner = launcher.Launcher(store, dry_run=bool(dry_run))
        result = runner.start_all(cfg, include_xdg=True)
    except Exception as exc:
        _error("启动过程出错：%s" % (exc,))
        return 1

    if not isinstance(result, dict):
        result = {}
    started = result.get("started") or []
    failed = result.get("failed") or []
    skipped = result.get("skipped") or []

    _line("已启动：%d 项" % (len(started),))
    for item_id in started:
        _line("  [成功] %s" % (_item_label(cfg, item_id),))
    if skipped:
        _line("已跳过：%d 项" % (len(skipped),))
        for item_id in skipped:
            _line("  [跳过] %s" % (_item_label(cfg, item_id),))
    if failed:
        _line("启动失败：%d 项" % (len(failed),))
        for item_id in failed:
            _line("  [失败] %s" % (_item_label(cfg, item_id),))
        return 1
    return 0


# ---------------------------------------------------------------------------
# 掉线守护（守护线程 / 单轮检测）
# ---------------------------------------------------------------------------
def _watch_result_lines(result, store):
    """把 Watchdog.run_once() 的结果转成中文文本行。"""
    lines = []
    cfg = None
    try:
        cfg = store.load()
    except Exception:
        cfg = None

    def label(item_id):
        if cfg is not None:
            try:
                item = cfg.find(item_id)
            except Exception:
                item = None
            if item is not None:
                return item.name or item.id
        return str(item_id)

    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    if not result.get("enabled"):
        lines.append("[%s] 掉线守护全局开关已关闭，本轮跳过。" % (stamp,))
        return lines
    lines.append(
        "[%s] 守护检查：检查 %d 项 · 运行中 %d 项 · 重新启动 %d 项 · 失败 %d 项"
        % (
            stamp,
            int(result.get("checked", 0)),
            len(result.get("running") or []),
            len(result.get("restarted") or []),
            len(result.get("failed") or []),
        )
    )
    for item_id in result.get("restarted") or []:
        lines.append("  [重启] %s" % (label(item_id),))
    for item_id in result.get("failed") or []:
        lines.append("  [失败] %s" % (label(item_id),))
    for message in result.get("errors") or []:
        lines.append("  [错误] %s" % (message,))
    return lines


def run_watch_once(store, interval_seconds=None):
    """执行一轮掉线检测并输出结果，返回进程退出码。"""
    guard = watchdog_module.Watchdog(store, interval_seconds=interval_seconds)
    result = guard.run_once()
    for line in _watch_result_lines(result, store):
        _line(line)
    if result.get("failed") or result.get("errors"):
        return 1
    return 0


def run_watch(store, interval_seconds=None):
    """前台运行掉线守护循环（Ctrl+C 退出），返回进程退出码。"""
    guard = watchdog_module.Watchdog(store, interval_seconds=interval_seconds)
    _line("掉线守护：前台运行模式，按 Ctrl+C 退出。")
    _line("默认每 20 分钟检查一次，可在图形界面「设置」中修改间隔。")
    try:
        while True:
            result = guard.run_once()
            for line in _watch_result_lines(result, store):
                _line(line)
            wait_seconds = float(interval_seconds) if interval_seconds else guard.interval_seconds()
            if not result.get("enabled"):
                wait_seconds = min(wait_seconds, 60.0)
            if wait_seconds < 1:
                wait_seconds = 1.0
            _line("下次检查：%d 秒后……" % (int(wait_seconds),))
            guard.wait(wait_seconds)
    except KeyboardInterrupt:
        _line("")
        _line("已退出掉线守护。")
        return 0
