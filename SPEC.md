# UOS 自启动管理器 —— 内部实现规范（SPEC v1.0）

本文件是本次开发的唯一契约。所有实现必须严格遵守下面的模块边界、函数签名、数据结构与打包布局。
文件名、路径、JSON 字段名、函数名都不可自行更改；确需扩展时只能在"新增"方向扩展，不得改变既有语义。

目标产品：一个可在统信 UOS（及 Debian 系 Linux）上运行的「开机自启动管理器」。
- 自身可开机自启动（以 `--tray` 方式驻留托盘）
- 可管理任意第三方应用的开机自启动（从已安装应用列表选择，或自定义命令）
- 提供图形界面（PyQt5 优先，tkinter 兜底）
- 提供 .deb 安装包（纯 Python 构造，无需 dpkg 工具链）

---

## 0. 通用约束（所有人必须遵守）

1. **Python 兼容性**：代码必须兼容 **Python 3.7**（UOS 20 自带 3.7.5）。
   - 禁止：`match` 语句、`X | Y` 类型联合、`list[str]`/`dict[str, int]` 内置泛型注解、`str.removeprefix`、`functools.cache`、`zoneinfo`。
   - 允许：`dataclasses`、`typing`、f-string、`pathlib`、`subprocess.run`、`os.replace`。
2. **依赖**：核心模块只用标准库；GUI 模块才允许 `PyQt5` / `tkinter`。
   - **核心模块顶层禁止 import PyQt5 / tkinter**（否则单元测试在无 GUI 环境下失败）。
3. **编码**：所有文件 UTF-8（无 BOM），LF 换行。面向用户的文字一律简体中文。
4. **安全**：启动外部程序只能 `subprocess.Popen(argv, shell=False)`，禁止拼接 shell 字符串执行。
5. **禁止执行 shell 命令**（当前环境 shell 不稳定，Lead 会统一跑测试）。只写代码 + 自查。
6. **文件归属**：只允许创建/修改自己任务里列出的文件；不要创建 `__pycache__`、不要动别人的文件、不要新建计划外文件。
7. **XSS/注入式输入**：写入 .desktop 的所有值必须先经 `_sanitize_value()` 去掉换行（`\r`/`\n` → 空格）并去掉首尾空白。

### 目录结构（最终仓库）

```
uos-autostart-manager/
├── VERSION                        # 内容：1.1.0（Lead 写）
├── SPEC.md                        # 本文件（Lead 写）
├── README.md                      # 中文使用/安装文档（packaging-docs 写）
├── assets/                        # icon.svg / icon-16..256.png（Lead 生成）
├── src/uos_autostart/             # Python 包
│   ├── __init__.py                # core-engine
│   ├── paths.py                   # core-engine
│   ├── model.py                   # core-engine
│   ├── store.py                   # core-engine
│   ├── desktop.py                 # core-engine
│   ├── autostart.py               # core-engine
│   ├── launcher.py                # core-engine
│   ├── processes.py               # 进程探测（v1.1.0 新增，core-engine）
│   ├── watchdog.py                # 掉线自动重启（v1.1.0 新增，core-engine）
│   ├── cli.py                     # gui-app
│   ├── app.py                     # gui-app
│   ├── gui_qt.py                  # gui-app
│   ├── gui_tk.py                  # gui-app
│   └── __main__.py                # gui-app
├── tests/
│   ├── test_core.py               # core-engine
│   └── test_package.py            # packaging-docs
├── packaging/
│   ├── control                    # packaging-docs（含 {{VERSION}} {{INSTALLED_SIZE}} 占位符）
│   ├── postinst / prerm / postrm  # packaging-docs
│   ├── uos-autostart-manager.desktop          # 桌面启动器（packaging-docs）
│   ├── uos-autostart-manager-autostart.desktop # /etc/xdg/autostart 用（packaging-docs）
│   └── copyright                  # packaging-docs
├── scripts/install.sh             # 便携安装脚本（packaging-docs）
├── tools/
│   ├── build_deb.py               # Lead
│   ├── verify_deb.py              # Lead
│   └── make_icons.py              # Lead
└── dist/                          # 构建产物（Lead）
```

---

## 1. 应用标识常量

以下常量定义在 `paths.py`，其他模块一律 `from .paths import ...`：

| 常量 | 值 |
| --- | --- |
| `APP_ID` | `"uos-autostart-manager"` |
| `APP_NAME` | `"统信 UOS 开机自启动管理器"` |
| `APP_SHORT` | `"UOS 自启动管理器"` |
| `VERSION` | `"1.1.0"` |
| `MAINTAINER` | `"UOS Autostart Manager <uos-autostart@example.com>"` |

