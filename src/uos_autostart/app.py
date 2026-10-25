# -*- coding: utf-8 -*-
"""命令行入口：参数解析、单实例锁、后端选择与启动流程。

本模块顶层不导入 PyQt5 / tkinter；只有在真正需要图形界面时才按需 import
对应的 GUI 后端模块，因此在无图形环境下 ``--list`` / ``--doctor`` /
``--start-all`` / ``--version`` 依然可用。
"""

import argparse
import errno
import os
import sys

from . import autostart, cli, launcher, paths
from . import watchdog as watchdog_module
from .model import Config
from .store import ConfigStore

try:  # Linux / UOS 正常路径
    import fcntl
except ImportError:  # pragma: no cover - Windows 下走 msvcrt
    fcntl = None

try:  # Windows 兜底
    import msvcrt
except ImportError:  # pragma: no cover - Linux 下没有
    msvcrt = None


_LOCK_ERRNOS = set()
for _name in ("EACCES", "EAGAIN", "EDEADLK"):
    _value = getattr(errno, _name, None)
    if _value is not None:
        _LOCK_ERRNOS.add(_value)


class SingleInstance(object):
    """基于锁文件的单实例保护。

    用法::

        with SingleInstance() as guard:
            if not guard:   # 已被其它实例占用
                ...

    或者显式调用 ``acquire()`` / ``release()``。
    获取失败只返回 False，绝不抛异常；锁机制本身不可用（例如文件无法创建）时
    为"允许继续"返回 True，避免把用户挡在门外。
    """

    def __init__(self, lock_path=None):
        if lock_path is None:
            lock_path = os.path.join(paths.state_dir(), "manager.lock")
        self.lock_path = lock_path
        self._handle = None
        self.acquired = False

    # ---- 内部工具 -------------------------------------------------
    def _ensure_parent(self):
        directory = os.path.dirname(self.lock_path)
        if not directory:
            return
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError:
            pass

    def _try_lock(self):
        if fcntl is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        if msvcrt is not None:
            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        # 两种锁都不可用：视为"允许继续"

    def _unlock(self):
        if self._handle is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:
                try:
                    self._handle.seek(0)
                    msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        except OSError:
            pass

    # ---- 公开 API -------------------------------------------------
    def acquire(self):
        """尝试获取锁；返回 True 表示可以继续。"""
        if self.acquired:
            return True
        self._ensure_parent()
        try:
            self._handle = open(self.lock_path, "a+")
        except OSError:
            # 锁文件都建不了：允许继续，不阻塞用户
            self._handle = None
            self.acquired = True
            return True
        try:
            self._try_lock()
        except OSError as exc:
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None
            self.acquired = False
            if exc.errno in _LOCK_ERRNOS:
                return False
            # 其它异常（文件系统不支持 flock 等）允许继续
            self.acquired = True
            return True
        self.acquired = True
        try:
            self._handle.seek(0)
            self._handle.truncate()
            self._handle.write("%d\n" % (os.getpid(),))
            self._handle.flush()
        except OSError:
            pass
        return True

    def release(self):
        """释放锁并关闭文件，重复调用安全。"""
        self._unlock()
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
        self._handle = None
        self.acquired = False

    def is_acquired(self):
        return bool(self.acquired)

    def __bool__(self):
        return bool(self.acquired)

    # 兼容 Python 2 风格的写法（无实际影响，保持显式）
    __nonzero__ = __bool__

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()
        return False


def build_parser():
    parser = argparse.ArgumentParser(
        prog="uos-autostart-manager",
        description="统信 UOS 开机自启动管理器：图形化管理开机自启动应用。",
        epilog="不带参数直接运行会打开图形界面。",
    )
    parser.add_argument(
        "--tray",
        action="store_true",
        help="托盘模式（开机自启使用）：后台启动托管应用后驻留托盘",
    )
    parser.add_argument(
        "--start-all",
        dest="start_all",
        action="store_true",
        help="无界面启动全部已启用项（含系统直接启动项）",
    )
    parser.add_argument(
        "--list", dest="list_mode", action="store_true", help="文本列出配置项"
    )
    parser.add_argument(
        "--doctor", action="store_true", help="输出环境诊断信息"
    )
    parser.add_argument(
        "--version", action="store_true", help="显示版本信息"
    )
    parser.add_argument(
        "--config", metavar="PATH", default=None, help="指定配置文件（调试 / 测试用）"
    )
    parser.add_argument(
        "--no-sync",
        dest="no_sync",
        action="store_true",
        help="跳过 XDG 自启动文件同步",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="只打印将要启动什么，不真正创建进程",
    )
    parser.add_argument(
        "--no-watchdog",
        dest="no_watchdog",
        action="store_true",
        help="本次运行不启动「掉线自动重启」后台守护",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="不打开界面，前台运行掉线守护（Ctrl+C 退出）",
    )
    parser.add_argument(
        "--watch-once",
        dest="watch_once",
        action="store_true",
        help="只执行一轮掉线检测并输出结果，然后退出",
    )
    parser.add_argument(
        "--watch-interval",
        dest="watch_interval",
        type=int,
        default=None,
        metavar="SECONDS",
        help="掉线检测间隔（秒），调试用；默认使用设置中的分钟数",
    )
    return parser


