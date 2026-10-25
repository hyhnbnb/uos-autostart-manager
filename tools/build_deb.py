#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纯 Python 构造 Debian 安装包（不依赖 dpkg-deb / ar / tar 命令）。

用法：
    python3 tools/build_deb.py              # 构建 deb + 源码包 + SHA256SUMS
    python3 tools/build_deb.py --no-portable
    python3 tools/build_deb.py --keep-stage
    python3 tools/build_deb.py --verify     # 构建后立即用 tools/verify_deb.py 校验

产物：
    dist/uos-autostart-manager_<version>_all.deb
    dist/uos-autostart-manager-<version>-src.tar.gz
    dist/SHA256SUMS
"""

import argparse
import gzip
import hashlib
import io
import os
import shutil
import sys
import tarfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
PKG = "uos-autostart-manager"
ARCH = "all"
PY_PKG = "uos_autostart"
INSTALL_LIB = "/usr/lib/%s" % PKG

SOURCE_DATE_EPOCH = int(os.environ.get("SOURCE_DATE_EPOCH", "1704067200"))
MODE_FILE = 0o644
MODE_EXEC = 0o755

REQUIRED_MODULES = (
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
)

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)

LAUNCHER_TEMPLATE = """#!/usr/bin/python3
# -*- coding: utf-8 -*-
# 由 uos-autostart-manager 安装包生成的启动器，请勿手工修改。
import os
import sys

sys.path.insert(0, "{libdir}")

from uos_autostart.app import main

if __name__ == "__main__":
    sys.exit(main())
"""

CHANGELOG_TEMPLATE = """uos-autostart-manager ({version}) unstable; urgency=medium

  * 初始版本：图形化开机自启动管理，支持从已安装应用中选择或自定义命令，
    可设置启动延迟与顺序，管理器自身开机自启并驻留系统托盘。
  * 提供 .deb 安装包，适用于统信 UOS / Debian 系发行版。

 -- UOS Autostart Manager <uos-autostart@example.com>  {date}