---

## 2. `paths.py`（core-engine）

全部返回绝对路径字符串；不做 I/O 副作用（`ensure_dirs()` 除外）。
`home()` 必须按顺序取：环境变量 `HOME` → `USERPROFILE` → `os.path.expanduser("~")`（便于 Windows 上跑单元测试）。

```python
APP_ID: str
APP_NAME: str
APP_SHORT: str
VERSION: str
MAINTAINER: str

def home() -> str
def config_dir() -> str          # $XDG_CONFIG_HOME 或 home()/.config，再拼 APP_ID
def config_path() -> str         # config_dir()/config.json
def data_dir() -> str            # $XDG_DATA_HOME 或 home()/.local/share，再拼 APP_ID
def log_dir() -> str             # data_dir()/logs
def log_path() -> str            # log_dir()/manager.log
def state_dir() -> str           # data_dir()/state（锁文件等）
def autostart_dir() -> str       # ($XDG_CONFIG_HOME 或 home()/.config)/autostart
def applications_dirs() -> List[str]   # 已存在才返回，顺序：~/.local/share/applications、$XDG_DATA_DIRS 各项/applications、/usr/local/share/applications、/usr/share/applications；去重
def system_autostart_path() -> str     # "/etc/xdg/autostart/uos-autostart-manager.desktop"
def manager_bin_path() -> str          # "/usr/bin/uos-autostart-manager"
def is_installed() -> bool             # os.path.isfile(manager_bin_path())
def assets_dir() -> Optional[str]      # 包上级目录下的 assets/（源码运行时存在）
def icon_paths() -> List[str]          # 已存在的图标候选（源码 assets 优先，其次 /usr/share/icons/hicolor/*/apps/uos-autostart-manager.png|svg）
def icon_path() -> Optional[str]       # 第一个存在的候选，否则 None
def icon_name() -> str                 # "uos-autostart-manager"
def manager_launch_command() -> List[str]
    # 安装后：["/usr/bin/uos-autostart-manager", "--tray"]
    # 源码运行：["env", "PYTHONPATH=<src 根目录>", sys.executable, "-m", "uos_autostart", "--tray"]
def module_root() -> str               # src/ 目录（源码运行时）或 /usr/lib/uos-autostart-manager
def ensure_dirs() -> None              # 创建 config_dir/data_dir/log_dir/state_dir/autostart_dir
def now_iso() -> str                   # 本地时间 ISO8601，秒级，如 2026-10-24T20:40:34
```

---

## 3. `model.py`（core-engine）

### 3.1 AutostartItem（dataclass）

字段（顺序与默认值固定）：

```python
id: str                 # 12 位十六进制，secrets.token_hex(6)
name: str               # 显示名，非空
command: str            # 完整命令行（程序 + 参数，已去掉 %U 等占位符），非空
workdir: str = ""
icon: str = ""          # 图标名或绝对路径
comment: str = ""
enabled: bool = True
watchdog: bool = False  # v1.1.0：掉线自动重启（守护）开关
delay: int = 0          # 0..600 秒，启动前额外等待
mode: str = "manager"   # "manager"=由管理器启动；"xdg"=写系统 XDG 自启动文件，由桌面环境直接启动
terminal: bool = False
created_at: str = ""    # paths.now_iso()
last_run_at: str = ""   # 最近一次由管理器启动的时间，可为空
run_count: int = 0
```

方法：

```python
@classmethod
def new(cls, name: str, command: str, **kwargs) -> "AutostartItem"
    # 生成 id / created_at / delay 归一化，忽略未知 kwargs
def to_dict(self) -> Dict[str, Any]
@classmethod
def from_dict(cls, data: Dict[str, Any]) -> "AutostartItem"
    # 容错：缺字段用默认值；delay 转 int 并 clamp 到 0..600；mode 非法→"manager"；
    # enabled/terminal 用 bool()；run_count 转 int；id 为空则重新生成
def validate(self) -> List[str]
    # 返回中文错误信息列表：name 为空 / command 为空 / delay 越界 / mode 非法
def display_mode(self) -> str          # "管理器启动" / "系统直接启动"
def display_delay(self) -> str         # "立即" 或 "延迟 5 秒"
def display_watchdog(self) -> str      # "开启" / "关闭"（v1.1.0）
```

### 3.2 Config（dataclass）

