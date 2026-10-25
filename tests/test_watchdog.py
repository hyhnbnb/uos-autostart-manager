# -*- coding: utf-8 -*-
"""掉线自动重启（守护）相关单元测试：进程探测 / Watchdog / 配置字段。"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from uos_autostart import model, processes, store, watchdog  # noqa: E402


class _EnvMixin(object):
    def setUp(self):
        self._old = {}
        for name in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_DATA_DIRS"):
            self._old[name] = os.environ.get(name)
        self.tmpdir = tempfile.mkdtemp(prefix="uosam-wd-")
        os.environ["HOME"] = self.tmpdir
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.tmpdir, ".config")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.tmpdir, ".local", "share")
        os.environ["XDG_DATA_DIRS"] = os.path.join(self.tmpdir, "xdg-data")
        self.store = store.ConfigStore()

    def tearDown(self):
        for name, value in self._old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class ProcessProbeTests(unittest.TestCase):
    def test_absolute_path_match(self):
        entries = [(321, "/opt/app/bin/music --minimized")]
        self.assertTrue(processes.is_process_running("/opt/app/bin/music --flag", entries))

    def test_basename_match(self):
        entries = [(321, "/usr/bin/fcitx5")]
        self.assertTrue(processes.is_process_running("fcitx5", entries))

    def test_not_running(self):
        entries = [(321, "/usr/bin/other-app")]
        self.assertFalse(processes.is_process_running("/opt/app/bin/music", entries))

    def test_own_manager_process_is_ignored(self):
        command = '"%s" -m uos_autostart --tray' % sys.executable
        entries = [(os.getpid(), command), (os.getppid(), command)]
        self.assertFalse(processes.is_process_running(command, entries))

    def test_empty_command(self):
        self.assertFalse(processes.is_process_running("", []))
        self.assertFalse(processes.is_process_running(None, []))

    def test_field_codes_are_stripped_before_match(self):
        self.assertEqual(processes.target_argv("/usr/bin/app %U"), ["/usr/bin/app"])
        entries = [(9, "/usr/bin/app --now")]
        self.assertTrue(processes.is_process_running("/usr/bin/app %U", entries))

    def test_command_basename(self):
        self.assertEqual(processes.command_basename('"/opt/My App/run"'), "run")
        self.assertEqual(processes.command_basename("fcitx5"), "fcitx5")
        self.assertEqual(processes.command_basename(""), "")

    def test_pid_alive(self):
        self.assertTrue(processes.pid_alive(os.getpid()))
        self.assertFalse(processes.pid_alive(99999999))
        self.assertFalse(processes.pid_alive("bad"))

    def test_list_processes_returns_entries(self):
        entries = processes.list_processes()
        self.assertIsInstance(entries, list)
        for entry in entries:
            self.assertEqual(len(entry), 2)
            self.assertIsInstance(entry[0], int)


class _FakeLauncher(object):
    def __init__(self, result=True):
        self.result = result
        self.started = []

    def start_item(self, item):
        self.started.append(item.id)
        return self.result


class ModelWatchdogFieldTests(_EnvMixin, unittest.TestCase):
    def test_defaults(self):
        item = model.AutostartItem.new("应用", "/usr/bin/app")
        self.assertFalse(item.watchdog)
        cfg = model.Config()
        self.assertTrue(cfg.watchdog_enabled)
        self.assertEqual(cfg.watchdog_interval_minutes, 20)

    def test_roundtrip(self):
        item = model.AutostartItem.new("应用", "/usr/bin/app", watchdog=True)
        again = model.AutostartItem.from_dict(item.to_dict())
        self.assertTrue(again.watchdog)
        cfg = model.Config(watchdog_enabled=False, watchdog_interval_minutes=30)
        cfg.items.append(item)
        again_cfg = model.Config.from_dict(cfg.to_dict())
        self.assertFalse(again_cfg.watchdog_enabled)
        self.assertEqual(again_cfg.watchdog_interval_minutes, 30)
        self.assertTrue(again_cfg.items[0].watchdog)

    def test_interval_clamped(self):
        self.assertEqual(model.Config.from_dict({"watchdog_interval_minutes": 0}).watchdog_interval_minutes, 1)
        self.assertEqual(model.Config.from_dict({"watchdog_interval_minutes": 99999}).watchdog_interval_minutes, 1440)
        self.assertEqual(model.Config.from_dict({"watchdog_interval_minutes": "abc"}).watchdog_interval_minutes, 20)

    def test_watchdog_items_filter(self):
        cfg = model.Config()
        a = model.AutostartItem.new("守护开启", "/usr/bin/a", watchdog=True)
        b = model.AutostartItem.new("守护关闭", "/usr/bin/b")
        c = model.AutostartItem.new("已停用", "/usr/bin/c", watchdog=True)
        c.enabled = False
        cfg.items.extend([a, b, c])
        self.assertEqual([item.id for item in cfg.watchdog_items()], [a.id])

    def test_display_watchdog(self):
        self.assertEqual(model.AutostartItem.new("x", "y", watchdog=True).display_watchdog(), "开启")
        self.assertEqual(model.AutostartItem.new("x", "y").display_watchdog(), "关闭")


class StoreWatchdogOptionTests(_EnvMixin, unittest.TestCase):
    def test_set_option_clamps_interval(self):
        cfg = self.store.set_option("watchdog_interval_minutes", 0)
        self.assertEqual(cfg.watchdog_interval_minutes, 1)
        cfg = self.store.set_option("watchdog_interval_minutes", 5000)
        self.assertEqual(cfg.watchdog_interval_minutes, 1440)
        cfg = self.store.set_option("watchdog_enabled", False)
        self.assertFalse(cfg.watchdog_enabled)
        self.assertFalse(self.store.load().watchdog_enabled)

    def test_watchdog_fields_persist(self):
        item = model.AutostartItem.new("带守护", "/usr/bin/app", watchdog=True)
        self.store.add(item)
        loaded = self.store.load().find(item.id)
        self.assertTrue(loaded.watchdog)
        self.assertTrue(self.store.load().watchdog_enabled)
        self.assertEqual(self.store.load().watchdog_interval_minutes, 20)

    def test_config_with_bom_is_readable(self):
        """用户用 Windows 编辑器保存、带 UTF-8 BOM 的配置也应能读取。"""
        path = self.store.path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = json.dumps({
            "version": 1,
            "watchdog_enabled": False,
            "watchdog_interval_minutes": 45,
            "items": [{"id": "bom000000001", "name": "BOM 项",
                       "command": "/usr/bin/app", "watchdog": True}],
        }, ensure_ascii=False)
        with open(path, "wb") as fh:
            fh.write(b"\xef\xbb\xbf" + payload.encode("utf-8"))
        cfg = self.store.load()
        self.assertFalse(cfg.watchdog_enabled)
        self.assertEqual(cfg.watchdog_interval_minutes, 45)
        self.assertTrue(cfg.items[0].watchdog)


class WatchdogRunOnceTests(_EnvMixin, unittest.TestCase):
    def _seed(self, **kwargs):
        cfg = self.store.load()
        cfg.watchdog_enabled = kwargs.get("global_enabled", True)
        cfg.watchdog_interval_minutes = kwargs.get("interval", 20)
        restarted = model.AutostartItem.new(
            "掉线应用", "/opt/apps/music/bin/music %U", watchdog=True
        )
        running = model.AutostartItem.new(
            "运行中应用", "/opt/apps/chat/bin/chat", watchdog=True
        )
        plain = model.AutostartItem.new("无守护应用", "/opt/apps/tool/tool")
        disabled = model.AutostartItem.new(
            "已停用守护", "/opt/apps/off/off", watchdog=True
        )
        disabled.enabled = False
        cfg.items = [restarted, running, plain, disabled]
        self.store.save(cfg)
        return cfg, restarted, running

    def test_restart_missing_and_skip_running(self):
        cfg, restarted, running = self._seed()
        entries = [(100, "/opt/apps/chat/bin/chat --tray")]
        fake = _FakeLauncher()
        guard = watchdog.Watchdog(self.store, launcher=fake, probe=lambda: entries)
        result = guard.run_once()
        self.assertTrue(result["enabled"])
        self.assertEqual(result["checked"], 2)  # 只统计启用且勾选守护的条目
        self.assertEqual(result["restarted"], [restarted.id])
        self.assertEqual(result["running"], [running.id])
        self.assertEqual(fake.started, [restarted.id])
        self.assertEqual(result["errors"], [])

    def test_global_switch_off_does_nothing(self):
        self._seed(global_enabled=False)
        fake = _FakeLauncher()
        guard = watchdog.Watchdog(self.store, launcher=fake, probe=lambda: [])
        result = guard.run_once()
        self.assertFalse(result["enabled"])
        self.assertEqual(result["checked"], 0)
        self.assertEqual(fake.started, [])

    def test_no_watchdog_items(self):
        cfg = self.store.load()
        cfg.items = [model.AutostartItem.new("普通", "/usr/bin/plain")]
        self.store.save(cfg)
        fake = _FakeLauncher()
        guard = watchdog.Watchdog(self.store, launcher=fake, probe=lambda: [])
        result = guard.run_once()
        self.assertEqual(result["checked"], 0)
        self.assertEqual(fake.started, [])

    def test_failure_recorded(self):
        self._seed()
        fake = _FakeLauncher(result=False)
        guard = watchdog.Watchdog(self.store, launcher=fake, probe=lambda: [])
        result = guard.run_once()
        self.assertEqual(len(result["failed"]), 2)  # restarted + running 都未在运行
        self.assertEqual(result["restarted"], [])

    def test_probe_error_reported(self):
        self._seed()

        def broken_probe():
            raise RuntimeError("无法读取进程表")

        fake = _FakeLauncher()
        guard = watchdog.Watchdog(self.store, launcher=fake, probe=broken_probe)
        result = guard.run_once()
        self.assertTrue(result["errors"])
        self.assertEqual(len(result["restarted"]), 2)

    def test_interval_override_and_config(self):
        self._seed(interval=7)
        guard = watchdog.Watchdog(self.store, launcher=_FakeLauncher(), probe=lambda: [])
        self.assertEqual(guard.interval_minutes(), 7)
        self.assertEqual(guard.interval_seconds(), 7 * 60)
        quick = watchdog.Watchdog(
            self.store, launcher=_FakeLauncher(), probe=lambda: [], interval_seconds=0.05
        )
        self.assertAlmostEqual(quick.interval_seconds(), 0.05)

    def test_thread_runs_and_stops(self):
        self._seed()
        fake = _FakeLauncher()
        guard = watchdog.Watchdog(
            self.store, launcher=fake, probe=lambda: [], interval_seconds=0.05
        )
        thread = guard.start()
        self.assertTrue(guard.is_running())
        deadline = time.time() + 3
        while guard.rounds < 1 and time.time() < deadline:
            time.sleep(0.02)
        guard.stop()
        self.assertFalse(guard.is_running())
        self.assertGreaterEqual(guard.rounds, 1)
        self.assertFalse(thread.is_alive())
        self.assertTrue(fake.started)  # 至少重新拉起过一次

    def test_clamp_interval_helper(self):
        self.assertEqual(watchdog.clamp_interval_minutes(0), 1)
        self.assertEqual(watchdog.clamp_interval_minutes(99999), 1440)
        self.assertEqual(watchdog.clamp_interval_minutes("oops"), 20)
        self.assertEqual(watchdog.clamp_interval_minutes(20), 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