"""


class BuildError(Exception):
    pass


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def rel(path):
    return os.path.relpath(path, ROOT)


def read_bytes(path, text=True):
    """读取文件；text=True 时统一换行到 LF，避免 CRLF 进入安装包。"""
    if not os.path.isfile(path):
        raise BuildError("缺少文件：%s" % rel(path))
    with open(path, "rb") as fh:
        data = fh.read()
    if text:
        data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return data


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def gzip_bytes(data):
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as gz:
        gz.write(data)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 包内文件树
# ---------------------------------------------------------------------------

class PackageTree(object):
    """arcname（以 ./ 开头） -> (bytes, mode)"""

    def __init__(self):
        self.files = []

    def add_file(self, arcname, data, mode=MODE_FILE):
        if not arcname.startswith("./"):
            raise BuildError("arcname 必须以 ./ 开头：%s" % arcname)
        self.files.append((arcname, data, mode))

    def add_tree(self):
        """返回 (所有目录集合, 文件列表)，目录含 ./ 与每个父目录。"""
        dirs = {"./"}
        for arcname, _data, _mode in self.files:
            parts = arcname[2:].split("/")
            for i in range(1, len(parts)):
                dirs.add("./" + "/".join(parts[:i]) + "/")
        return dirs, self.files

    def total_size(self):
        return sum(len(data) for _n, data, _m in self.files)


def build_tar_gz(tree):
    dirs, files = tree.add_tree()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name in ["./"] + sorted(d for d in dirs if d != "./"):
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            info.mode = MODE_EXEC
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = SOURCE_DATE_EPOCH
            tar.addfile(info)
        for name, data, mode in sorted(files, key=lambda item: item[0]):
            info = tarfile.TarInfo(name)
            info.type = tarfile.REGTYPE
            info.mode = mode
            info.size = len(data)
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = SOURCE_DATE_EPOCH
            tar.addfile(info, io.BytesIO(data))
    return gzip_bytes(buf.getvalue())


def write_ar(path, members):
    """members: [(name, data, mode)]，按 GNU ar / POSIX 格式写入。"""
    out = io.BytesIO()
    out.write(b"!<arch>\n")
    for name, data, mode in members:
        if len(name) > 16:
            raise BuildError("ar 成员名过长：%s" % name)
        header = "%-16s%-12d%-6d%-6d%-8o%-10d`\n" % (
            name + "/",
            SOURCE_DATE_EPOCH,
            0,
            0,
            0o100000 | mode,
            len(data),
        )
        if len(header) != 60:
            raise BuildError("ar 头长度异常：%d" % len(header))
        out.write(header.encode("ascii"))
        out.write(data)
        if len(data) % 2:
            out.write(b"\n")
    with open(path, "wb") as fh:
        fh.write(out.getvalue())


# ---------------------------------------------------------------------------
# 构建数据包
# ---------------------------------------------------------------------------

def load_version():
    text = read_bytes(os.path.join(ROOT, "VERSION"), text=True).decode("utf-8").strip()
    if not text:
        raise BuildError("VERSION 文件为空")
    return text


def check_sources():
    problems = []
    pkg_dir = os.path.join(ROOT, "src", PY_PKG)
    for mod in REQUIRED_MODULES:
        if not os.path.isfile(os.path.join(pkg_dir, mod)):
            problems.append("缺少模块 src/%s/%s" % (PY_PKG, mod))
    for path in (
        os.path.join(ROOT, "packaging", "control"),
        os.path.join(ROOT, "packaging", "postinst"),
        os.path.join(ROOT, "packaging", "prerm"),
        os.path.join(ROOT, "packaging", "postrm"),
        os.path.join(ROOT, "packaging", "uos-autostart-manager.desktop"),
        os.path.join(ROOT, "packaging", "uos-autostart-manager-autostart.desktop"),
        os.path.join(ROOT, "packaging", "copyright"),
        os.path.join(ROOT, "README.md"),
        os.path.join(ROOT, "assets", "icon.svg"),
    ):
        if not os.path.isfile(path):
            problems.append("缺少文件 %s" % rel(path))
    for size in ICON_SIZES:
        icon = os.path.join(ROOT, "assets", "icon-%d.png" % size)
        if not os.path.isfile(icon):
            problems.append("缺少图标 %s（先运行 python tools/make_icons.py）" % rel(icon))
    if problems:
        raise BuildError("源文件不完整：\n  - " + "\n  - ".join(problems))


def check_python_sources():
    """语法检查 + Python 3.7 兼容性检查（ast feature_version）。"""
    import ast

    pkg_dir = os.path.join(ROOT, "src", PY_PKG)
    errors = []
    for mod in REQUIRED_MODULES:
        path = os.path.join(pkg_dir, mod)
        source = read_bytes(path, text=True).decode("utf-8")
        try:
            ast.parse(source, filename=path, feature_version=(3, 7))
        except SyntaxError as exc:
            errors.append("%s: 第 %s 行语法错误（需兼容 Python 3.7）：%s"
                          % (rel(path), exc.lineno, exc.msg))
    if errors:
        raise BuildError("Python 源码检查失败：\n  - " + "\n  - ".join(errors))


def build_data_tree(version):
    tree = PackageTree()
    pkg_dir = os.path.join(ROOT, "src", PY_PKG)

    # Python 包
    for mod in REQUIRED_MODULES:
        data = read_bytes(os.path.join(pkg_dir, mod), text=True)
        tree.add_file("./usr/lib/%s/%s/%s" % (PKG, PY_PKG, mod), data, MODE_FILE)

    # 启动器
    tree.add_file(
        "./usr/bin/%s" % PKG,
        (LAUNCHER_TEMPLATE.format(libdir=INSTALL_LIB)).encode("utf-8"),
        MODE_EXEC,
    )

    # 桌面入口
    tree.add_file(
        "./usr/share/applications/%s.desktop" % PKG,
        read_bytes(os.path.join(ROOT, "packaging", "%s.desktop" % PKG)),
        MODE_FILE,
    )
    tree.add_file(
        "./etc/xdg/autostart/%s.desktop" % PKG,
        read_bytes(os.path.join(ROOT, "packaging", "%s-autostart.desktop" % PKG)),
        MODE_FILE,
    )

    # 图标
    tree.add_file(
        "./usr/share/icons/hicolor/scalable/apps/%s.svg" % PKG,
        read_bytes(os.path.join(ROOT, "assets", "icon.svg")),
        MODE_FILE,
    )
    for size in ICON_SIZES:
        tree.add_file(
            "./usr/share/icons/hicolor/%dx%d/apps/%s.png" % (size, size, PKG),
            read_bytes(os.path.join(ROOT, "assets", "icon-%d.png" % size), text=False),
            MODE_FILE,
        )

    # 文档
    tree.add_file(
        "./usr/share/doc/%s/README.md" % PKG,
        read_bytes(os.path.join(ROOT, "README.md")),
        MODE_FILE,
    )
    shot = os.path.join(ROOT, "docs", "main-window.png")
    if os.path.isfile(shot):
        tree.add_file(
            "./usr/share/doc/%s/docs/main-window.png" % PKG,
            read_bytes(shot, text=False),
            MODE_FILE,
        )
    tree.add_file(
        "./usr/share/doc/%s/copyright" % PKG,
        read_bytes(os.path.join(ROOT, "packaging", "copyright")),
        MODE_FILE,
    )
    changelog = CHANGELOG_TEMPLATE.format(
        version=version,
        date=time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime(SOURCE_DATE_EPOCH)),
    ).encode("utf-8")
    tree.add_file(
        "./usr/share/doc/%s/changelog.gz" % PKG,
        gzip_bytes(changelog),
        MODE_FILE,
    )
    return tree


def build_control_tree(version, data_tree, installed_size):
    tree = PackageTree()
    control = read_bytes(os.path.join(ROOT, "packaging", "control")).decode("utf-8")
    control = control.replace("{{VERSION}}", version)
    control = control.replace("{{INSTALLED_SIZE}}", str(installed_size))
    if "{{" in control:
        raise BuildError("packaging/control 仍有未替换的占位符")
    if not control.endswith("\n"):
        control += "\n"
    tree.add_file("./control", control.encode("utf-8"), MODE_FILE)

    # md5sums：conffiles 不参与校验和
    conffiles = {"./etc/xdg/autostart/%s.desktop" % PKG}
    lines = []
    for arcname, data, _mode in sorted(data_tree.files, key=lambda item: item[0]):
        if arcname in conffiles:
            continue
        lines.append("%s  %s" % (hashlib.md5(data).hexdigest(), arcname[2:]))
    tree.add_file("./md5sums", ("\n".join(lines) + "\n").encode("utf-8"), MODE_FILE)

    # conffiles
    tree.add_file(
        "./conffiles",
        ("/etc/xdg/autostart/%s.desktop\n" % PKG).encode("utf-8"),
        MODE_FILE,
    )

    # 维护脚本
    for script in ("postinst", "prerm", "postrm"):
        tree.add_file(
            "./%s" % script,
            read_bytes(os.path.join(ROOT, "packaging", script)),
            MODE_EXEC,
        )
    return tree


# ---------------------------------------------------------------------------
# 源码包（可移植）
# ---------------------------------------------------------------------------

def build_portable(version, out_path):
    include_dirs = ("src", "packaging", "scripts", "tools", "assets", "tests", "docs")
    include_files = ("VERSION", "README.md", "SPEC.md", ".gitignore")
    prefix = "%s-%s/" % (PKG, version)
    entries = []
    for name in include_files:
        path = os.path.join(ROOT, name)
        if os.path.isfile(path):
            entries.append((prefix + name, read_bytes(path, text=False), MODE_FILE))
    for dirname in include_dirs:
        base = os.path.join(ROOT, dirname)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames.sort()
            for filename in sorted(filenames):
                if filename.endswith(".pyc") or "__pycache__" in dirpath:
                    continue
                path = os.path.join(dirpath, filename)
                arcname = prefix + os.path.relpath(path, ROOT).replace(os.sep, "/")
                mode = MODE_EXEC if filename.endswith(".sh") else MODE_FILE
                entries.append((arcname, read_bytes(path, text=False), mode))

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for arcname, data, mode in sorted(entries, key=lambda item: item[0]):
            info = tarfile.TarInfo(arcname)
            info.size = len(data)
            info.mode = mode
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = SOURCE_DATE_EPOCH
            tar.addfile(info, io.BytesIO(data))
    with open(out_path, "wb") as fh:
        fh.write(gzip_bytes(buf.getvalue()))
    return out_path


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="构建 uos-autostart-manager 的 .deb 安装包")
    parser.add_argument("--output", default=os.path.join(ROOT, "dist"), help="输出目录（默认 dist/）")
    parser.add_argument("--no-portable", action="store_true", help="不生成源码压缩包")
    parser.add_argument("--keep-stage", action="store_true", help="保留 build/ 中间产物")
    parser.add_argument("--verify", action="store_true", help="构建后立即校验 .deb")
    args = parser.parse_args(argv)

    version = load_version()
    check_sources()
    check_python_sources()

    out_dir = os.path.abspath(args.output)
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    stage = os.path.join(ROOT, "build", "stage")
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    os.makedirs(stage)

    data_tree = build_data_tree(version)
    installed_size = max(1, int((data_tree.total_size() + 1023) // 1024))
    control_tree = build_control_tree(version, data_tree, installed_size)

    data_gz = build_tar_gz(data_tree)
    control_gz = build_tar_gz(control_tree)

    deb_path = os.path.join(out_dir, "%s_%s_%s.deb" % (PKG, version, ARCH))
    write_ar(
        deb_path,
        [
            ("debian-binary", b"2.0\n", MODE_FILE),
            ("control.tar.gz", control_gz, MODE_FILE),
            ("data.tar.gz", data_gz, MODE_FILE),
        ],
    )

    artifacts = [deb_path]
    if not args.no_portable:
        src_path = os.path.join(out_dir, "%s-%s-src.tar.gz" % (PKG, version))
        build_portable(version, src_path)
        artifacts.append(src_path)

    sums_path = os.path.join(out_dir, "SHA256SUMS")
    with open(sums_path, "w", encoding="utf-8", newline="\n") as fh:
        for path in artifacts:
            fh.write("%s  %s\n" % (sha256_file(path), os.path.basename(path)))
    artifacts.append(sums_path)

    print("=" * 72)
    print("%s %s (%s)" % (PKG, version, ARCH))
    print("=" * 72)
    print("数据文件 %d 个，Installed-Size %d KiB" % (len(data_tree.files), installed_size))
    print("data.tar.gz    %8d 字节" % len(data_gz))
    print("control.tar.gz %8d 字节" % len(control_gz))
    for path in artifacts:
        print("%-52s %9d 字节  sha256 %s" % (
            os.path.relpath(path, ROOT), os.path.getsize(path), sha256_file(path)[:16]))
    print("=" * 72)

    if args.verify:
        sys.path.insert(0, TOOLS)
        import verify_deb  # noqa: E402  (延迟导入，避免循环)
        ok, messages = verify_deb.verify(deb_path, expected_version=version)
        for line in messages:
            print(line)
        if not ok:
            print("校验失败")
            return 1
        print("校验通过")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BuildError as exc:
        sys.stderr.write("构建失败：%s\n" % exc)
        sys.exit(1)