```python
version: int = 1
manager_autostart: bool = True      # 管理器自身是否开机自启
start_minimized: bool = False       # 开机自启时是否最小化到托盘
close_to_tray: bool = True          # 关闭主窗口时最小化到托盘而不是退出
global_delay: int = 0               # 启动全部应用前的全局等待秒数 0..600
watchdog_enabled: bool = True       # v1.1.0：掉线自动重启全局开关
watchdog_interval_minutes: int = 20 # v1.1.0：守护检测间隔，1..1440 分钟
items: List[AutostartItem] = field(default_factory=list)

def to_dict(self) -> Dict[str, Any]         # 顶层 version 字段=1
@classmethod
def from_dict(cls, data: Dict[str, Any]) -> "Config"   # 容错，items 逐项 from_dict
def find(self, item_id: str) -> Optional[AutostartItem]
def enabled_items(self) -> List[AutostartItem]
def watchdog_items(self) -> List[AutostartItem]   # v1.1.0：enabled 且 watchdog 为真
def index_of(self, item_id: str) -> int      # 找不到返回 -1
def remove_item(self, item_id: str) -> bool
def move_item(self, item_id: str, delta: int) -> bool   # delta=-1/+1，返回是否发生变化
```

---

## 4. `store.py`（core-engine）

```python
class ConfigStore:
    def __init__(self, path: Optional[str] = None)   # 默认 paths.config_path()
    @property
    def path(self) -> str
    def load(self) -> Config
        # 文件不存在 → Config()（不写盘）；JSON 损坏 → 把坏文件改名为
        # "<path>.corrupt.<时间戳>"，写日志并返回 Config()
    def save(self, cfg: Config) -> None
        # 原子写：同目录下 "<path>.tmp.<pid>" → os.replace；父目录自动创建；权限 0o600（失败忽略）
    def load_items(self) -> List[AutostartItem]
    def get(self, item_id: str) -> Optional[AutostartItem]
    def add(self, item: AutostartItem) -> AutostartItem       # 追加到末尾并保存
    def update(self, item: AutostartItem) -> bool            # 按 id 替换并保存
    def remove(self, item_id: str) -> bool
    def move(self, item_id: str, delta: int) -> bool
    def set_option(self, key: str, value: Any) -> Config
        # 仅允许 manager_autostart/start_minimized/close_to_tray/global_delay
    def set_manager_autostart(self, enabled: bool) -> Config
    def reset(self) -> Config
```
内部用 `threading.RLock` 保护读写（GUI 线程与启动线程会并发访问）。
保存时同时调用 `autostart.sync(cfg)` 吗？**不要**。sync 由调用方（app/GUI）显式执行，保持 store 只做持久化（避免循环导入）。

---

## 5. `desktop.py`（core-engine）

### 5.1 .desktop 解析与生成

```python
@dataclass
class AppEntry:
    desktop_id: str      # 如 "firefox.desktop"
    name: str
    command: str         # 已去掉 %U 等字段码的完整命令行
    icon: str = ""
    comment: str = ""
    path: str = ""       # .desktop 文件绝对路径
    terminal: bool = False
    categories: str = ""
    def display_label(self) -> str      # "名称 — 备注"（无备注则仅名称）

def parse_desktop_file(path: str) -> Dict[str, str]
    # 只取 [Desktop Entry] 组；忽略注释行/空行；返回 {key: value}；文件不存在或读失败返回 {}

def strip_field_codes(command: str) -> str
    # 去掉 %f %F %u %U %i %c %k %d %D %n %N %v %m %%，并压缩多余空格、去首尾空白

def split_command(command: str) -> List[str]
    # shlex.split(posix=True)；解析异常时退化为 str.split()

def quote_exec_arg(arg: str) -> str
    # 按 Desktop Entry 规范：含空白或特殊字符时用双引号包裹，
    # 并转义 " ` $ \ 为 \\ 形式；不含特殊字符原样返回

def exec_line(argv: List[str]) -> str       # " ".join(quote_exec_arg(a) for a in argv)

def looks_like_executable(name: str) -> bool  # 含 os.sep 或存在于 PATH

def sanitize_value(value: str) -> str
    # 去掉 \r \n（替换为空格）、去掉首尾空白、内部连续空白压缩为单空格（用于 .desktop 值）
