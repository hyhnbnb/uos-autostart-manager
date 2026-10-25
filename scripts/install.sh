#!/bin/sh
# uos-autostart-manager 便携安装脚本（基于源码树，不依赖 dpkg）。
#
# 用法：
#   sudo ./scripts/install.sh                     # 安装到默认前缀 /usr
#   sudo ./scripts/install.sh --prefix /usr/local # 安装到指定前缀
#   sudo ./scripts/install.sh --uninstall         # 卸载已安装文件
#
# 默认安装布局（PREFIX=/usr）：
#   /usr/bin/uos-autostart-manager
#   /usr/lib/uos-autostart-manager/uos_autostart/*.py
#   /usr/share/applications/uos-autostart-manager.desktop
#   /etc/xdg/autostart/uos-autostart-manager.desktop
#   /usr/share/icons/hicolor/{scalable,16x16,...,256x256}/apps/uos-autostart-manager.*
#
# 卸载只删除本脚本安装的系统文件，用户配置与日志（~/.config、~/.local/share）保留。

set -u
umask 022

APP_ID="uos-autostart-manager"
APP_NAME="统信 UOS 开机自启动管理器"
PREFIX="/usr"
ACTION="install"
AUTOSTART_DIR="${UOS_AM_AUTOSTART_DIR:-/etc/xdg/autostart}"

usage() {
    cat <<'EOF'
用法：
  sudo ./scripts/install.sh [--prefix /usr]
  sudo ./scripts/install.sh --uninstall [--prefix /usr]

说明：
  从源码树安装/卸载「统信 UOS 开机自启动管理器」，不依赖 dpkg。
  默认安装前缀为 /usr；安装到系统目录需要 root 权限，请使用 sudo。

选项：
  --prefix DIR     安装前缀，默认 /usr
  --uninstall, -u  卸载本脚本安装的文件（保留用户配置与日志）
  -h, --help       显示本帮助

示例：
  sudo ./scripts/install.sh
  sudo ./scripts/install.sh --prefix /usr/local
  sudo ./scripts/install.sh --uninstall

提示：
  安装到用户目录（无需 root）时可指定自启动目录，例如：
    UOS_AM_AUTOSTART_DIR="$HOME/.config/autostart" \
      ./scripts/install.sh --prefix "$HOME/.local"
  卸载时请使用与安装时相同的 --prefix 与 UOS_AM_AUTOSTART_DIR。
EOF
}

die() {
    printf '错误：%s\n' "$*" >&2
    exit 1
}

info() {
    printf '%s\n' "$*"
}

# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------
while [ "$#" -gt 0 ]; do
    case "$1" in
        --prefix)
            [ "$#" -ge 2 ] || die "--prefix 缺少参数值（例如：--prefix /usr）。"
            PREFIX="$2"
            shift 2
            ;;
        --prefix=*)
            PREFIX="${1#*=}"
            shift
            ;;
        --uninstall|-u)
            ACTION="uninstall"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf '错误：未知参数：%s\n\n' "$1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

