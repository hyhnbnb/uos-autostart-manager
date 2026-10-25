#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立校验 .deb 安装包结构（纯标准库，不需要 dpkg / ar / tar 命令）。

用法：
    python3 tools/verify_deb.py                       # 校验 dist/ 下最新的 deb
    python3 tools/verify_deb.py dist/xxx.deb
    python3 tools/verify_deb.py --version 1.1.0
"""

import argparse
import ast
import glob
import hashlib
import io
import os
import sys
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = "uos-autostart-manager"
PY_PKG = "uos_autostart"
ARCH = "all"
PY_MODULES = (
    "__init__.py", "paths.py", "model.py", "store.py", "desktop.py",
    "autostart.py", "launcher.py", "processes.py", "watchdog.py", "cli.py",
    "app.py", "gui_qt.py", "gui_tk.py", "__main__.py",
)
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
REQUIRED_FIELDS = (
    "Package", "Version", "Section", "Priority", "Architecture",
    "Maintainer", "Depends", "Installed-Size", "Description",
)


def parse_ar(data):
    if not data.startswith(b"!<arch>\n"):
        raise ValueError("不是 ar 归档（缺少 !<arch> 魔数）")
    members = []
    pos = 8
    while pos < len(data):
        header = data[pos:pos + 60]
        if len(header) < 60:
            raise ValueError("ar 头被截断")
        name = header[0:16].decode("ascii", "replace").strip()
        if name.endswith("/"):
            name = name[:-1]
        try:
            size = int(header[48:58].decode("ascii").strip())
        except ValueError:
            raise ValueError("ar 头 size 字段非法：%r" % header[48:58])
        payload = data[pos + 60:pos + 60 + size]
        if len(payload) != size:
            raise ValueError("ar 成员 %s 数据被截断" % name)
        members.append((name, payload))
        pos += 60 + size
        if pos % 2:
            pos += 1
    return members


def read_tar_gz(data):
    entries = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            name = member.name
            payload = b""
            if member.isfile():
                fh = tar.extractfile(member)
                if fh is not None:
                    payload = fh.read()
            entries[name.lstrip("./")] = (member, payload)
    return entries


def parse_control(text):
    fields = {}
    current = None
    for line in text.splitlines():
        if not line:
            continue
        if line[0] in " \t":
            if current is None:
                raise ValueError("control 文件以续行开头")
            fields[current] += "\n" + line
            continue
        if ":" not in line:
            raise ValueError("control 行缺少冒号：%r" % line)
        key, value = line.split(":", 1)
        current = key.strip()
        fields[current] = value.strip()
    return fields


def _norm(name):
    return name.lstrip("./")


def verify(deb_path, expected_version=None, verbose=True):
    checks = []

    def check(ok, message, detail=""):
        checks.append((bool(ok), message, detail))

    def fail(message):
        checks.append((False, message, ""))

    def info(message):
        checks.append((True, message, ""))

    # ---------- ar 容器 ----------
    with open(deb_path, "rb") as fh:
        raw = fh.read()
    try:
        members = dict(parse_ar(raw))
    except ValueError as exc:
        return False, ["[FAIL] ar 结构解析失败：%s" % exc]
    check(list(members.keys()) == ["debian-binary", "control.tar.gz", "data.tar.gz"],
          "ar 成员顺序为 debian-binary / control.tar.gz / data.tar.gz",
          ",".join(members.keys()))
    check(members.get("debian-binary") == b"2.0\n", "debian-binary 内容为 \"2.0\\n\"")

    # ---------- control ----------
    try:
        ctl_entries = read_tar_gz(members["control.tar.gz"])
    except Exception as exc:  # noqa: BLE001
        return False, ["[FAIL] control.tar.gz 解压失败：%s" % exc]
    for name in ("control", "md5sums", "postinst", "prerm", "postrm"):
        check(name in ctl_entries, "control 包含 %s" % name)
    for script in ("postinst", "prerm", "postrm"):
        if script in ctl_entries:
            member, payload = ctl_entries[script]
            check(member.mode & 0o111 != 0, "%s 具有可执行权限（0o%o）" % (script, member.mode))
            check(payload.startswith(b"#!/bin/sh"), "%s 使用 #!/bin/sh" % script)
    control_text = ctl_entries.get("control", (None, b""))[1].decode("utf-8", "replace")
    try:
        fields = parse_control(control_text)
    except ValueError as exc:
        return False, ["[FAIL] control 解析失败：%s" % exc]
    for name in REQUIRED_FIELDS:
        check(name in fields, "control 字段 %s 存在" % name)
    check(fields.get("Package") == PKG, "Package = %s" % PKG, fields.get("Package"))
    check(fields.get("Architecture") == ARCH, "Architecture = %s" % ARCH, fields.get("Architecture"))
    version = fields.get("Version", "")
    check(bool(version), "Version 非空", version)
    if expected_version:
        check(version == expected_version, "Version 与 VERSION 文件一致（%s）" % version, version)
    depends = fields.get("Depends", "")
    check("python3" in depends, "Depends 含 python3", depends)
    check("python3-pyqt5" in depends and "python3-tk" in depends,
          "Depends 同时提供 PyQt5 与 tkinter 兜底", depends)
    check("python3 (>= 3.7)" in depends, "Depends 要求 python3 >= 3.7", depends)
    desc_lines = control_text.splitlines()
    desc_idx = next((i for i, line in enumerate(desc_lines) if line.startswith("Description:")), -1)
    check(desc_idx >= 0 and desc_idx + 1 < len(desc_lines) and desc_lines[desc_idx + 1].startswith(" "),
          "Description 续行以空格开头")

    # ---------- data ----------
    try:
        data_entries = read_tar_gz(members["data.tar.gz"])
    except Exception as exc:  # noqa: BLE001
        return False, ["[FAIL] data.tar.gz 解压失败：%s" % exc]
    check("./" in data_entries or "" in data_entries, "data 包包含 ./ 根目录条目")

    required = [
        "usr/bin/%s" % PKG,
        "usr/share/applications/%s.desktop" % PKG,
        "etc/xdg/autostart/%s.desktop" % PKG,
        "usr/share/icons/hicolor/scalable/apps/%s.svg" % PKG,
        "usr/share/doc/%s/README.md" % PKG,
        "usr/share/doc/%s/copyright" % PKG,
        "usr/share/doc/%s/changelog.gz" % PKG,
    ]
    required += ["usr/lib/%s/%s/%s" % (PKG, PY_PKG, mod) for mod in PY_MODULES]
    required += ["usr/share/icons/hicolor/%dx%d/apps/%s.png" % (s, s, PKG) for s in ICON_SIZES]
    for name in required:
        check(name in data_entries, "data 包含 %s" % name)

    bad = [n for n in data_entries if "__pycache__" in n or n.endswith(".pyc") or ".venv" in n]
    check(not bad, "data 包无 __pycache__ / .pyc 等垃圾文件", ",".join(bad[:5]))

    for name, (member, _payload) in data_entries.items():
        check(member.uid == 0 and member.gid == 0, "成员 %s 属主为 root(0:0)" % name)
        break  # 只需抽查第一个非 ./ 条目
    nonroot = [n for n, (m, _p) in data_entries.items() if m.uid != 0 or m.gid != 0]
    check(not nonroot, "所有成员 uid/gid 为 0", ",".join(nonroot[:5]))

    launcher = data_entries.get("usr/bin/%s" % PKG)
    if launcher:
        member, payload = launcher
        check(member.mode & 0o111 != 0, "启动器可执行（0o%o）" % member.mode)
        text = payload.decode("utf-8", "replace")
        check(text.startswith("#!/usr/bin/python3"), "启动器 shebang 为 /usr/bin/python3")
        check('sys.path.insert(0, "/usr/lib/%s")' % PKG in text, "启动器注入安装库路径")
        check("from uos_autostart.app import main" in text, "启动器导入 uos_autostart.app.main")

    for mod in PY_MODULES:
        entry = data_entries.get("usr/lib/%s/%s/%s" % (PKG, PY_PKG, mod))
        if not entry:
            continue
        member, payload = entry
        check(member.mode == 0o644, "%s 权限为 0644" % mod, "0o%o" % member.mode)
        text = payload.decode("utf-8", "replace")
        try:
            try:
                ast.parse(text, filename=mod, feature_version=(3, 7))
            except TypeError:  # Python 3.7 自身不支持 feature_version
                ast.parse(text, filename=mod)
        except SyntaxError as exc:
            fail("%s 语法错误：第 %s 行 %s" % (mod, exc.lineno, exc.msg))

    app_desktop = data_entries.get("usr/share/applications/%s.desktop" % PKG)
    if app_desktop:
        text = app_desktop[1].decode("utf-8", "replace")
        for token in ("[Desktop Entry]", "Type=Application", "Name=", "Exec=/usr/bin/%s" % PKG,
                      "Icon=%s" % PKG, "Categories=", "Terminal=false"):
            check(token in text, "desktop 启动器包含 %s" % token)

    auto_desktop = data_entries.get("etc/xdg/autostart/%s.desktop" % PKG)
    if auto_desktop:
        text = auto_desktop[1].decode("utf-8", "replace")
        for token in ("[Desktop Entry]", "Exec=/usr/bin/%s --tray" % PKG,
                      "X-GNOME-Autostart-enabled=true", "NoDisplay=true"):
            check(token in text, "自启动 desktop 包含 %s" % token)

    svg = data_entries.get("usr/share/icons/hicolor/scalable/apps/%s.svg" % PKG)
    if svg:
        check(svg[1].lstrip().startswith(b"<?xml"), "SVG 图标内容合法")
    png_ok = True
    for size in ICON_SIZES:
        entry = data_entries.get("usr/share/icons/hicolor/%dx%d/apps/%s.png" % (size, size, PKG))
        if entry and not entry[1].startswith(b"\x89PNG\r\n\x1a\n"):
            png_ok = False
    check(png_ok, "全部 PNG 图标文件头合法")

    readme = data_entries.get("usr/share/doc/%s/README.md" % PKG)
    check(bool(readme) and len(readme[1]) > 500, "README.md 已安装且内容非空")
    shot = data_entries.get("usr/share/doc/%s/docs/main-window.png" % PKG)
    check(bool(shot) and shot[1].startswith(b"\x89PNG"),
          "界面截图已安装且为合法 PNG")
    changelog = data_entries.get("usr/share/doc/%s/changelog.gz" % PKG)
    if changelog:
        check(changelog[1][:2] == b"\x1f\x8b", "changelog.gz 为合法 gzip")

    # ---------- md5sums ----------
    md5_raw = ctl_entries.get("md5sums", (None, b""))[1].decode("utf-8", "replace")
    declared = {}
    for line in md5_raw.splitlines():
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            fail("md5sums 行格式非法：%r" % line)
            continue
        declared[parts[1].strip()] = parts[0].strip()
    conffiles_raw = ctl_entries.get("conffiles", (None, b""))[1].decode("utf-8", "replace")
    conffiles = {line.strip() for line in conffiles_raw.splitlines() if line.strip()}
    check("/etc/xdg/autostart/%s.desktop" % PKG in conffiles,
          "conffiles 标记了 /etc/xdg/autostart 配置")

    expected_md5 = {}
    for name, (member, payload) in data_entries.items():
        if member.isdir():
            continue
        if "/" + name in conffiles:
            continue
        expected_md5[name] = hashlib.md5(payload).hexdigest()
    missing = sorted(set(expected_md5) - set(declared))
    check(not missing, "md5sums 覆盖所有非 conffile 文件", ",".join(missing[:5]))
    mismatch = [name for name, digest in expected_md5.items()
                if name in declared and declared[name] != digest]
    check(not mismatch, "md5sums 与实际文件一致", ",".join(mismatch[:5]))

    # ---------- Installed-Size ----------
    total = sum(len(payload) for _n, (member, payload) in data_entries.items()
                if member.isfile())
    try:
        declared_size = int(fields.get("Installed-Size", "0"))
    except ValueError:
        declared_size = -1
    expected_size = max(1, (total + 1023) // 1024)
    check(declared_size == expected_size,
          "Installed-Size 正确（%d KiB）" % expected_size, "实际 %s" % declared_size)

    # ---------- 文件名 ----------
    base = os.path.basename(deb_path)
    check(base == "%s_%s_%s.deb" % (PKG, version, ARCH),
          "文件名符合 <包名>_<版本>_<架构>.deb", base)

    ok = all(item[0] for item in checks)
    if verbose:
        lines = []
        for passed, message, detail in checks:
            line = ("[PASS] " if passed else "[FAIL] ") + message
            if detail and not passed:
                line += "  -> %s" % detail
            lines.append(line)
        failed = sum(1 for item in checks if not item[0])
        lines.append("-" * 72)
        lines.append("共 %d 项检查，通过 %d 项，失败 %d 项" % (len(checks), len(checks) - failed, failed))
        lines.append("结果：%s" % ("通过" if ok else "失败"))
        return ok, lines
    return ok, []


def find_default_deb():
    candidates = sorted(glob.glob(os.path.join(ROOT, "dist", "*.deb")),
                        key=os.path.getmtime, reverse=True)
    if not candidates:
        raise SystemExit("dist/ 下没有 .deb，请先运行 python3 tools/build_deb.py")
    return candidates[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description="校验 .deb 安装包结构（纯标准库）")
    parser.add_argument("deb", nargs="?", help="待校验的 .deb 路径（默认取 dist/ 下最新）")
    parser.add_argument("--version", dest="expected_version", default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    path = args.deb or find_default_deb()
    expected = args.expected_version
    if expected is None:
        version_file = os.path.join(ROOT, "VERSION")
        if os.path.isfile(version_file):
            with open(version_file, "r", encoding="utf-8") as fh:
                expected = fh.read().strip()
    ok, messages = verify(path, expected_version=expected, verbose=not args.quiet)
    if not args.quiet:
        print("校验 %s" % path)
        for line in messages:
            print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