```

### 5.2 生成文件内容（必须逐字符匹配，测试会断言）

**单个应用条目（xdg 模式）** `build_item_desktop(item: AutostartItem) -> str`：

```
[Desktop Entry]
Type=Application
Version=1.0
Name=<sanitize(name)>
Comment=<sanitize(comment)>            # comment 为空时写 "由 UOS 自启动管理器托管"
Exec=<exec_line(split_command(item.command))>
Icon=<sanitize(icon)>                  # icon 为空时整行省略
Path=<sanitize(workdir)>               # workdir 为空时整行省略
Terminal=true|false
Hidden=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=<delay>
X-UOS-Autostart-Manager=true
```
行序如上；`Exec` 为空（命令非法）时 `build_item_desktop` 返回 `""`。

**管理器自启动条目** `build_manager_desktop(command: List[str], hidden: bool) -> str`：

```
[Desktop Entry]
Type=Application
Version=1.0
Name=统信 UOS 开机自启动管理器
Comment=管理开机自启动应用
Exec=<exec_line(command)>
Icon=uos-autostart-manager
Terminal=false
Hidden=true|false
X-GNOME-Autostart-enabled=true|false
X-UOS-Autostart-Manager=true
```

### 5.3 应用发现

```python
def discover_applications(extra_dirs: Optional[List[str]] = None) -> List[AppEntry]
    # 目录顺序：extra_dirs 优先，然后 paths.applications_dirs()
    # 递归一层 *.desktop（子目录也要，如 kde4）
    # 过滤：Type != Application、NoDisplay=true、Hidden=true、Exec 为空、
    #       只有 TryExec 且命令不存在？不过滤（保留）
    # 去重：同名 desktop_id 只保留先出现的（用户目录优先）
    # command = strip_field_codes(Exec)；无 Exec 但 TryExec 存在则用 TryExec
    # 结果按 name 排序（不区分大小写的 str.lower 排序）
```
同时提供 `def desktop_file_name(item_id: str) -> str: return "uos-am-%s.desktop" % item_id`。

---

## 6. `autostart.py`（core-engine）

```python
MANAGER_DESKTOP_FILE: str           # "uos-autostart-manager.desktop"
ITEM_PREFIX: str                    # "uos-am-"

def manager_override_path() -> str  # paths.autostart_dir()/MANAGER_DESKTOP_FILE（用户级覆盖文件）
def item_file_path(item_id: str) -> str              # paths.autostart_dir()/desktop_file_name(item_id)
def write_item_file(item: AutostartItem) -> str      # 写文件（0600→0644 均可），返回路径；命令非法返回 ""
def remove_item_file(item_id: str) -> bool
def sync(cfg: Config, dry_run: bool = False) -> Dict[str, Any]
    # 1) 对 enabled 且 mode=="xdg" 的项写/更新 item 文件
    # 2) 删除 autostart_dir 下所有 "uos-am-*.desktop" 中不再需要的（项被删/被禁用/改成 manager 模式）
    # 3) 绝不触碰 MANAGER_DESKTOP_FILE
    # 4) 返回 {"written": List[str], "removed": List[str], "errors": List[str]}
def set_manager_autostart(enabled: bool) -> bool
    # 总是写用户级覆盖文件：enabled→Hidden=false 完整条目；disabled→Hidden=true 条目
    # Exec 使用 paths.manager_launch_command()
def manager_autostart_enabled() -> bool
    # 用户覆盖文件存在→解析 Hidden（缺失视为 False）；不存在→检查 paths.system_autostart_path() 是否存在
def audit(cfg: Config) -> List[Dict[str, str]]
    # 每项 {"level": "ok"|"warn"|"error", "message": "..."}，用于 doctor / GUI 状态栏
    # 检查：管理器自启状态、每个 item 命令是否可执行、xdg 项文件是否存在且 Enabled
```
所有写盘操作要 `os.makedirs(..., exist_ok=True)`，异常捕获并写入 `errors`，不得抛出到 GUI。

---

## 7. `launcher.py`（core-engine）

```python
def setup_logging(log_file: Optional[str] = None, level: str = "INFO") -> logging.Logger
    # 默认写 paths.log_path()；RotatingFileHandler(maxBytes=1MB, backupCount=2)；
    # 重复调用不重复添加 handler；失败时退化为 StreamHandler
def get_logger() -> logging.Logger
def command_available(command: str) -> Tuple[bool, str]
    # 返回 (是否可用, 解析到的路径 或 错误说明)