# 前缀必须是绝对路径；去掉结尾多余的斜杠（根目录 / 除外）。
case "$PREFIX" in
    /*) ;;
    *) die "安装前缀必须是绝对路径（例如 /usr 或 /usr/local），当前值为：$PREFIX" ;;
esac
while [ "$PREFIX" != "/" ] && [ "${PREFIX%/}" != "$PREFIX" ]; do
    PREFIX="${PREFIX%/}"
done

BIN_DIR="$PREFIX/bin"
LIB_DIR="$PREFIX/lib/$APP_ID"
PKG_DIR="$LIB_DIR/uos_autostart"
DESKTOP_DIR="$PREFIX/share/applications"
ICON_ROOT="$PREFIX/share/icons/hicolor"
BIN_PATH="$BIN_DIR/$APP_ID"

# 源码树根目录 = 脚本所在目录的上一级。
SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd) || die "无法定位脚本所在目录。"
SOURCE_ROOT=$(CDPATH= cd "$SCRIPT_DIR/.." && pwd) || die "无法定位源码树根目录。"
SOURCE_PKG="$SOURCE_ROOT/src/uos_autostart"
SOURCE_PACKAGING="$SOURCE_ROOT/packaging"
SOURCE_ASSETS="$SOURCE_ROOT/assets"

MODULES="__init__ paths model store desktop autostart launcher processes watchdog cli app gui_qt gui_tk __main__"
ICON_SIZES="16 24 32 48 64 128 256"

# ---------------------------------------------------------------------------
# 前置检查
# ---------------------------------------------------------------------------
check_root_permission() {
    if [ "$(id -u)" -ne 0 ]; then
        case "$PREFIX" in
            /usr|/usr/local|/etc|/etc/*|/opt|/opt/*)
                die "写入 $PREFIX 需要 root 权限，请使用 sudo 重新运行本脚本。"
                ;;
        esac
    fi
}

check_sources() {
    if [ ! -d "$SOURCE_ROOT/src" ]; then
        die "当前目录不像完整的源码树：找不到 $SOURCE_ROOT/src（请从仓库根目录运行 scripts/install.sh）。"
    fi
    if [ ! -d "$SOURCE_PKG" ]; then
        die "源码不完整：缺少目录 $SOURCE_PKG。"
    fi
    for module in $MODULES; do
        if [ ! -f "$SOURCE_PKG/$module.py" ]; then
            die "源码不完整：缺少文件 $SOURCE_PKG/$module.py。"
        fi
    done
    for desktop in "$APP_ID.desktop" "$APP_ID-autostart.desktop"; do
        if [ ! -f "$SOURCE_PACKAGING/$desktop" ]; then
            die "打包文件不完整：缺少文件 $SOURCE_PACKAGING/$desktop。"
        fi
    done
    if [ ! -f "$SOURCE_ASSETS/icon.svg" ]; then
        die "图标不完整：缺少文件 $SOURCE_ASSETS/icon.svg。"
    fi
    if ! ls "$SOURCE_ASSETS"/icon-*.png >/dev/null 2>&1; then
        die "图标不完整：$SOURCE_ASSETS 下没有 icon-*.png 位图。"
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        die "未找到 python3，请先安装 Python 3（UOS：sudo apt install python3）。"
    fi
}

# ---------------------------------------------------------------------------
# 安装步骤
# ---------------------------------------------------------------------------
install_python_package() {
    mkdir -p "$LIB_DIR" || die "无法创建目录 $LIB_DIR（请使用 sudo）。"
    rm -rf "$PKG_DIR"
    mkdir -p "$PKG_DIR" || die "无法创建目录 $PKG_DIR（请使用 sudo）。"
    for source_file in "$SOURCE_PKG"/*.py; do
        if [ ! -f "$source_file" ]; then
            die "源码不完整：$SOURCE_PKG 下没有可安装的 .py 文件。"
        fi
        cp -f "$source_file" "$PKG_DIR/${source_file##*/}" \
            || die "复制文件失败：${source_file##*/}（请检查权限）。"
    done
    chmod 0644 "$PKG_DIR"/*.py || die "设置文件权限失败：$PKG_DIR/*.py。"
    # 清掉可能残留的旧字节码，避免升级后加载过期模块。
    rm -rf "$PKG_DIR/__pycache__"
    # 应用包在 $PREFIX/lib 下，需要它位于启动器写入的 sys.path 中。
    chmod 0755 "$LIB_DIR" "$PKG_DIR" 2>/dev/null || true
}

install_launcher() {
    mkdir -p "$BIN_DIR" || die "无法创建目录 $BIN_DIR（请使用 sudo）。"
    cat > "$BIN_PATH" <<EOF || die "写入启动器失败：$BIN_PATH（请检查权限）。"
#!/usr/bin/python3
# -*- coding: utf-8 -*-
# 本文件由 uos-autostart-manager 的 scripts/install.sh 生成，请勿手动修改。
import os
import sys

sys.path.insert(0, "$LIB_DIR")

from uos_autostart.app import main

if __name__ == "__main__":
    sys.exit(main())
EOF
    chmod 0755 "$BIN_PATH" || die "设置启动器可执行权限失败：$BIN_PATH。"
}

install_desktop_files() {
    mkdir -p "$DESKTOP_DIR" || die "无法创建目录 $DESKTOP_DIR（请使用 sudo）。"
    escaped_prefix=$(printf '%s' "$PREFIX" | sed -e 's/[&|]/\\&/g')
    for desktop in "$APP_ID.desktop" "$APP_ID-autostart.desktop"; do
        sed -e "s|/usr/bin/$APP_ID|$escaped_prefix/bin/$APP_ID|g" \
            "$SOURCE_PACKAGING/$desktop" > "$DESKTOP_DIR/$desktop" \
            || die "安装桌面文件失败：$SOURCE_PACKAGING/$desktop。"
        chmod 0644 "$DESKTOP_DIR/$desktop" \
            || die "设置桌面文件权限失败：$DESKTOP_DIR/$desktop。"
    done
    mkdir -p "$AUTOSTART_DIR" || die "无法创建目录 $AUTOSTART_DIR（如为系统目录请使用 sudo）。"
    cp -f "$DESKTOP_DIR/$APP_ID-autostart.desktop" "$AUTOSTART_DIR/$APP_ID.desktop" \
        || die "安装自启动文件失败：$AUTOSTART_DIR/$APP_ID.desktop（请检查权限）。"
    chmod 0644 "$AUTOSTART_DIR/$APP_ID.desktop" \
        || die "设置自启动文件权限失败：$AUTOSTART_DIR/$APP_ID.desktop。"
}

install_icons() {
    mkdir -p "$ICON_ROOT/scalable/apps" || die "无法创建图标目录 $ICON_ROOT/scalable/apps（请使用 sudo）。"
    cp -f "$SOURCE_ASSETS/icon.svg" "$ICON_ROOT/scalable/apps/$APP_ID.svg" \
        || die "安装矢量图标失败：$SOURCE_ASSETS/icon.svg。"
    chmod 0644 "$ICON_ROOT/scalable/apps/$APP_ID.svg" || die "设置图标权限失败。"
    for size in $ICON_SIZES; do
        source_icon="$SOURCE_ASSETS/icon-$size.png"
        if [ -f "$source_icon" ]; then
            mkdir -p "$ICON_ROOT/${size}x${size}/apps" \
                || die "无法创建图标目录 $ICON_ROOT/${size}x${size}/apps（请使用 sudo）。"
            cp -f "$source_icon" "$ICON_ROOT/${size}x${size}/apps/$APP_ID.png" \
                || die "安装图标失败：$source_icon。"
            chmod 0644 "$ICON_ROOT/${size}x${size}/apps/$APP_ID.png" \
                || die "设置图标权限失败：$ICON_ROOT/${size}x${size}/apps/$APP_ID.png。"
        fi
    done
}

refresh_caches() {
    # 缺少对应工具时静默跳过，始终返回成功（幂等）。
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q "$DESKTOP_DIR" 2>/dev/null || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q -t -f "$ICON_ROOT" 2>/dev/null || true
    fi
    return 0
}

# ---------------------------------------------------------------------------
# 安装 / 卸载
# ---------------------------------------------------------------------------
do_install() {
    check_sources
    check_root_permission
    info "==> 正在安装 $APP_NAME（前缀：$PREFIX）..."
    install_python_package
    install_launcher
    install_desktop_files
    install_icons
    refresh_caches
    info ""
    info "安装完成。"
    info "  启动器：    $BIN_PATH"
    info "  程序目录：  $LIB_DIR"
    info "  桌面入口：  $DESKTOP_DIR/$APP_ID.desktop"
    info "  自启动项：  $AUTOSTART_DIR/$APP_ID.desktop"
    info "  图标目录：  $ICON_ROOT"
    info ""
    info "可从应用菜单启动，或执行：$BIN_PATH"
    info "环境诊断：$BIN_PATH --doctor"
    info "卸载：sudo ./scripts/install.sh --uninstall${PREFIX:+ --prefix $PREFIX}"
}

do_uninstall() {
    check_root_permission
    info "==> 正在卸载 $APP_NAME（前缀：$PREFIX）..."
    rm -f "$BIN_PATH" || die "无法删除启动器：$BIN_PATH（请使用 sudo）。"
    rm -rf "$LIB_DIR" || die "无法删除程序目录：$LIB_DIR（请使用 sudo）。"
    rm -f "$DESKTOP_DIR/$APP_ID.desktop" || true
    rm -f "$AUTOSTART_DIR/$APP_ID.desktop" || true
    rm -f "$ICON_ROOT/scalable/apps/$APP_ID.svg" || true
    for size in $ICON_SIZES; do
        rm -f "$ICON_ROOT/${size}x${size}/apps/$APP_ID.png" || true
    done
    refresh_caches
    info ""
    info "卸载完成。用户配置与日志已保留："
    info "  配置：~/.config/$APP_ID/config.json"
    info "  日志：~/.local/share/$APP_ID/logs/manager.log"
    info "如需彻底清除，请手动删除上述目录。"
}

case "$ACTION" in
    install)
        do_install
        ;;
    uninstall)
        do_uninstall
        ;;
    *)
        die "未知操作：$ACTION"
        ;;
esac

exit 0
