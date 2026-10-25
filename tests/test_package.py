# -*- coding: utf-8 -*-
"""安装包校验测试（SPEC 11.2）。

直接用标准库解析 ``dist/*.deb`` 的 ar 归档，以及其中的 control.tar.gz /
data.tar.gz，因此不依赖 dpkg / ar / tar 命令，Windows 上也能运行：

    python -m unittest tests.test_package -v

若 ``dist/`` 下还没有 .deb 安装包（尚未构建），全部用例自动跳过。
"""

import ast
import glob
import gzip
import hashlib
import io
import os
import struct
import tarfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST_DIR = os.path.join(ROOT, "dist")
VERSION_FILE = os.path.join(ROOT, "VERSION")

# SPEC 10.2 要求出现在 data 包里的文件（uos_autostart/*.py 单独展开）。
EXPECTED_DATA_FILES = [
    "usr/bin/uos-autostart-manager",
    "usr/share/applications/uos-autostart-manager.desktop",
    "etc/xdg/autostart/uos-autostart-manager.desktop",
    "usr/share/icons/hicolor/scalable/apps/uos-autostart-manager.svg",
    "usr/share/doc/uos-autostart-manager/README.md",
    "usr/share/doc/uos-autostart-manager/copyright",
    "usr/share/doc/uos-autostart-manager/changelog.gz",
] + [
    "usr/share/icons/hicolor/%dx%d/apps/uos-autostart-manager.png" % (size, size)
    for size in (16, 24, 32, 48, 64, 128, 256)
]

# SPEC 目录结构中 uos_autostart 包的全部模块。
EXPECTED_MODULES = [
    "__init__.py",
    "paths.py",
    "model.py",
    "store.py",
    "desktop.py",
    "autostart.py",
    "launcher.py",
    "processes.py",
    "watchdog.py",
    "cli.py",
    "app.py",
    "gui_qt.py",
    "gui_tk.py",
    "__main__.py",
]

MODULE_DIR = "usr/lib/uos-autostart-manager/uos_autostart/"

# SPEC 10.1 要求的 control 字段。
REQUIRED_CONTROL_FIELDS = [
    "Package",
    "Version",
    "Section",
    "Priority",
    "Architecture",
    "Maintainer",
    "Depends",
    "Recommends",
    "Installed-Size",
    "Homepage",
    "Description",
]

EXPECTED_MEMBERS = ["debian-binary", "control.tar.gz", "data.tar.gz"]

# /etc/xdg/autostart 下的文件按 Debian 约定标记为 conffile，不参与 md5sums 校验。
CONFFILES = ("etc/xdg/autostart/uos-autostart-manager.desktop",)

# ar 成员头：名字 16 / mtime 12 / uid 6 / gid 6 / mode 8 / size 10 / magic 2。
_AR_HEADER = struct.Struct("16s12s6s6s8s10s2s")
_AR_MAGIC = b"`\n"
_AR_GLOBAL_MAGIC = b"!<arch>\n"