class Launcher:
    def __init__(self, store: ConfigStore, dry_run: bool = False,
                 sleep_func: Callable[[float], None] = time.sleep,
                 logger: Optional[logging.Logger] = None)
    def start_item(self, item: AutostartItem) -> bool
        # split_command；空→False；Popen(argv, cwd=workdir or None,
        #   stdin=DEVNULL, stdout=日志文件或DEVNULL, stderr=STDOUT,
        #   start_new_session=True, close_fds=True)
        # 成功后更新 item.last_run_at/run_count 并 store.update(item)
        # dry_run=True 时只记日志并返回 True
    def start_all(self, cfg: Optional[Config] = None, include_xdg: bool = True,
                  delay_scale: float = 1.0) -> Dict[str, Any]
        # 先等 cfg.global_delay*delay_scale，再按 items 顺序逐项：
        #   sleep(item.delay*delay_scale) → start_item（mode=="xdg" 且 include_xdg=False 时跳过并计入 skipped）
        # 返回 {"started": [id...], "failed": [id...], "skipped": [id...]}
    def start_all_async(self, cfg: Optional[Config] = None, include_xdg: bool = False,
                        delay_scale: float = 1.0) -> threading.Thread
        # 后台线程执行 start_all，daemon=True，返回线程对象
```

启动时环境：继承 `os.environ`，并确保 `DISPLAY` / `WAYLAND_DISPLAY` / `XAUTHORITY` 已存在时原样传递。

---

## 8. `cli.py` / `app.py`（gui-app）

### 8.1 命令行（`app.main`）

```
uos-autostart-manager                     # 打开图形界面
uos-autostart-manager --tray              # 托盘模式（开机自启使用），启动托管应用后驻留托盘
uos-autostart-manager --start-all         # 无界面启动全部启用项（含 xdg 模式）
uos-autostart-manager --list              # 文本列出配置项
uos-autostart-manager --doctor            # 输出环境诊断
uos-autostart-manager --version
uos-autostart-manager --config PATH       # 指定配置文件（测试用）
uos-autostart-manager --no-sync           # 跳过 xdg 文件同步
uos-autostart-manager --dry-run           # 只打印将要启动什么
```

`def main(argv: Optional[List[str]] = None) -> int` 行为：
1. 解析参数（argparse），`--version` 打印 `APP_NAME VERSION` 返回 0。
2. `store = ConfigStore(args.config)`；`cfg = store.load()`；`ensure_dirs()`。
3. 非 `--no-sync` 时 `autostart.sync(cfg)`（只告警不中断）。
4. `--list` / `--doctor` / `--start-all` 走 `cli.py` 的文本实现并直接返回（不加载 GUI）。
5. GUI 模式：单实例锁（`paths.state_dir()/manager.lock`，fcntl 可用则锁，Windows 用 msvcrt 兜底，失败则允许继续）；
   已被占用时打印"已有实例在运行"返回 0。
6. `--tray` 或 `cfg.start_minimized` 时：先在后台线程 `Launcher.start_all_async(include_xdg=False)`，
   再启动 GUI（`options={"tray": True, "minimized": args.tray or cfg.start_minimized}`）。
7. 后端选择：`import gui_qt` 成功且 `gui_qt.available()` → 用它；否则 `gui_tk`；都不可用 → 打印安装提示返回 2。
8. `KeyboardInterrupt` → 返回 0。

### 8.2 `cli.py`

```python
def run_list(store: ConfigStore) -> int      # 打印 "序号 状态 名称 | 方式 | 延迟 | 命令"
def run_doctor(store: ConfigStore) -> int    # 环境信息 + backend 可用性 + autostart.audit() 结果
def run_start_all(store: ConfigStore, dry_run: bool = False) -> int
```

### 8.3 单实例

`app.py` 内部实现 `class SingleInstance: __enter__/__exit__`，锁文件 `paths.state_dir()/manager.lock`；
获取失败返回 False，不抛异常。

---

## 9. 图形界面（gui-app）

统一约定：两个后端必须暴露同名入口，且 **`MainWindow` 可在不进入事件循环的情况下构造并调用 `refresh()`**（Lead 会用 offscreen 平台做冒烟测试）。

```python
# gui_qt.py
def available() -> bool                       # PyQt5 是否可 import
def create_app(argv: Optional[List[str]] = None) -> "QApplication"
def create_icon() -> "QIcon"                  # 有图标文件用图标，否则用 QPixmap 画一个
class MainWindow(QMainWindow):
    def __init__(self, store: ConfigStore, options: Optional[Dict[str, Any]] = None)
    def refresh(self) -> None                 # 从 store 重载并刷新表格/状态栏
    def start_selected(self) -> None
    def start_all(self) -> None
    ...
class TrayIcon(QSystemTrayIcon)               # 菜单：显示主窗口/立即启动全部/打开配置目录/退出
def main(store: ConfigStore, options: Dict[str, Any]) -> int

