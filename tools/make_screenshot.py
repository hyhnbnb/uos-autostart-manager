#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成界面截图（文档用）。需要 PyQt5 与离屏平台，可在无桌面环境运行。

用法：
    QT_QPA_PLATFORM=offscreen python3 tools/make_screenshot.py
产物：
    docs/main-window.png
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

WORK = tempfile.mkdtemp(prefix="uosam-shot-")
os.environ["HOME"] = WORK
os.environ["XDG_CONFIG_HOME"] = os.path.join(WORK, ".config")
os.environ["XDG_DATA_HOME"] = os.path.join(WORK, ".local", "share")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt" and os.path.isdir(r"C:\Windows\Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

SAMPLE = [
    ("网易云音乐", "/opt/netease/netease-cloud-music %U", "manager", 3, True, True, "音乐"),
    ("微信", "/opt/wechat/wechat", "manager", 5, True, True, "即时通讯"),
    ("输入法 fcitx5", "/usr/bin/fcitx5", "xdg", 0, True, True, "系统"),
    ("WPS 文字", "/usr/bin/wps %F", "manager", 8, True, False, "办公"),
    ("系统监视器", "/usr/bin/deepin-system-monitor", "manager", 0, False, False, "工具"),
]


def main():
    from uos_autostart import gui_qt, model, store

    st = store.ConfigStore()
    st.reset()
    for name, command, mode, delay, enabled, guarded, comment in SAMPLE:
        item = model.AutostartItem.new(name, command, mode=mode, delay=delay,
                                       comment=comment, icon="", watchdog=guarded)
        item.enabled = enabled
        st.add(item)
    cfg = st.load()
    cfg.manager_autostart = True
    cfg.start_minimized = True
    cfg.watchdog_enabled = True
    cfg.watchdog_interval_minutes = 20
    st.save(cfg)

    app = gui_qt.create_app([])
    window = gui_qt.MainWindow(st)
    window.resize(1024, 620)
    window.show()
    for _ in range(12):
        app.processEvents()
    # 截图中展示典型 UOS 用户路径，而不是构建机的临时目录
    window.store._path = "/home/uos/.config/uos-autostart-manager/config.json"
    window._update_status()
    # 选中第一行，展示按钮可用状态
    window._select_row(0)
    for _ in range(4):
        app.processEvents()

    out_dir = os.path.join(ROOT, "docs")
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    shot = window.grab()
    out_path = os.path.join(out_dir, "main-window.png")
    if not shot.save(out_path, "PNG"):
        sys.stderr.write("截图保存失败：%s\n" % out_path)
        return 1
    image = shot.toImage()
    colors = set()
    step = max(1, image.width() // 80)
    for x in range(0, image.width(), step):
        for y in range(0, image.height(), step):
            colors.add(image.pixel(x, y))
    window.quit_app()
    print("wrote %s（%d 字节，采样颜色 %d 种）"
          % (os.path.relpath(out_path, ROOT), os.path.getsize(out_path), len(colors)))
    if len(colors) < 8:
        sys.stderr.write("警告：截图像素过于单一，可能渲染为空\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
