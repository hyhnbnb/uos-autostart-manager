# -*- coding: utf-8 -*-
"""统信 UOS 开机自启动管理器 —— 核心库。

本包只包含仅依赖标准库的核心模块：``paths`` / ``model`` / ``store`` /
``desktop`` / ``autostart`` / ``launcher`` / ``processes`` / ``watchdog``。
图形界面（gui_qt / gui_tk）与命令行入口（app）由上层按需延迟导入，
因此无 GUI 环境下也能正常使用核心功能。

本模块刻意保持轻量：只暴露版本号，不导入任何子模块，避免副作用。
"""

__version__ = "1.1.0"