# gui_tk.py
def available() -> bool
def main(store: ConfigStore, options: Dict[str, Any]) -> int
class MainWindow: ...                          # 属性 self.tree / self.status_text 供测试
```

### 9.1 主窗口布局（两后端功能对齐）

- 标题：`统信 UOS 开机自启动管理器`
- 顶部工具栏按钮：`添加应用`、`添加命令`、`编辑`、`删除`、`上移`、`下移`、`全部启动`、`设置`
- 表格列：`启用`（勾选框）| `名称` | `启动方式` | `延迟` | `命令` | `最近启动`
  - 勾选框切换 → 立即 `store.update()` + `autostart.sync()`；`mode=="manager"` 的项禁用/启用只影响管理器行为
  - 双击行 → 编辑；选中行变化 → 启用/禁用编辑、删除、上移、下移按钮
- 状态栏：`共 N 项，已启用 M 项 · 配置：<config_path>`；右下角显示当前后端（`Qt` / `Tk`）
- 菜单：`文件`（打开配置目录、退出）、`帮助`（环境诊断、关于）
- 关闭窗口：`cfg.close_to_tray` 为真且托盘可用 → 隐藏窗口并提示一次；否则退出
- 首次运行（items 为空）：表格上方显示提示条"点击「添加应用」从已安装程序中选择"；不自动添加任何项

### 9.2 添加/编辑对话框

- `添加应用`：对话框列出 `desktop.discover_applications()` 结果（QListWidget/QTreeWidget + 搜索框 + "仅显示可执行"过滤）；
  选中后填入命令、名称、图标、备注，进入可编辑的条目表单确认。
- `添加命令`：条目表单。
- 条目表单字段：`名称`（必填）、`命令`（必填，带"浏览"文件按钮）、`参数`（可选，拼接在命令后）、`工作目录`（可选，带浏览）、
  `图标`（可选）、`启动延迟`（0–600 秒 SpinBox/QSpinBox）、`启动方式`（下拉：管理器启动 / 系统直接启动）、
  `在终端中运行`（勾选）、`备注`。
- 保存前调用 `item.validate()`，错误弹窗提示；命令含 `%U/%F` 时自动 `strip_field_codes`。
- `设置` 对话框：`管理器开机自启动`、`开机时最小化到托盘`、`关闭窗口时最小化到托盘`、`全部启动前等待（秒）`、
  `打开配置目录` 按钮、`立即启用/停用管理器自启` 即时生效（调用 `autostart.set_manager_autostart`）。

### 9.3 托盘

- 图标：`create_icon()`；提示文字 `统信 UOS 开机自启动管理器`
- 菜单：`显示主窗口`、`立即启动全部应用`、`打开配置目录`、`退出`
- 双击图标显示主窗口；无系统托盘时（`QSystemTrayIcon.isSystemTrayAvailable()` 为假）静默降级为普通窗口。

### 9.4 Tk 兜底

功能子集但必须可用：列表、添加命令、添加应用（简易列表）、编辑、删除、上移/下移、启用勾选、全部启动、设置（简化）、退出。
不得因缺少 `tkinter` 导致 `import uos_autostart.app` 失败（延迟 import）。

---

## 10. 打包（packaging-docs + Lead）

### 10.1 Debian 包元数据（`packaging/control`）

```
Package: uos-autostart-manager
Version: {{VERSION}}
Section: utils
Priority: optional
Architecture: all
Maintainer: UOS Autostart Manager <uos-autostart@example.com>
Depends: python3 (>= 3.7), python3-pyqt5 | python3-tk
Recommends: libnotify-bin
Installed-Size: {{INSTALLED_SIZE}}
Homepage: https://example.com/uos-autostart-manager
Description: 统信 UOS 开机自启动管理器
 图形化管理 Linux 桌面开机自启动应用：从已安装应用中选择、自定义命令、
 设置启动延迟与顺序、管理器自身开机自启并驻留托盘。