def _read_version():
    """读取仓库根 VERSION 文件（版本号唯一来源）；不存在返回 None。"""
    if not os.path.isfile(VERSION_FILE):
        return None
    with open(VERSION_FILE, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read().strip()


def _find_deb():
    """找到待校验的 .deb；优先与 VERSION 完全匹配的文件名，否则取最新一个。"""
    if not os.path.isdir(DIST_DIR):
        return None
    version = _read_version()
    if version:
        exact = os.path.join(
            DIST_DIR, "uos-autostart-manager_%s_all.deb" % version
        )
        if os.path.isfile(exact):
            return exact
    candidates = sorted(
        glob.glob(os.path.join(DIST_DIR, "*.deb")),
        key=lambda path: (os.path.getmtime(path), path),
    )
    return candidates[-1] if candidates else None


def _norm_member_name(name):
    """归一化归档成员名：去掉 ./ 前缀、结尾斜杠与首尾空白。"""
    name = name.replace("\\", "/").strip()
    while name.startswith("./"):
        name = name[2:]
    name = name.strip("/")
    return name


def _parse_ar(blob):
    """解析 ar 归档，返回 [(成员名, 内容字节), ...]（保持原始顺序）。"""
    if not blob.startswith(_AR_GLOBAL_MAGIC):
        raise AssertionError("不是合法的 ar 归档：缺少 %r 全局魔数" % _AR_GLOBAL_MAGIC)
    members = []
    offset = len(_AR_GLOBAL_MAGIC)
    total = len(blob)
    while offset + _AR_HEADER.size <= total:
        header = blob[offset:offset + _AR_HEADER.size]
        raw_name, _mtime, _uid, _gid, _mode, raw_size, magic = _AR_HEADER.unpack(header)
        if magic != _AR_MAGIC:
            raise AssertionError("ar 成员头魔数错误：%r" % (magic,))
        name = raw_name.decode("utf-8", "replace").strip()
        # GNU ar 可能在短名字末尾补一个 "/"。
        if name.endswith("/"):
            name = name[:-1]
        try:
            size = int(raw_size.decode("ascii").strip())
        except ValueError:
            raise AssertionError("ar 成员 %r 的 size 字段非法：%r" % (name, raw_size))
        start = offset + _AR_HEADER.size
        end = start + size
        if end > total:
            raise AssertionError("ar 成员 %r 的数据被截断（声明 %d 字节）" % (name, size))
        members.append((name, blob[start:end]))
        offset = end
        if offset % 2 == 1:  # ar 成员按 2 字节对齐
            offset += 1
    return members


def _extract_tar_gz(blob, label):
    """解压 tar.gz，返回 {归一化路径: (权限位, 内容字节)}，只保留普通文件。"""
    try:
        raw = gzip.decompress(blob)
    except OSError as exc:
        raise AssertionError("%s 不是有效的 gzip 数据：%s" % (label, exc))
    files = {}
    try:
        archive = tarfile.open(fileobj=io.BytesIO(raw), mode="r:")
    except tarfile.TarError as exc:
        raise AssertionError("%s 不是有效的 tar 归档：%s" % (label, exc))
    with archive:
        for member in archive.getmembers():
            name = _norm_member_name(member.name)
            if not name:
                continue
            if not member.isfile():
                continue
            handle = archive.extractfile(member)
            payload = handle.read() if handle is not None else b""
            files[name] = (member.mode & 0o777, payload)
    return files


def _parse_control_text(text):
    """解析 Debian control 段落，返回 {字段: 值}（续行折叠并保留缩进）。"""
    fields = {}
    current_key = None
    for line in text.splitlines():
        if not line.strip():
            continue
        if line[0] in " \t" and current_key is not None:
            fields[current_key] += "\n" + line
            continue
        if ":" not in line:
            raise AssertionError("control 格式错误，行内缺少冒号：%r" % line)
        key, value = line.split(":", 1)
        current_key = key.strip()
        fields[current_key] = value.strip()
    return fields


class PackageTestCase(unittest.TestCase):
    """对 dist 下 .deb 安装包做结构与一致性校验。"""

    @classmethod
    def setUpClass(cls):
        cls.deb_path = _find_deb()
        if cls.deb_path is None:
            raise unittest.SkipTest("尚未构建 .deb：请先运行 python3 tools/build_deb.py")
        cls.version = _read_version()
        if not cls.version:
            raise unittest.SkipTest("缺少仓库根目录 VERSION 文件，无法校验版本号")

        with open(cls.deb_path, "rb") as handle:
            cls.blob = handle.read()

        cls.members = _parse_ar(cls.blob)
        cls.member_names = [name for name, _ in cls.members]
        cls.member_map = {}
        for name, data in cls.members:
            cls.member_map[name] = data

        for required in ("control.tar.gz", "data.tar.gz"):
            if required not in cls.member_map:
                raise AssertionError(
                    "%s 缺少 ar 成员 %s（实际成员：%s）"
                    % (os.path.basename(cls.deb_path), required, cls.member_names)
                )

        cls.control_files = _extract_tar_gz(
            cls.member_map["control.tar.gz"], "control.tar.gz"
        )
        data_files = _extract_tar_gz(cls.member_map["data.tar.gz"], "data.tar.gz")
        # 安装包布局按 SPEC 10.2 以 usr/ 等为根；部分构建器会写成 "./usr/..."，
        # _norm_member_name 已统一去掉 "./" 前缀。
        cls.data_files = data_files

        control_raw = cls.control_files.get("control", (0o644, b""))[1]
        cls.control_text = control_raw.decode("utf-8", "replace")
        cls.control_fields = _parse_control_text(cls.control_text)

    # ------------------------------------------------------------------
    # 1) ar 成员顺序与内容
    # ------------------------------------------------------------------
    def test_ar_members_order_and_content(self):
        self.assertTrue(
            self.blob.startswith(_AR_GLOBAL_MAGIC),
            "deb 不是 ar 归档（缺少 !<arch> 全局头）",
        )
        self.assertEqual(
            self.member_names,
            EXPECTED_MEMBERS,
            "ar 成员应依次为 debian-binary、control.tar.gz、data.tar.gz",
        )
        self.assertEqual(
            self.member_map["debian-binary"],
            b"2.0\n",
            "debian-binary 内容必须是 2.0\\n",
        )
        for name in ("control.tar.gz", "data.tar.gz"):
            self.assertTrue(
                self.member_map[name].startswith(b"\x1f\x8b"),
                "%s 应为 gzip 压缩数据" % name,
            )
        self.assertTrue(
            os.path.basename(self.deb_path).startswith("uos-autostart-manager_"),
            "安装包文件名应以 uos-autostart-manager_ 开头：%s"
            % os.path.basename(self.deb_path),
        )

    # ------------------------------------------------------------------
    # 2) control 字段、架构、版本、依赖
    # ------------------------------------------------------------------
    def test_control_metadata(self):
        missing = [
            field for field in REQUIRED_CONTROL_FIELDS
            if field not in self.control_fields
        ]
        self.assertEqual(missing, [], "control 缺少字段：%s" % ", ".join(missing))

        self.assertEqual(self.control_fields["Package"], "uos-autostart-manager")
        self.assertEqual(self.control_fields["Architecture"], "all")
        self.assertEqual(
            self.control_fields["Version"],
            self.version,
            "control 中的 Version 必须与仓库根 VERSION 文件一致",
        )
        self.assertEqual(self.control_fields["Section"], "utils")
        self.assertEqual(self.control_fields["Priority"], "optional")
        self.assertIn(
            "uos-autostart@example.com",
            self.control_fields["Maintainer"],
            "Maintainer 应包含维护者邮箱",
        )

        depends = self.control_fields["Depends"]
        self.assertIn("python3", depends)
        self.assertIn(
            "python3-pyqt5",
            depends,
            "Depends 应提供 PyQt5 依赖",
        )
        self.assertIn(
            "python3-tk",
            depends,
            "Depends 应提供 tkinter 兜底依赖",
        )
        self.assertTrue(
            "{{VERSION}}" not in self.control_text
            and "{{INSTALLED_SIZE}}" not in self.control_text,
            "control 中仍有未替换的 {{...}} 占位符",
        )
        installed_size = self.control_fields["Installed-Size"]
        self.assertTrue(
            installed_size.isdigit(),
            "Installed-Size 应为纯数字，实际为：%r" % installed_size,
        )
        self.assertGreater(int(installed_size), 0, "Installed-Size 应大于 0")

        description = self.control_fields["Description"]
        lines = description.split("\n")
        self.assertEqual(
            lines[0], "统信 UOS 开机自启动管理器", "Description 首行不匹配 SPEC 10.1"
        )
        self.assertGreaterEqual(
            len(lines), 2, "Description 应包含续行（SPEC 10.1 要求两行续行）"
        )
        for line in lines[1:]:
            self.assertTrue(
                line.startswith(" "),
                "Description 续行必须以一个空格开头：%r" % line,
            )

    # ------------------------------------------------------------------
    # 3) data 包路径与权限
    # ------------------------------------------------------------------
    def test_data_package_layout(self):
        expected = list(EXPECTED_DATA_FILES) + [
            MODULE_DIR + module for module in EXPECTED_MODULES
        ]
        missing = [path for path in expected if path not in self.data_files]
        self.assertEqual(missing, [], "data 包缺少文件：%s" % ", ".join(missing))

        bin_mode, bin_content = self.data_files["usr/bin/uos-autostart-manager"]
        self.assertEqual(
            bin_mode, 0o755, "usr/bin/uos-autostart-manager 权限必须是 0755"
        )
        self.assertTrue(
            bin_content.startswith(b"#!/usr/bin/python3\n"),
            "启动器必须以 #!/usr/bin/python3 开头",
        )
        self.assertIn(b"uos_autostart.app", bin_content, "启动器应调用 uos_autostart.app")
        self.assertIn(b"sys.path.insert", bin_content, "启动器应把程序目录加入 sys.path")

        # SPEC 10.2 其余文件权限精确为 0644。
        for path in EXPECTED_DATA_FILES:
            if path == "usr/bin/uos-autostart-manager":
                continue
            mode, _content = self.data_files[path]
            self.assertEqual(mode, 0o644, "%s 权限必须是 0644" % path)

        # data 包内不应出现可疑路径（绝对路径 / 上级目录穿越）。
        for path in self.data_files:
            self.assertFalse(
                path.startswith("/") or ".." in path.split("/"),
                "data 包包含非法路径：%s" % path,
            )

    # ------------------------------------------------------------------
    # 3b) 桌面文件内容与启动器（由 packaging/ 输入决定）
    # ------------------------------------------------------------------
    def test_desktop_entries(self):
        app_desktop = self.data_files[
            "usr/share/applications/uos-autostart-manager.desktop"
        ][1].decode("utf-8", "replace")
        self.assertIn("[Desktop Entry]", app_desktop)
        self.assertIn("Name=统信 UOS 开机自启动管理器", app_desktop)
        self.assertIn("Exec=/usr/bin/uos-autostart-manager", app_desktop)
        self.assertIn("Icon=uos-autostart-manager", app_desktop)
        self.assertIn("Categories=Utility;System;", app_desktop)
        self.assertIn("Terminal=false", app_desktop)

        autostart_desktop = self.data_files[
            "etc/xdg/autostart/uos-autostart-manager.desktop"
        ][1].decode("utf-8", "replace")
        self.assertIn("Exec=/usr/bin/uos-autostart-manager --tray", autostart_desktop)
        self.assertIn("NoDisplay=true", autostart_desktop)
        self.assertIn("X-GNOME-Autostart-enabled=true", autostart_desktop)
        self.assertIn("X-UOS-Autostart-Manager=true", autostart_desktop)

    # ------------------------------------------------------------------
    # 4) uos_autostart/*.py 全部可被 ast.parse
    # ------------------------------------------------------------------
    def test_python_sources_parse(self):
        sources = sorted(
            path for path in self.data_files
            if path.startswith(MODULE_DIR) and path.endswith(".py")
        )
        self.assertTrue(sources, "data 包中没有任何 uos_autostart/*.py")
        for path in sources:
            _mode, payload = self.data_files[path]
            try:
                source = payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                self.fail("%s 不是 UTF-8 编码：%s" % (path, exc))
            try:
                # 同时约束 Python 3.7 兼容性（SPEC 0）；3.7 自身不支持该参数时退化。
                try:
                    ast.parse(source, filename=path, feature_version=(3, 7))
                except TypeError:
                    ast.parse(source, filename=path)
            except SyntaxError as exc:
                self.fail("%s 存在语法错误（需兼容 Python 3.7）：%s" % (path, exc))

    # ------------------------------------------------------------------
    # 5) md5sums 存在且与 data 文件一致
    # ------------------------------------------------------------------
    def test_md5sums_consistency(self):
        self.assertIn(
            "md5sums", self.control_files, "control.tar.gz 缺少 md5sums"
        )
        raw = self.control_files["md5sums"][1].decode("utf-8", "replace")
        entries = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            self.assertEqual(
                len(parts), 2, "md5sums 行格式错误（应为 <md5>  <路径>）：%r" % line
            )
            digest, path = parts[0], parts[1].strip()
            if path.startswith("*"):
                path = path[1:]
            entries[_norm_member_name(path)] = digest.lower()
        self.assertTrue(entries, "md5sums 为空")

        for path, digest in entries.items():
            self.assertIn(
                path,
                self.data_files,
                "md5sums 列出的文件不存在于 data 包：%s" % path,
            )
            actual = hashlib.md5(self.data_files[path][1]).hexdigest()
            self.assertEqual(
                actual, digest, "%s 的 md5 校验值不一致（md5sums 与实际文件不符）" % path
            )

        # 关键文件必须被 md5sums 覆盖（conffile 按 Debian 约定不参与 md5sums）。
        for path in EXPECTED_DATA_FILES + [
            MODULE_DIR + module for module in EXPECTED_MODULES
        ]:
            if path in CONFFILES:
                continue
            self.assertIn(path, entries, "md5sums 缺少文件：%s" % path)

    # ------------------------------------------------------------------
    # 6) 维护脚本随包安装（SPEC 10.4）
    # ------------------------------------------------------------------
    def test_maintainer_scripts(self):
        for script in ("postinst", "prerm", "postrm"):
            self.assertIn(
                script, self.control_files, "control.tar.gz 缺少维护脚本 %s" % script
            )
            mode, payload = self.control_files[script]
            self.assertTrue(mode & 0o111, "%s 必须可执行（0755）" % script)
            text = payload.decode("utf-8", "replace")
            self.assertTrue(
                text.startswith("#!/bin/sh"), "%s 必须以 #!/bin/sh 开头" % script
            )
            self.assertIn("set -e", text, "%s 必须包含 set -e" % script)
            self.assertIn("exit 0", text, "%s 必须以 exit 0 结束" % script)


if __name__ == "__main__":
    unittest.main(verbosity=2)