def select_backend():
    """返回可用的 GUI 后端模块（gui_qt 优先），都不可用时返回 None。"""
    qt_module = None
    try:
        from . import gui_qt

        qt_module = gui_qt
    except Exception:
        qt_module = None
    if qt_module is not None:
        try:
            if qt_module.available():
                return qt_module
        except Exception:
            pass

    tk_module = None
    try:
        from . import gui_tk

        tk_module = gui_tk
    except Exception:
        tk_module = None
    if tk_module is not None:
        try:
            if tk_module.available():
                return tk_module
        except Exception:
            pass
    return None


def _print_version():
    sys.stdout.write("%s %s\n" % (paths.APP_NAME, paths.VERSION))


def _warn(message):
    sys.stderr.write("警告：%s\n" % (message,))


def _setup_logging():
    try:
        launcher.setup_logging()
    except Exception:
        # 日志不可用不影响主流程
        pass


def _run_gui(args, store, cfg):
    backend = select_backend()
    if backend is None:
        sys.stderr.write(
            "未找到可用的图形界面后端。\n"
            "请安装 PyQt5（sudo apt install python3-pyqt5）或 tkinter（sudo apt install python3-tk）后重试。\n"
        )
        return 2

    tray_mode = bool(args.tray or getattr(cfg, "start_minimized", False))
    options = {
        "tray": tray_mode,
        "minimized": tray_mode,
        "dry_run": bool(args.dry_run),
    }

    guard = None
    if tray_mode:
        # 开机自启场景：先让托管应用在后台启动，再驻留托盘
        try:
            runner = launcher.Launcher(store, dry_run=bool(args.dry_run))
            runner.start_all_async(cfg, include_xdg=False)
        except Exception as exc:
            _warn("后台启动托管应用失败：%s" % (exc,))
    if not getattr(args, "no_watchdog", False) and not bool(args.dry_run):
        try:
            guard = watchdog_module.Watchdog(store)
            guard.start()
        except Exception as exc:
            guard = None
            _warn("掉线守护启动失败：%s" % (exc,))

    try:
        return int(backend.main(store, options))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        sys.stderr.write("图形界面启动失败：%s\n" % (exc,))
        return 2
    finally:
        if guard is not None:
            try:
                guard.stop()
            except Exception:
                pass


def main(argv=None):
    """程序入口，返回进程退出码。"""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # --help / 参数错误时保持返回整数
        code = getattr(exc, "code", 0)
        return code if isinstance(code, int) else 0

    if args.version:
        _print_version()
        return 0

    # 2. 加载配置（load 自带容错，这里再兜一层）
    store = ConfigStore(args.config)
    try:
        cfg = store.load()
    except Exception as exc:
        _warn("读取配置失败，使用默认配置：%s" % (exc,))
        cfg = Config()

    try:
        paths.ensure_dirs()
    except Exception as exc:
        _warn("创建配置目录失败：%s" % (exc,))

    _setup_logging()

    # 3. 同步 XDG 自启动文件（只告警不中断）
    if not args.no_sync:
        try:
            result = autostart.sync(cfg)
            if isinstance(result, dict):
                errors = result.get("errors") or []
                if errors:
                    _warn("同步自启动文件时出现 %d 个问题：%s" % (len(errors), errors[0]))
        except Exception as exc:
            _warn("同步自启动文件失败：%s" % (exc,))

    # 4. 文本 / 无界面模式：不加载任何 GUI
    if args.watch_once:
        return cli.run_watch_once(store, interval_seconds=args.watch_interval)
    if args.watch:
        return cli.run_watch(store, interval_seconds=args.watch_interval)
    if args.list_mode:
        return cli.run_list(store)
    if args.doctor:
        return cli.run_doctor(store)
    if args.start_all:
        return cli.run_start_all(store, dry_run=bool(args.dry_run))

    # 5. 图形模式：单实例保护
    instance = SingleInstance()
    try:
        acquired = instance.acquire()
    except Exception:
        acquired = True
    if not acquired:
        sys.stdout.write("已有实例在运行。\n")
        return 0

    try:
        return _run_gui(args, store, cfg)
    except KeyboardInterrupt:
        return 0
    finally:
        try:
            instance.release()
        except Exception:
            pass


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
