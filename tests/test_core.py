# -*- coding: utf-8 -*-
"""core-engine 单元测试（SPEC 11.1）。

运行方式（仓库根目录）：
    python -m unittest tests.test_core -v

每个用例都把 HOME / USERPROFILE / XDG_* 指向 tempfile.mkdtemp()，
不会读写真实用户目录。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from uos_autostart import autostart, desktop, launcher, model, paths, store  # noqa: E402


def _command_path(path):
    """在 Windows 上把路径转成 / 分隔，避免 shlex 把反斜杠当转义符。"""
    if os.name == "nt":
        return path.replace("\\", "/")
    return path


class CoreTestCase(unittest.TestCase):
    """所有核心测试的基类：隔离 HOME / XDG 环境变量。"""

    _ENV_KEYS = (
        "HOME",
        "USERPROFILE",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_DATA_DIRS",
    )

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="uos-autostart-test-")
        self._saved_env = {}
        for key in self._ENV_KEYS:
            self._saved_env[key] = os.environ.get(key)
        os.environ["HOME"] = self.tmpdir
        os.environ["USERPROFILE"] = self.tmpdir
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.tmpdir, ".config")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.tmpdir, ".local", "share")
        os.environ["XDG_DATA_DIRS"] = os.path.join(self.tmpdir, "system-share")
        self.addCleanup(self._restore)

    def _restore(self):
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def path(self, *parts):
        return os.path.join(self.tmpdir, *parts)

    def write_text(self, relative_path, content):
        full_path = self.path(relative_path)
        parent = os.path.dirname(full_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(full_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        return full_path

    def make_tool(self, name="coretest-tool"):
        """在临时目录里造一个可执行文件，返回其绝对路径。"""
        bin_dir = self.path("bin")
        os.makedirs(bin_dir, exist_ok=True)
        if os.name == "nt":
            tool_path = os.path.join(bin_dir, name + ".exe")
        else:
            tool_path = os.path.join(bin_dir, name)
        with open(tool_path, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\nexit 0\n")
        try:
            os.chmod(tool_path, 0o755)
        except OSError:
            pass
        return tool_path


class PathsTests(CoreTestCase):
    def test_home_and_xdg_paths_respect_environment(self):
        self.assertEqual(paths.home(), os.path.abspath(self.tmpdir))
        expected_config = os.path.join(os.path.abspath(self.tmpdir), ".config")
        self.assertEqual(paths.config_dir(), os.path.join(expected_config, paths.APP_ID))
        self.assertEqual(paths.config_path(), os.path.join(paths.config_dir(), "config.json"))
        expected_data = os.path.join(
            os.path.abspath(self.tmpdir), ".local", "share", paths.APP_ID
        )
        self.assertEqual(paths.data_dir(), expected_data)
        self.assertEqual(paths.log_dir(), os.path.join(expected_data, "logs"))
        self.assertEqual(paths.log_path(), os.path.join(expected_data, "logs", "manager.log"))
        self.assertEqual(paths.state_dir(), os.path.join(expected_data, "state"))
        self.assertEqual(paths.autostart_dir(), os.path.join(expected_config, "autostart"))
        self.assertEqual(
            paths.system_autostart_path(),
            "/etc/xdg/autostart/uos-autostart-manager.desktop",
        )
        self.assertEqual(paths.manager_bin_path(), "/usr/bin/uos-autostart-manager")
        self.assertEqual(paths.icon_name(), "uos-autostart-manager")
        self.assertEqual(paths.APP_ID, "uos-autostart-manager")
        version_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION"
        )
        with open(version_file, "r", encoding="utf-8") as handle:
            self.assertEqual(paths.VERSION, handle.read().strip())

    def test_home_falls_back_to_userprofile(self):
        os.environ.pop("HOME", None)
        os.environ["USERPROFILE"] = self.path("profile-home")
        self.assertEqual(paths.home(), os.path.abspath(self.path("profile-home")))

    def test_ensure_dirs_creates_directories(self):
        paths.ensure_dirs()
        for directory in (
            paths.config_dir(),
            paths.data_dir(),
            paths.log_dir(),
            paths.state_dir(),
            paths.autostart_dir(),
        ):
            self.assertTrue(os.path.isdir(directory), directory)

    def test_manager_launch_command(self):
        command = paths.manager_launch_command()
        self.assertTrue(command)
        if paths.is_installed():
            self.assertEqual(command, [paths.manager_bin_path(), "--tray"])
        else:
            self.assertEqual(command[0], "env")
            self.assertTrue(command[1].startswith("PYTHONPATH="))
            src_root = command[1][len("PYTHONPATH="):]
            self.assertTrue(os.path.isdir(src_root))
            self.assertTrue(os.path.isdir(os.path.join(src_root, "uos_autostart")))
            self.assertEqual(command[2], sys.executable)
            self.assertEqual(command[3:], ["-m", "uos_autostart", "--tray"])

    def test_applications_dirs_only_existing_and_deduped(self):
        local_apps = self.path(".local", "share", "applications")
        os.makedirs(local_apps, exist_ok=True)
        extra_share = self.path("extra-share")
        extra_apps = os.path.join(extra_share, "applications")
        os.makedirs(extra_apps, exist_ok=True)
        saved = os.environ.get("XDG_DATA_DIRS")
        os.environ["XDG_DATA_DIRS"] = extra_share + os.pathsep + extra_share
        try:
            directories = paths.applications_dirs()
        finally:
            if saved is None:
                os.environ.pop("XDG_DATA_DIRS", None)
            else:
                os.environ["XDG_DATA_DIRS"] = saved
        self.assertIn(os.path.abspath(local_apps), directories)
        self.assertIn(os.path.abspath(extra_apps), directories)
        normalized = [os.path.normcase(directory) for directory in directories]
        self.assertEqual(len(normalized), len(set(normalized)))
        for directory in directories:
            self.assertTrue(os.path.isdir(directory))

    def test_icons_and_now_iso(self):
        self.assertIsInstance(paths.icon_paths(), list)
        icon = paths.icon_path()
        self.assertTrue(icon is None or os.path.isfile(icon))
        self.assertIsInstance(paths.assets_dir(), (str, type(None)))
        self.assertRegex(paths.now_iso(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")


class ModelTests(CoreTestCase):
    def test_new_generates_valid_id_and_defaults(self):
        item = model.AutostartItem.new("测试应用", "/usr/bin/testapp --flag")
        self.assertEqual(len(item.id), 12)
        self.assertTrue(all(char in "0123456789abcdef" for char in item.id))
        self.assertTrue(item.created_at)
        self.assertEqual(item.delay, 0)
        self.assertTrue(item.enabled)
        self.assertEqual(item.mode, "manager")
        self.assertEqual(item.run_count, 0)
        self.assertEqual(item.validate(), [])
        ids = set(model.AutostartItem.new("x", "true").id for _ in range(20))
        self.assertEqual(len(ids), 20)

    def test_new_normalizes_and_ignores_unknown_kwargs(self):
        item = model.AutostartItem.new(
            "应用", "cmd --a", delay=999, bogus="x", run_count="3", mode="bad"
        )
        self.assertEqual(item.delay, 600)
        self.assertEqual(item.mode, "manager")
        self.assertEqual(item.run_count, 3)
        self.assertFalse(hasattr(item, "bogus"))

    def test_to_dict_roundtrip(self):
        item = model.AutostartItem.new(
            "测试应用",
            "/opt/My App/run --x %U",
            workdir="/tmp/work",
            icon="app",
            comment="备注",
            delay=5,
            mode="xdg",
            terminal=True,
        )
        data = item.to_dict()
        self.assertEqual(data["id"], item.id)
        self.assertEqual(data["mode"], "xdg")
        self.assertTrue(data["terminal"])
        restored = model.AutostartItem.from_dict(data)
        self.assertEqual(restored.to_dict(), data)

    def test_from_dict_tolerates_dirty_data(self):
        restored = model.AutostartItem.from_dict({})
        self.assertEqual(len(restored.id), 12)
        self.assertEqual(restored.name, "")
        self.assertEqual(restored.command, "")
        self.assertEqual(restored.delay, 0)
        self.assertTrue(restored.enabled)
        self.assertEqual(restored.mode, "manager")
        errors = restored.validate()
        self.assertTrue(any("名称" in message for message in errors))
        self.assertTrue(any("命令" in message for message in errors))

        dirty = model.AutostartItem.from_dict(
            {
                "id": "",
                "name": "坏数据",
                "command": "cmd",
                "delay": "9999",
                "mode": "weird",
                "enabled": 0,
                "terminal": 1,
                "run_count": "7",
                "created_at": None,
            }
        )
        self.assertEqual(len(dirty.id), 12)
        self.assertEqual(dirty.delay, 600)
        self.assertEqual(dirty.mode, "manager")
        self.assertFalse(dirty.enabled)
        self.assertTrue(dirty.terminal)
        self.assertEqual(dirty.run_count, 7)
        self.assertEqual(dirty.created_at, "")
        self.assertEqual(model.AutostartItem.from_dict(None).name, "")

    def test_delay_clamp_validate_and_display(self):
        self.assertEqual(model.AutostartItem.new("a", "b", delay=-10).delay, 0)
        self.assertEqual(model.AutostartItem.new("a", "b", delay=601).delay, 600)
        item = model.AutostartItem.new("a", "b")
        self.assertEqual(item.display_delay(), "立即")
        item.delay = 5
        self.assertEqual(item.display_delay(), "延迟 5 秒")
        item.delay = 700
        self.assertNotEqual(item.validate(), [])
        item = model.AutostartItem.new("a", "b")
        item.mode = "bogus"
        self.assertNotEqual(item.validate(), [])
        item.mode = "manager"
        self.assertEqual(item.display_mode(), "管理器启动")
        item.mode = "xdg"
        self.assertEqual(item.display_mode(), "系统直接启动")

    def test_config_operations(self):
        cfg = model.Config()
        self.assertEqual(cfg.version, 1)
        first = model.AutostartItem.new("甲", "cmd-a", delay=1)
        second = model.AutostartItem.new("乙", "cmd-b", delay=2)
        cfg.items = [first, second]
        self.assertIs(cfg.find(first.id), first)
        self.assertIsNone(cfg.find("none"))
        self.assertEqual(cfg.index_of(second.id), 1)
        self.assertEqual(cfg.index_of("none"), -1)
        self.assertEqual([item.id for item in cfg.enabled_items()], [first.id, second.id])
        second.enabled = False
        self.assertEqual([item.id for item in cfg.enabled_items()], [first.id])
        second.enabled = True
        self.assertTrue(cfg.move_item(second.id, -1))
        self.assertEqual([item.id for item in cfg.items], [second.id, first.id])
        self.assertFalse(cfg.move_item(second.id, -1))
        self.assertFalse(cfg.move_item(second.id, 2))
        self.assertTrue(cfg.move_item(second.id, 1))
        self.assertTrue(cfg.remove_item(first.id))
        self.assertFalse(cfg.remove_item(first.id))
        data = cfg.to_dict()
        self.assertEqual(data["version"], 1)
        self.assertEqual(len(data["items"]), 1)
        restored = model.Config.from_dict(data)
        self.assertEqual(restored.items[0].id, second.id)
        tolerant = model.Config.from_dict(
            {"items": [{"name": "x"}, "bad"], "global_delay": "5000"}
        )
        self.assertEqual(len(tolerant.items), 1)
        self.assertEqual(tolerant.global_delay, 600)
        self.assertTrue(tolerant.manager_autostart)
        self.assertFalse(tolerant.start_minimized)
        self.assertTrue(tolerant.close_to_tray)
        self.assertEqual(model.Config.from_dict(None).items, [])


class StoreTests(CoreTestCase):
    def make_store(self):
        return store.ConfigStore(self.path("cfg", "config.json"))

    def test_load_missing_returns_default_without_creating_file(self):
        cfg_store = self.make_store()
        cfg = cfg_store.load()
        self.assertEqual(cfg.items, [])
        self.assertTrue(cfg.manager_autostart)
        self.assertEqual(cfg_store.path, os.path.abspath(self.path("cfg", "config.json")))
        self.assertFalse(os.path.exists(cfg_store.path))
        self.assertEqual(store.ConfigStore().path, paths.config_path())

    def test_crud_and_persist(self):
        cfg_store = self.make_store()
        first = model.AutostartItem.new("甲", "cmd-a", mode="xdg")
        second = model.AutostartItem.new("乙", "cmd-b")
        cfg_store.add(first)
        cfg_store.add(second)
        self.assertEqual([item.id for item in cfg_store.load_items()], [first.id, second.id])
        self.assertEqual(cfg_store.get(first.id).name, "甲")
        self.assertIsNone(cfg_store.get("nope"))
        first.name = "甲改"
        self.assertTrue(cfg_store.update(first))
        self.assertEqual(cfg_store.get(first.id).name, "甲改")
        self.assertFalse(cfg_store.update(model.AutostartItem.new("缺", "cmd-c")))
        self.assertTrue(cfg_store.move(second.id, -1))
        self.assertEqual([item.id for item in cfg_store.load_items()], [second.id, first.id])
        self.assertFalse(cfg_store.move(second.id, -1))
        self.assertTrue(cfg_store.remove(second.id))
        self.assertFalse(cfg_store.remove(second.id))
        reloaded = store.ConfigStore(cfg_store.path)
        self.assertEqual([item.id for item in reloaded.load_items()], [first.id])

    def test_set_option(self):
        cfg_store = self.make_store()
        self.assertFalse(cfg_store.set_option("manager_autostart", False).manager_autostart)
        self.assertTrue(cfg_store.set_option("start_minimized", True).start_minimized)
        self.assertFalse(cfg_store.set_option("close_to_tray", False).close_to_tray)
        self.assertEqual(cfg_store.set_option("global_delay", 5000).global_delay, 600)
        cfg = cfg_store.set_option("unknown_key", "x")
        self.assertEqual(cfg.global_delay, 600)
        cfg = cfg_store.set_manager_autostart(True)
        self.assertTrue(cfg.manager_autostart)
        reloaded = cfg_store.load()
        self.assertTrue(reloaded.manager_autostart)
        self.assertTrue(reloaded.start_minimized)
        self.assertFalse(reloaded.close_to_tray)
        self.assertEqual(reloaded.global_delay, 600)

    def test_atomic_save_leaves_no_temp_file(self):
        cfg_store = self.make_store()
        cfg_store.add(model.AutostartItem.new("甲", "cmd-a"))
        parent = os.path.dirname(cfg_store.path)
        leftovers = [name for name in os.listdir(parent) if ".tmp." in name]
        self.assertEqual(leftovers, [])
        with open(cfg_store.path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["version"], 1)
        self.assertEqual(len(data["items"]), 1)
        self.assertTrue(data["items"][0]["id"])

    def test_corrupt_json_is_backed_up(self):
        cfg_store = self.make_store()
        os.makedirs(os.path.dirname(cfg_store.path), exist_ok=True)
        with open(cfg_store.path, "w", encoding="utf-8") as handle:
            handle.write("{ this is not json")
        cfg = cfg_store.load()
        self.assertEqual(cfg.items, [])
        self.assertFalse(os.path.exists(cfg_store.path))
        backups = [
            name
            for name in os.listdir(os.path.dirname(cfg_store.path))
            if ".corrupt." in name
        ]
        self.assertEqual(len(backups), 1)
        cfg_store.add(model.AutostartItem.new("甲", "cmd-a"))
        self.assertTrue(os.path.isfile(cfg_store.path))

    def test_valid_json_but_wrong_shape_is_backed_up(self):
        cfg_store = self.make_store()
        os.makedirs(os.path.dirname(cfg_store.path), exist_ok=True)
        with open(cfg_store.path, "w", encoding="utf-8") as handle:
            handle.write("[]")
        self.assertEqual(cfg_store.load().items, [])
        backups = [
            name
            for name in os.listdir(os.path.dirname(cfg_store.path))
            if ".corrupt." in name
        ]
        self.assertEqual(len(backups), 1)

    def test_reset(self):
        cfg_store = self.make_store()
        cfg_store.add(model.AutostartItem.new("甲", "cmd-a"))
        cfg = cfg_store.reset()
        self.assertEqual(cfg.items, [])
        self.assertEqual(cfg_store.load().items, [])
        self.assertTrue(os.path.isfile(cfg_store.path))


class DesktopTests(CoreTestCase):
    def test_parse_desktop_file(self):
        content = (
            "# 这是注释\n"
            "\n"
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=示例应用\n"
            "Comment=示例备注\n"
            "Exec=/usr/bin/example %U\n"
            "Icon=example\n"
            "Terminal=false\n"
            "Categories=Utility;\n"
            "\n"
            "[Desktop Action new]\n"
            "Name=新建\n"
            "Exec=/usr/bin/example --new\n"
        )
        path = self.write_text("fixtures/example.desktop", content)
        data = desktop.parse_desktop_file(path)
        self.assertEqual(data["Type"], "Application")
        self.assertEqual(data["Name"], "示例应用")
        self.assertEqual(data["Exec"], "/usr/bin/example %U")
        self.assertEqual(data["Icon"], "example")
        self.assertEqual(data["Categories"], "Utility;")
        self.assertEqual(data.get("Exec"), "/usr/bin/example %U")
        self.assertEqual(desktop.parse_desktop_file(self.path("missing.desktop")), {})

    def test_strip_field_codes(self):
        self.assertEqual(desktop.strip_field_codes("/usr/bin/app %U"), "/usr/bin/app")
        self.assertEqual(
            desktop.strip_field_codes("/usr/bin/app %F --flag %i"),
            "/usr/bin/app --flag",
        )
        self.assertEqual(desktop.strip_field_codes("  /usr/bin/app   %u  "), "/usr/bin/app")
        self.assertNotIn(
            "%",
            desktop.strip_field_codes("app %f %U %i %c %k %d %D %n %N %v %m %%"),
        )
        self.assertEqual(desktop.strip_field_codes(""), "")
        self.assertEqual(desktop.strip_field_codes(None), "")

    def test_split_command(self):
        self.assertEqual(
            desktop.split_command('"/opt/My App/run" --x "a b"'),
            ["/opt/My App/run", "--x", "a b"],
        )
        self.assertEqual(
            desktop.split_command("/opt/My App/run --x"),
            ["/opt/My", "App/run", "--x"],
        )
        self.assertEqual(desktop.split_command(""), [])
        self.assertEqual(desktop.split_command(None), [])
        self.assertEqual(desktop.split_command('foo "unterminated'), ['foo', '"unterminated'])
        self.assertEqual(desktop.split_command("app %U"), ["app", "%U"])

    def test_quote_exec_arg_and_exec_line(self):
        self.assertEqual(desktop.quote_exec_arg("/usr/bin/app"), "/usr/bin/app")
        self.assertEqual(desktop.quote_exec_arg("/opt/My App/app"), '"/opt/My App/app"')
        self.assertEqual(desktop.quote_exec_arg('a"b'), '"a\\"b"')
        self.assertEqual(desktop.quote_exec_arg(""), '""')
        self.assertEqual(
            desktop.exec_line(["/usr/bin/app", "a b"]), '/usr/bin/app "a b"'
        )

    def test_sanitize_value(self):
        self.assertEqual(desktop.sanitize_value("  甲  应用  "), "甲 应用")
        self.assertEqual(desktop.sanitize_value("a\r\nb"), "a b")
        self.assertEqual(desktop.sanitize_value("a\t\tb"), "a b")
        self.assertEqual(desktop.sanitize_value(None), "")
        self.assertIs(desktop._sanitize_value, desktop.sanitize_value)

    def test_build_item_desktop(self):
        item = model.AutostartItem.new("测试 应用", "/usr/bin/testapp --flag %U", delay=5)
        content = desktop.build_item_desktop(item)
        lines = content.splitlines()
        self.assertEqual(lines[0], "[Desktop Entry]")
        self.assertIn("Type=Application", lines)
        self.assertIn("Version=1.0", lines)
        self.assertIn("Name=测试 应用", lines)
        self.assertIn("Comment=由 UOS 自启动管理器托管", lines)
        self.assertIn("Exec=/usr/bin/testapp --flag", lines)
        self.assertIn("Terminal=false", lines)
        self.assertIn("Hidden=false", lines)
        self.assertIn("X-GNOME-Autostart-enabled=true", lines)
        self.assertIn("X-GNOME-Autostart-Delay=5", lines)
        self.assertIn("X-UOS-Autostart-Manager=true", lines)
        self.assertFalse(any(line.startswith("Icon=") for line in lines))
        self.assertFalse(any(line.startswith("Path=") for line in lines))
        self.assertTrue(content.endswith("\n"))

        item.icon = "/usr/share/icons/app.png"
        item.workdir = "/opt/my app"
        item.comment = "备注\n带换行"
        item.terminal = True
        item.delay = 10
        lines = desktop.build_item_desktop(item).splitlines()
        self.assertIn("Icon=/usr/share/icons/app.png", lines)
        self.assertIn("Path=/opt/my app", lines)
        self.assertIn("Comment=备注 带换行", lines)
        self.assertIn("Terminal=true", lines)
        self.assertIn("X-GNOME-Autostart-Delay=10", lines)

        self.assertEqual(desktop.build_item_desktop(model.AutostartItem.new("甲", "")), "")
        self.assertEqual(
            desktop.build_item_desktop(model.AutostartItem.new("甲", "   ")), ""
        )

    def test_build_manager_desktop(self):
        argv = ["/usr/bin/uos-autostart-manager", "--tray"]
        enabled_lines = desktop.build_manager_desktop(argv, False).splitlines()
        self.assertIn("Name=统信 UOS 开机自启动管理器", enabled_lines)
        self.assertIn("Comment=管理开机自启动应用", enabled_lines)
        self.assertIn("Exec=/usr/bin/uos-autostart-manager --tray", enabled_lines)
        self.assertIn("Icon=uos-autostart-manager", enabled_lines)
        self.assertIn("Terminal=false", enabled_lines)
        self.assertIn("Hidden=false", enabled_lines)
        self.assertIn("X-GNOME-Autostart-enabled=true", enabled_lines)
        self.assertIn("X-UOS-Autostart-Manager=true", enabled_lines)
        disabled_lines = desktop.build_manager_desktop(argv, True).splitlines()
        self.assertIn("Hidden=true", disabled_lines)
        self.assertIn("X-GNOME-Autostart-enabled=false", disabled_lines)

    def test_discover_applications(self):
        base = self.path("apps-a")
        other = self.path("apps-b")
        nested = os.path.join(base, "kde4")
        for directory in (base, nested, other):
            os.makedirs(directory, exist_ok=True)

        def write_entry(file_name, rows):
            return self.write_text(
                file_name, "[Desktop Entry]\n" + "\n".join(rows) + "\n"
            )

        write_entry(
            "apps-a/uos-coretest-alpha.desktop",
            [
                "Type=Application",
                "Name=Alpha",
                "Comment=第一个",
                "Exec=/usr/bin/alpha %U",
                "Icon=alpha",
            ],
        )
        write_entry(
            "apps-a/uos-coretest-hidden.desktop",
            ["Type=Application", "Name=隐藏", "Exec=/usr/bin/hidden", "NoDisplay=true"],
        )
        write_entry(
            "apps-a/uos-coretest-invisible.desktop",
            ["Type=Application", "Name=隐藏项", "Exec=/usr/bin/inv", "Hidden=true"],
        )
        write_entry(
            "apps-a/uos-coretest-link.desktop",
            ["Type=Link", "Name=链接", "Exec=/usr/bin/link"],
        )
        write_entry(
            "apps-a/uos-coretest-tryexec.desktop",
            ["Type=Application", "Name=TryExec 项", "TryExec=/usr/bin/tryexec"],
        )
        write_entry(
            "apps-a/kde4/uos-coretest-nested.desktop",
            ["Type=Application", "Name=嵌套", "Exec=/usr/bin/nested"],
        )
        write_entry(
            "apps-b/uos-coretest-alpha.desktop",
            ["Type=Application", "Name=Alpha 重复", "Exec=/usr/bin/alpha2"],
        )
        write_entry(
            "apps-b/uos-coretest-beta.desktop",
            ["Type=Application", "Name=Beta", "Exec=/usr/bin/beta --u %U"],
        )

        entries = desktop.discover_applications(extra_dirs=[base, other])
        by_id = dict((entry.desktop_id, entry) for entry in entries)
        self.assertIn("uos-coretest-alpha.desktop", by_id)
        self.assertEqual(by_id["uos-coretest-alpha.desktop"].name, "Alpha")
        self.assertEqual(by_id["uos-coretest-alpha.desktop"].command, "/usr/bin/alpha")
        self.assertEqual(by_id["uos-coretest-alpha.desktop"].display_label(), "Alpha — 第一个")
        self.assertIn("uos-coretest-beta.desktop", by_id)
        self.assertEqual(by_id["uos-coretest-beta.desktop"].command, "/usr/bin/beta --u")
        self.assertIn("uos-coretest-nested.desktop", by_id)
        self.assertEqual(by_id["uos-coretest-tryexec.desktop"].command, "/usr/bin/tryexec")
        self.assertNotIn("uos-coretest-hidden.desktop", by_id)
        self.assertNotIn("uos-coretest-invisible.desktop", by_id)
        self.assertNotIn("uos-coretest-link.desktop", by_id)
        self.assertFalse(by_id["uos-coretest-alpha.desktop"].terminal)

        ours = [entry for entry in entries if entry.desktop_id.startswith("uos-coretest-")]
        names = [entry.name.lower() for entry in ours]
        self.assertEqual(names, sorted(names))
        self.assertIsInstance(desktop.discover_applications(), list)
        self.assertEqual(desktop.desktop_file_name("abc123"), "uos-am-abc123.desktop")


class AutostartTests(CoreTestCase):
    def make_store(self):
        return store.ConfigStore(self.path("cfg", "config.json"))

    def test_constants(self):
        self.assertEqual(autostart.MANAGER_DESKTOP_FILE, "uos-autostart-manager.desktop")
        self.assertEqual(autostart.ITEM_PREFIX, "uos-am-")
        self.assertEqual(
            autostart.manager_override_path(),
            os.path.join(paths.autostart_dir(), "uos-autostart-manager.desktop"),
        )

    def test_sync_writes_and_removes_item_files(self):
        cfg_store = self.make_store()
        xdg_item = model.AutostartItem.new(
            "系统启动项",
            "/usr/bin/xdgapp --now %U",
            mode="xdg",
            delay=3,
            icon="xdgapp",
            comment="注释",
        )
        manager_item = model.AutostartItem.new("管理器项", "/usr/bin/mgrapp")
        cfg_store.add(xdg_item)
        cfg_store.add(manager_item)
        result = autostart.sync(cfg_store.load())
        item_path = autostart.item_file_path(xdg_item.id)
        self.assertEqual(result["errors"], [])
        self.assertIn(item_path, result["written"])
        self.assertEqual(result["removed"], [])
        self.assertTrue(os.path.isfile(item_path))
        self.assertFalse(os.path.exists(autostart.item_file_path(manager_item.id)))
        data = desktop.parse_desktop_file(item_path)
        self.assertEqual(data["Exec"], "/usr/bin/xdgapp --now")
        self.assertEqual(data["Name"], "系统启动项")
        self.assertEqual(data["Icon"], "xdgapp")
        self.assertEqual(data["Comment"], "注释")
        self.assertEqual(data["X-GNOME-Autostart-Delay"], "3")
        self.assertEqual(data["X-UOS-Autostart-Manager"], "true")

        cfg = cfg_store.load()
        cfg.find(xdg_item.id).enabled = False
        cfg_store.save(cfg)
        result = autostart.sync(cfg_store.load())
        self.assertIn(item_path, result["removed"])
        self.assertFalse(os.path.exists(item_path))

        cfg = cfg_store.load()
        target = cfg.find(manager_item.id)
        target.mode = "xdg"
        cfg_store.save(cfg)
        result = autostart.sync(cfg_store.load())
        new_path = autostart.item_file_path(manager_item.id)
        self.assertIn(new_path, result["written"])
        self.assertTrue(os.path.isfile(new_path))

        cfg = cfg_store.load()
        cfg.find(manager_item.id).mode = "manager"
        cfg_store.save(cfg)
        result = autostart.sync(cfg_store.load())
        self.assertIn(new_path, result["removed"])
        self.assertFalse(os.path.exists(new_path))

    def test_sync_does_not_touch_manager_file(self):
        paths.ensure_dirs()
        manager_file = autostart.manager_override_path()
        with open(manager_file, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("[Desktop Entry]\nType=Application\nHidden=false\n")
        with open(manager_file, "r", encoding="utf-8") as handle:
            original = handle.read()
        stale_path = os.path.join(paths.autostart_dir(), "uos-am-deadbeef0000.desktop")
        with open(stale_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("[Desktop Entry]\nType=Application\nName=旧\n")

        cfg_store = self.make_store()
        cfg_store.add(model.AutostartItem.new("甲", "/usr/bin/甲app", mode="xdg"))
        result = autostart.sync(cfg_store.load())
        self.assertEqual(result["errors"], [])
        self.assertIn(stale_path, result["removed"])
        self.assertNotIn(manager_file, result["removed"])
        with open(manager_file, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), original)

    def test_sync_collects_errors_for_invalid_command(self):
        cfg_store = self.make_store()
        bad_item = model.AutostartItem.new("坏命令", "   ", mode="xdg")
        cfg_store.add(bad_item)
        result = autostart.sync(cfg_store.load())
        self.assertEqual(result["written"], [])
        self.assertTrue(result["errors"])
        self.assertFalse(os.path.exists(autostart.item_file_path(bad_item.id)))

    def test_sync_dry_run_does_not_write(self):
        cfg_store = self.make_store()
        item = model.AutostartItem.new("甲", "/usr/bin/甲app", mode="xdg")
        cfg_store.add(item)
        result = autostart.sync(cfg_store.load(), dry_run=True)
        self.assertIn(autostart.item_file_path(item.id), result["written"])
        self.assertFalse(os.path.exists(autostart.item_file_path(item.id)))

    def test_set_manager_autostart(self):
        self.assertTrue(autostart.set_manager_autostart(False))
        override = autostart.manager_override_path()
        self.assertTrue(os.path.isfile(override))
        data = desktop.parse_desktop_file(override)
        self.assertEqual(data["Hidden"], "true")
        self.assertEqual(data["X-GNOME-Autostart-enabled"], "false")
        self.assertEqual(data["Name"], "统信 UOS 开机自启动管理器")
        self.assertEqual(data["Exec"], desktop.exec_line(paths.manager_launch_command()))
        self.assertFalse(autostart.manager_autostart_enabled())

        self.assertTrue(autostart.set_manager_autostart(True))
        data = desktop.parse_desktop_file(override)
        self.assertEqual(data["Hidden"], "false")
        self.assertEqual(data["X-GNOME-Autostart-enabled"], "true")
        self.assertTrue(autostart.manager_autostart_enabled())

    def test_manager_autostart_enabled_falls_back_to_system_file(self):
        self.assertFalse(os.path.exists(autostart.manager_override_path()))
        expected = os.path.isfile(paths.system_autostart_path())
        self.assertEqual(autostart.manager_autostart_enabled(), expected)

    def test_audit(self):
        cfg_store = self.make_store()
        tool = self.make_tool()
        good = model.AutostartItem.new("可执行项", _command_path(tool))
        bad = model.AutostartItem.new("缺命令项", "")
        xdg_missing = model.AutostartItem.new(
            "未同步项", "/usr/bin/whatever", mode="xdg"
        )
        disabled = model.AutostartItem.new("禁用项", "/usr/bin/disabled")
        disabled.enabled = False
        for item in (good, bad, xdg_missing, disabled):
            cfg_store.add(item)
        findings = autostart.audit(cfg_store.load())
        self.assertTrue(findings)
        for finding in findings:
            self.assertIn(finding["level"], ("ok", "warn", "error"))
            self.assertIsInstance(finding["message"], str)
            self.assertTrue(finding["message"])
        self.assertTrue(any(finding["level"] == "error" and "未同步项" in finding["message"] for finding in findings))
        self.assertTrue(any(finding["level"] == "error" and "缺命令项" in finding["message"] for finding in findings))
        self.assertTrue(any("管理器" in finding["message"] for finding in findings))
        self.assertTrue(any("禁用项" in finding["message"] for finding in findings))
        empty_findings = autostart.audit(model.Config())
        self.assertTrue(any("尚未添加" in finding["message"] for finding in empty_findings))


class LauncherTests(CoreTestCase):
    def setUp(self):
        super(LauncherTests, self).setUp()
        self.store = store.ConfigStore(self.path("cfg", "config.json"))
        self.sleeps = []
        self.dry_launcher = launcher.Launcher(
            self.store, dry_run=True, sleep_func=self.sleeps.append
        )

    def test_start_item_dry_run(self):
        item = model.AutostartItem.new("甲", "/usr/bin/甲app")
        self.assertTrue(self.dry_launcher.start_item(item))
        self.assertEqual(item.run_count, 0)
        self.assertEqual(item.last_run_at, "")
        self.assertEqual(self.sleeps, [])
        empty = model.AutostartItem.new("空", "")
        self.assertFalse(self.dry_launcher.start_item(empty))

    def test_start_all_order_and_counts(self):
        cfg = model.Config(global_delay=2)
        first = model.AutostartItem.new("甲", "/usr/bin/a", delay=2, mode="manager")
        xdg = model.AutostartItem.new("乙", "/usr/bin/b", delay=3, mode="xdg")
        disabled = model.AutostartItem.new("丙", "/usr/bin/c", delay=4)
        disabled.enabled = False
        bad = model.AutostartItem.new("坏", "")
        cfg.items = [first, xdg, disabled, bad]
        result = self.dry_launcher.start_all(cfg, include_xdg=False, delay_scale=0.5)
        self.assertEqual(result["started"], [first.id])
        self.assertEqual(result["failed"], [bad.id])
        self.assertEqual(result["skipped"], [xdg.id, disabled.id])
        self.assertEqual(len(self.sleeps), 2)
        self.assertAlmostEqual(self.sleeps[0], 1.0)
        self.assertAlmostEqual(self.sleeps[1], 1.0)

    def test_start_all_loads_store_when_cfg_none(self):
        self.store.add(model.AutostartItem.new("甲", "/usr/bin/a"))
        result = self.dry_launcher.start_all()
        self.assertEqual(len(result["started"]), 1)
        self.assertEqual(result["failed"], [])

    def test_start_all_async(self):
        cfg = model.Config(items=[model.AutostartItem.new("甲", "/usr/bin/a")])
        thread = self.dry_launcher.start_all_async(cfg)
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertTrue(thread.daemon)

    def test_command_available(self):
        tool = self.make_tool()
        ok, detail = launcher.command_available(_command_path(tool))
        self.assertTrue(ok)
        self.assertTrue(os.path.isfile(detail))
        bin_dir = os.path.dirname(tool)
        saved_path = os.environ.get("PATH")
        os.environ["PATH"] = bin_dir + os.pathsep + (saved_path or "")
        try:
            ok, detail = launcher.command_available("coretest-tool")
        finally:
            if saved_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = saved_path
        self.assertTrue(ok)
        self.assertTrue(os.path.isfile(detail))
        self.assertFalse(launcher.command_available("no-such-command-coretest-xyz")[0])
        self.assertFalse(
            launcher.command_available(_command_path(self.path("missing", "tool")))[0]
        )
        self.assertFalse(launcher.command_available("")[0])
        self.assertFalse(launcher.command_available("   ")[0])

    def test_setup_logging(self):
        log_file = self.path("logs", "manager.log")
        logger = launcher.setup_logging(log_file, level="DEBUG")
        try:
            self.assertEqual(logger.name, "uos_autostart")
            self.assertEqual(logger.level, 10)
            self.assertTrue(logger.handlers)
            self.assertTrue(os.path.isfile(log_file))
            handler_count = len(logger.handlers)
            self.assertIs(launcher.setup_logging(log_file), logger)
            self.assertEqual(len(logger.handlers), handler_count)
            self.assertIs(launcher.get_logger(), logger)
        finally:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            launcher._LOG_HANDLER = None


class EndToEndTests(CoreTestCase):
    def test_store_add_sync_and_read_back(self):
        cfg_store = store.ConfigStore(self.path("cfg", "config.json"))
        item = model.AutostartItem.new(
            "端到端应用", "/usr/bin/e2e-app --arg %U", mode="xdg", delay=1, icon="e2e"
        )
        cfg_store.add(item)
        result = autostart.sync(cfg_store.load())
        self.assertEqual(result["errors"], [])
        item_path = autostart.item_file_path(item.id)
        self.assertIn(item_path, result["written"])
        self.assertTrue(os.path.isfile(item_path))
        data = desktop.parse_desktop_file(item_path)
        self.assertEqual(data["Type"], "Application")
        self.assertEqual(data["Exec"], "/usr/bin/e2e-app --arg")
        self.assertEqual(data["Name"], "端到端应用")
        self.assertEqual(data["Icon"], "e2e")
        self.assertEqual(data["Terminal"], "false")
        self.assertEqual(data["X-GNOME-Autostart-enabled"], "true")
        self.assertEqual(data["X-GNOME-Autostart-Delay"], "1")
        self.assertEqual(data["X-UOS-Autostart-Manager"], "true")
        self.assertEqual(data["Hidden"], "false")
        reloaded = store.ConfigStore(cfg_store.path).load()
        self.assertEqual([entry.id for entry in reloaded.items], [item.id])


if __name__ == "__main__":
    unittest.main()