```
（描述续行必须以一个空格开头。）

### 10.2 安装布局（data.tar.gz 根，权限精确）

| 路径 | 权限 |
| --- | --- |
| `./usr/bin/uos-autostart-manager` | 0755 |
| `./usr/lib/uos-autostart-manager/uos_autostart/*.py` | 0644 |
| `./usr/share/applications/uos-autostart-manager.desktop` | 0644 |
| `./etc/xdg/autostart/uos-autostart-manager.desktop` | 0644 |
| `./usr/share/icons/hicolor/scalable/apps/uos-autostart-manager.svg` | 0644 |
| `./usr/share/icons/hicolor/{16x16,24x24,32x32,48x48,64x64,128x128,256x256}/apps/uos-autostart-manager.png` | 0644 |
| `./usr/share/doc/uos-autostart-manager/README.md` | 0644 |
| `./usr/share/doc/uos-autostart-manager/copyright` | 0644 |
| `./usr/share/doc/uos-autostart-manager/changelog.gz` | 0644 |

`./usr/bin/uos-autostart-manager` 内容（由 build 脚本生成，模板如下）：

```python
#!/usr/bin/python3
# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, "/usr/lib/uos-autostart-manager")

from uos_autostart.app import main

if __name__ == "__main__":
    sys.exit(main())
```

### 10.3 桌面文件

`packaging/uos-autostart-manager.desktop`（/usr/share/applications）：
```
[Desktop Entry]
Type=Application
Version=1.0
Name=统信 UOS 开机自启动管理器
Name[en_US]=UOS Autostart Manager
GenericName=开机自启动管理
Comment=管理开机自启动的应用，支持延迟启动与托盘常驻
Exec=/usr/bin/uos-autostart-manager
Icon=uos-autostart-manager
Terminal=false
Categories=Utility;System;
Keywords=autostart;startup;boot;自启动;开机;
StartupNotify=true
```

`packaging/uos-autostart-manager-autostart.desktop`（/etc/xdg/autostart）：
```
[Desktop Entry]
Type=Application
Version=1.0
Name=统信 UOS 开机自启动管理器
Comment=开机自动启动已配置的应用
Exec=/usr/bin/uos-autostart-manager --tray
Icon=uos-autostart-manager
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-UOS-Autostart-Manager=true
```

### 10.4 维护脚本

- `postinst`：`update-desktop-database -q /usr/share/applications || true`；`gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor 2>/dev/null || true`；`set -e`；首参 `configure` 才执行。
- `prerm`：`set -e`，无操作（可选提示）。
- `postrm`：purge 时删除 `~/.config/uos-autostart-manager` 不做（属用户数据），仅清理 desktop 缓存。
- 均为 `#!/bin/sh` 且可执行（0755）。

### 10.5 构建脚本（Lead 实现，供参考契约）

`python3 tools/build_deb.py` → `dist/uos-autostart-manager_1.1.0_all.deb`（纯标准库：ar + tar.gz）。
打包脚本会读取 `packaging/` 与 `src/`，按 10.2 布局生成；`Architecture: all`（跨 UOS x86_64/arm64/loongarch 通用）。

---

## 11. 测试要求

### 11.1 `tests/test_core.py`（core-engine 写，Lead 跑）

`unittest`，任何人可直接 `python -m unittest tests.test_core -v` 运行；**不依赖 GUI**；
每个用例必须把环境变量 `HOME` 指向 `tempfile.mkdtemp()`，并清理；不得写用户真实目录。

必须覆盖：
1. model：`new()` 生成合法 id、`to_dict/from_dict` 往返、容错（缺字段/脏数据）、`delay` clamp、`validate()`
2. store：增删改查/移动/重载/原子保存/损坏 JSON 自动备份恢复
3. desktop：`parse_desktop_file` 读 fixture、`strip_field_codes`、`quote_exec_arg`/`exec_line`、`build_item_desktop` 关键行断言、`sanitize_value`
4. `discover_applications`：用临时目录构造 2 个 .desktop（含 NoDisplay 过滤、同 id 去重）
5. autostart：xdg 项写文件/禁用删除/模式改为 manager 后删除旧文件/不动管理器文件；`set_manager_autostart(False)` 写 Hidden=true
6. launcher：`dry_run=True` + 假 `sleep_func` 记录顺序与延迟；`start_all` 的 started/failed/skipped 统计；`command_available`
7. 端到端：`ConfigStore.add` → `sync` → 读取生成的 desktop 内容含正确 Exec

### 11.2 `tests/test_package.py`（packaging-docs 写，Lead 跑）

`unittest`；若 `dist/*.deb` 不存在则 `skipTest("尚未构建 .deb")`；用纯标准库解析 ar + tar：
1. ar 成员顺序与内容（`debian-binary` = `2.0\n`，含 `control.tar.gz`、`data.tar.gz`）
2. control 字段齐全且 `Architecture: all`、`Version` 与 `VERSION` 文件一致、`Depends` 含 `python3`
3. data 包包含 10.2 全部路径，`usr/bin/uos-autostart-manager` 权限 0755
4. 所有 `uos_autostart/*.py` 通过 `ast.parse`
5. control 包含 `md5sums`，且与数据文件一致

---

## 12. 完成报告要求

每个任务完成后，用 `send_message` 回报 Lead，包含：
1. 创建/修改的文件清单（相对路径）
2. 实现的公开 API 清单（与本文档的差异，若有）
3. 自检结论：语法自查方式、已知风险/假设
4. 未完成项（若有）
报告控制在 300 字以内，不要粘贴完整代码。

---

## 13. v1.1.0 变更：掉线自动重启（守护）

需求：每 20 分钟检测一次被托管应用，若未运行则重新启动。v1.1.0 在既有契约上**只做新增**，
不改变任何既有函数签名与字段语义。

### 13.1 `processes.py`（新增，纯标准库）

```python
ProcessEntry = Tuple[int, str]                 # (pid, 命令行字符串)

def list_processes() -> List[ProcessEntry]
    # Linux 优先读 /proc/<pid>/cmdline；Windows 用 tasklist /FO CSV；其他 POSIX 用 ps -axo pid=,command=
    # 任何失败返回 []，绝不抛异常
def target_argv(command: str) -> List[str]     # strip_field_codes + split_command
def command_basename(command: str) -> str
def is_process_running(command: str,
                       processes: Optional[Iterable[ProcessEntry]] = None) -> bool
    # 匹配规则：绝对路径命中命令行；否则进程 argv[0] 的 basename 相同即视为运行
    # 跳过本进程、父进程以及命令行含 "uos_autostart" / "uos-autostart-manager" 的进程
def pid_alive(pid: int) -> bool
```

### 13.2 `watchdog.py`（新增，纯标准库）

```python
THREAD_NAME = "uos-autostart-watchdog"
def clamp_interval_minutes(value) -> int                 # 1..1440，非法→20

class Watchdog(object):
    def __init__(self, store, launcher=None, probe=None, sleep_func=None,
                 logger=None, interval_seconds=None)     # 测试可注入 probe/launcher/间隔
    def load_config(self) -> Optional[Config]
    def interval_minutes(self, cfg=None) -> int
    def interval_seconds(self, cfg=None) -> float
    def run_once(self) -> Dict[str, Any]
        # {"enabled","interval_minutes","checked","running":[id],"restarted":[id],
        #  "failed":[id],"errors":[str]}；绝不抛异常
    def start(self) -> threading.Thread      # daemon 线程；首轮检测在等待一个间隔之后
    def stop(self, timeout=2.0) -> None
    def is_running(self) -> bool
    def wait(self, seconds) -> bool          # 可被 stop() 打断
    def sleep(self, seconds) -> None         # 使用注入的 sleep
```

`run_once()` 行为：读取配置；若 `watchdog_enabled` 为假直接返回；否则对
`cfg.watchdog_items()` 逐项用 `is_process_running()` 判断，未运行则 `launcher.start_item(item)`。
每轮重新读取配置，因此界面修改即时生效。

### 13.3 启动流程

- `app.py` 新增参数：`--no-watchdog`、`--watch`、`--watch-once`、`--watch-interval SECONDS`。
- GUI/托盘模式：在 `--tray` 的后台 `start_all_async(include_xdg=False)` 之后启动 `Watchdog`，
  退出时 `stop()`；`--dry-run` 或 `--no-watchdog` 时不启动。
- 文本模式：`--watch-once` 执行一轮并输出统计（`cli.run_watch_once`）；
  `--watch` 前台循环（`cli.run_watch`），支持 Ctrl+C。

### 13.4 界面（两后端保持一致）

- 表格新增「守护」列（Qt 为可勾选单元格，Tk 为「是/否」+ 右键菜单切换），列顺序：
  `启用 | 名称 | 启动方式 | 延迟 | 守护 | 命令 | 最近启动`。
- 添加/编辑对话框新增「掉线自动重启」勾选。
- 设置对话框新增「启用掉线自动重启（全局开关）」与「检测间隔（分钟，1–1440，默认 20）」。
- 菜单「文件 → 立即检测掉线应用」与托盘菜单「立即检测掉线应用」调用 `MainWindow.check_now()`。
- 状态栏文案新增「守护 K 项」。

### 13.5 测试

新增 `tests/test_watchdog.py`（24 例）：进程匹配、pid_alive、模型字段与默认值、配置 clamp、
`ConfigStore.set_option` 的守护选项、`Watchdog.run_once` 各分支（运行中/未运行/失败/全局关闭/
探测异常/无线程条目）、间隔换算与线程启停。

### 13.6 版本

`VERSION`、`paths.VERSION`、`__init__.__version__` 统一为 `1.1.0`；
`.deb` 文件名相应为 `uos-autostart-manager_1.1.0_all.deb`。旧配置（无守护字段）可无损加载。
