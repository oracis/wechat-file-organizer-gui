# macOS 移植改动清单（WeChatFileOrganizer）

> 目标：让 `main.py` 在 macOS 上可编译、可运行，能正确探测新版 Mac 微信
> 沙盒目录、扫描接收文件、复制归类、预览、回收站删除。
> 本文档基于 v1.15.0 的 `main.py`（2347 行）逐行核对，改动点均带行号/函数名。

> **实施状态（2026-09-03，v1.16.0）**：§3–§7 代码改动**已全部落地**
> （§3.1 `open_in_system`、§3.2 回收站三分派、§4 目录探测 darwin 分支 +
> `_mac_wechat_roots` + BFS 账号发现、§5 `collect_mac_files`、§7 加密类守卫）。
> Windows 上 mock 平台的 27 项分支单测 + Windows 真实环境回归全部通过，
> exe 已重建（v1.16.0）。**未做**：§8 mac 实机验收（8 项）、§9 GitHub
> Actions macos job 与 Apple 证书打包。UI 文案「回收站/废纸篓」本地化为可选
> （§6 备注项），暂未逐条替换。

---

## 0. 结论先行

| 项 | 结论 |
|---|---|
| 是否值得做 | **值得**。Mac 微信是官方桌面版，用户量大；工具核心（扫描+归类+CSV）与平台无关，需改的耦合点集中在 ~6 处 |
| Linux | **不建议**（无官方微信客户端，无数据源） |
| 改动量 | 约 200~300 行新代码 + 40 行替换，无第三方新依赖（回收站用系统 `osascript`） |
| 最大风险 | 回收站删除的**静默永久删除降级**（见 §3.2），必须最先修 |
| 发布形态 | PyInstaller 打 `.app`；GitHub Actions 加 macOS job |

---

## 1. macOS 微信目录结构（移植的事实基础）

新版 Mac 微信（3.x/4.x，沙盒化）文件实际存放位置：

```
~/Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support/
    com.tencent.xinWeChat/
        <版本号，如 2.0b4.0.9 / 4.1.2.345，动态变化>/
            <账号哈希，如 4e720806...>/
                Message/
                    MessageTemp/
                        <会话哈希或年月目录>/
                            File/      ← 收到的文件（docx/pdf/zip…）
                            Image/     ← 收到的图片
                            Video/     ← 视频
                            Audio/     ← 音频
```

与 Windows 结构的关键差异：

| 维度 | Windows（现支持） | macOS（需新增） |
|---|---|---|
| 根目录 | `~/Documents/xwechat_files`、`WeChat Files` | `~/Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support/com.tencent.xinWeChat/`（下含多个版本号目录） |
| 账号层 | 根下直接是账号目录（`oracis_dfa0`） | `<版本号>/<账号哈希>`，版本号动态 |
| 文件层 | 账号下 `msg/file/YYYY-MM` 扁平 | 账号下 `Message/MessageTemp/<会话>/File|Image|Video|Audio` 多级 |
| 内部文件 | `cache/`、`db_storage/` 等 | `Message/MessageTemp` 里除 File/Image/Video/Audio 外还有大量系统目录，须过滤 |
| 微信加密文件类 | `business/favorite/data` + `msg/attach/*.dat` | 结构完全不同，`collect_wechat_internal()` 对 mac 返回空即可 |

---

## 2. 需要改动的位置总览（对照 v1.15.0 main.py）

| # | 位置 | 现状 | 问题 | 改动 |
|---|---|---|---|---|
| 1 | L178 `send_to_recycle_bin()` | `ctypes.windll.shell32` Windows API | **非 Windows 静默退化为永久删除**（最危险） | 加平台分派：darwin 走 `osascript`，其余显式报错（§3.1-3.2） |
| 2 | L250 `find_wechat_files_dir()` | 只查 `~/Documents/...` Windows 候选 | mac 上扫不到 | 加 darwin 分支探测沙盒根（§4.1） |
| 3 | L312 `find_all_wechat_dirs()` | 同上 | 同上 | 同步加 darwin 候选（§4.1） |
| 4 | L280 `_is_account_dir()` | 认 `msg` / `FileStorage` 子目录 | mac 账号目录无这两个特征 | 加 `Message` 特征 + 平台相关判定（§4.2） |
| 5 | L341 `discover_account_dirs()` / L380 `discover_accounts()` | 按 Windows 账号结构解析 | mac 版本号/哈希目录语义不同 | 增加 mac 版账号发现与版本目录排序（取最新版本）（§4.3） |
| 6 | L482 `collect_sources()` / L418 `recursive_collect()` | 文件层假设 `msg/file` 或 `FileStorage/File` | mac 在 `MessageTemp/<会话>/File` | 平台分支：收集 File/Image/Video/Audio 四类并过滤系统目录（§5） |
| 7 | L1485/1527/1534/1540/1751/2284 `os.startfile()` | Windows 专属 | mac 无此函数 → 打开文件夹/文件全失败 | 抽公共函数 `_open_in_system()` 平台分派（§6） |
| 8 | L438 `collect_wechat_internal()` | Windows 加密目录 | mac 结构不同 | 平台守卫：darwin 直接返回空（或按 mac 加密结构扩展，见 §7） |
| 9 | L374 `default_output_dir()` | `~/Desktop/微信文件整理` | macOS 路径 OK（`~/Desktop` 存在） | 无需改，但文件夹名建议本地化保持中文不变 |
| 10 | L53 CONFIG_PATH | `~/.wechat_file_organizer_config.json` | 跨平台兼容 | 无需改 |
| 11 | 缩略图 PIL/ImageTk L99-104 | `PIL` 可选依赖 | mac 装 Pillow 即可，Tk 由 python.org 安装包自带 | 无需改（README 注明 mac 安装方式） |
| 12 | build/CI | `build.bat` Windows；workflow `windows-latest` | mac 无打包 | 新增 mac 打包脚本 + Actions job（§9） |

> 备注：行号为 v1.15.0 快照，若后续版本有漂移，以函数名为准。

---

## 3. 平台抽象层（新增模块，建议放 main.py 顶部）

### 3.1 新增 `PLATFORM` 常量与公共打开函数

```python
import sys
PLATFORM = "mac" if sys.platform == "darwin" else ("win" if os.name == "nt" else "linux")

def open_in_system(path):
    """用系统默认程序打开文件/文件夹，跨平台。"""
    path = os.path.normpath(path)
    if PLATFORM == "mac":
        subprocess.Popen(["open", path])
    elif PLATFORM == "win":
        os.startfile(path)
    else:
        subprocess.Popen(["xdg-open", path])
```

把 L1485/1527/1534/1540/1751/2284 的 `os.startfile(x)` 全部替换为 `open_in_system(x)`。
（mac 上 `open` 可打开文件也可打开文件夹；保留各自 try/except 即可。）

### 3.2 回收站改造（最关键）

现状：L185-199，`ctypes.windll.shell32` 不存在时 **shutil.rmtree 永久删除**。
mac 上 `ctypes.windll` 本身会抛 `AttributeError`，被 L189 的 `except` 接住 → 走永久删除分支，
用户点「清理微信原文件」会**真删且不可恢复**。这是移植的 P0 隐患。

改法（保留 Windows 原逻辑，新增 darwin 分支）：

```python
def send_to_recycle_bin(paths):
    """跨平台移入回收站。Windows 用 Shell API；macOS 用 osascript 调 Finder；
    Linux 返回失败（不静默永久删除）。"""
    if PLATFORM == "mac":
        import subprocess
        ok = 0; failures = []
        for p in paths:
            # 逐条调用 Finder delete（路径含空格/中文需小心转义，用双引号包裹）
            script = ('tell application "Finder" to delete POSIX file "%s"' % p.replace('"', '\\"'))
            r = subprocess.run(["osascript", "-e", script],
                               capture_output=True, text=True)
            if r.returncode == 0:
                ok += 1
            else:
                failures.append((p, "移入废纸篓失败: " + r.stderr.strip()))
        return ok, failures
    # 原 Windows SHFileOperationW 逻辑（L185 起）保持不变
    ...
```

注意：
- 用 `Finder delete` 而非 `mv ~/.Trash`：跨卷（微信目录可能在移动盘）时 mv 会失败且绕过废纸篓；Finder delete 正确处理跨卷、同名冲突与「已占用」。
- 每删一条会闪 Finder 动画，批量删除（L1411/2061）场景若在意体验，可改为 `Finder delete` 一次传多选：`tell application "Finder" to delete {POSIX file "a", POSIX file "b"}`。
- **删除最后的降级保险**：无论哪个平台，若移入回收站失败，**不得**静默回退永久删除；Windows 原 except 分支（L189-199 的 rmtree/os.remove）应改为把该路径计入 failures 并由调用方弹窗告知。此改动同时加固 Windows 侧。

### 3.3 Linux 分支处理

`PLATFORM == "linux"` 时：`send_to_recycle_bin` 返回 `(0, [(p, "Linux 暂不支持移入回收站，已跳过")])`，
不删任何文件；`find_wechat_files_dir` 返回 None，GUI 提示手动选择——即便有用户硬跑，也保证零数据丢失。

---

## 4. 目录探测改造

### 4.1 `find_wechat_files_dir()` / `find_all_wechat_dirs()`

在 Windows 候选（L262-268 / L332-334）之前插入 darwin 分支：

```python
def _mac_wechat_roots():
    """返回 macOS 微信沙盒根目录列表（含所有版本目录下的账号目录，去重）。"""
    base = os.path.expanduser(
        "~/Library/Containers/com.tencent.xinWeChat/Data/Library/"
        "Application Support/com.tencent.xinWeChat")
    if not os.path.isdir(base):
        return []
    roots = []
    for ver in sorted(os.listdir(base)):          # 版本号目录
        vdir = os.path.join(base, ver)
        if not os.path.isdir(vdir):
            continue
        for acct in os.listdir(vdir):             # 账号哈希目录
            d = os.path.join(vdir, acct)
            if os.path.isdir(d) and os.path.isdir(os.path.join(d, "Message")):
                roots.append(d)                   # 直接以「账号目录」作为可扫描根
    return roots
```

- `find_wechat_files_dir()`：darwin 时取 `_mac_wechat_roots()[0]`（最新版本排最前）；
- `find_all_wechat_dirs()`：darwin 时把全部 roots 并入返回列表（多账号合并扫描直接复用 v1.15.0 的合并逻辑）；
- 保留 `WECHAT_FILES_DIR` 环境变量优先逻辑不变（两种平台都先查）。

### 4.2 `_is_account_dir()` 平台化

```python
def _is_account_dir(d):
    if PLATFORM == "mac":
        return os.path.isdir(os.path.join(d, "Message"))
    return (os.path.isdir(os.path.join(d, "msg"))
            or os.path.isdir(os.path.join(d, "FileStorage")))
```

### 4.3 `discover_account_dirs()` / `discover_accounts()`

- mac 语义：账号目录 = `<版本>/<账号哈希>`，一个版本目录下可有多个账号哈希。
  返回所有版本下全部账号目录即可（与 Windows 的多账号语义天然兼容，v1.15.0 的
  「微信账号」列展示账号哈希名即可）。
- `_scan_for_wechat` / `_scan_for_wechat_all`（L235/L286）：目前靠 `FileStorage`/`msg`
  特征 os.walk——对 mac 可加 `Message` 到特征判断（或 mac 直接走 `_mac_wechat_roots`，
  不走 walk，避免深扫沙盒全目录）。

---

## 5. 文件收集改造（`collect_sources` / `recursive_collect`）

mac 的文件层级是 `MessageTemp/<会话>/File|Image|Video|Audio`，且 MessageTemp 下还有
大量系统/中间目录。建议新增专门收集函数，不走现有 Windows 分支：

```python
# mac：MessageTemp 下用户文件的类型子目录（其余全算系统目录过滤掉）
MAC_FILE_SUBDIRS = {"File", "Image", "Video", "Audio"}

def collect_mac_files(acct_dir, include_media=False):
    """收集 mac 微信账号目录下 MessageTemp 中的用户文件。"""
    files = []
    msg_temp = os.path.join(acct_dir, "Message", "MessageTemp")
    if not os.path.isdir(msg_temp):
        return files
    for dp, dirnames, fnames in os.walk(msg_temp):
        base = os.path.basename(dp)
        # 只深入类型目录；其他目录直接剪枝，避免扫到系统数据
        if base in MAC_FILE_SUBDIRS:
            for fn in fnames:
                ext = os.path.splitext(fn)[1].lower()
                if ext in SKIP_EXTS:
                    continue
                if not include_media and ext == ".dat":
                    continue
                files.append(os.path.join(dp, fn))
            dirnames[:] = []
        elif any(sd in dirnames for sd in MAC_FILE_SUBDIRS):
            pass  # 上层目录，继续下探
        else:
            dirnames[:] = []
    return files
```

`collect_sources()` 入口（L482）开头加：

```python
if PLATFORM == "mac":
    out = []
    for acct in (discover_account_dirs(root) or [root]):
        out += collect_mac_files(acct, include_media)
    return out
```

- mac 没有 `msg/file` 扁平层，`scan_all`/`deep_scan` 语义映射为 include_media（把 Image/Video/Audio 也算上；默认 False 只收 File = 与 Windows「收到的文件」一致）。
- `recursive_collect()`（兼容模式全盘扫）对 mac 沙盒不建议启用（沙盒里系统数据极多），
  GUI 上 mac 默认/固定走上述账号收集路径即可。

---

## 6. UI 侧改动点

| 位置 | 改动 |
|---|---|
| L1485（归类后打开输出文件夹）| `os.startfile(dest)` → `open_in_system(dest)` |
| L1527（双击非图片用系统打开）| → `open_in_system(path)` |
| L1534（大图窗口「用系统程序打开」）| → `open_in_system(path)` |
| L1540（「打开所在文件夹」）| → `open_in_system(os.path.dirname(path))` |
| L1751 / L2284（重复文件查找器/其他打开输出）| → `open_in_system(d)` |
| 缩略图预览 L913/1546/1572/1653 | PIL 跨平台，无需改；ImageTk 在 mac Tk 上可用 |
| 提示文案 | 「回收站」在 mac 称「废纸篓」，删除确认弹窗文案建议按 PLATFORM 切换（可选） |

---

## 7. 「微信加密文件」类在 mac 上的处理

- mac 微信不采用 `business/favorite/data` + `msg/attach/*.dat` 结构，`collect_wechat_internal()` 入口加 `if PLATFORM == "mac": return []`，UI 上该类别照常显示但扫描恒为空（或整类隐藏）。
- 若后续要支持 mac 加密收藏：需逆向 mac 版 `favorite.db` 与附件加密格式，超出本清单范围，建议 v2 单独做。

---

## 8. 测试与验收（mac 实机）

无 mac 实机时先在 Windows 上做平台分支单测（mock `PLATFORM`），实机验收项：

1. `find_wechat_files_dir()` 返回沙盒内最新版本账号目录，含中文/空格路径正常
2. 双账号（两版本或同版本两哈希）合并扫描，账号列正确显示哈希名
3. 默认只收 File 下文件；勾选媒体后 Image/Video/Audio 计入
4. `_is_account_dir` 对普通目录（如 Downloads）返回 False
5. 缩略图 jpg/png/webp 解码（Pillow 装后），大图窗口、系统打开、所在文件夹均可用
6. **回收站**：复制归类后「清理微信原文件」→ 文件进废纸篓可恢复；故意构造失败路径不删文件
7. CSV 账号列、去重、时间/大小筛选行为与 Windows 一致
8. `.app` 双击启动、签名/公证后 Gatekeeper 不拦截

---

## 9. 打包与 CI

### 9.1 本地打包（mac 上执行）

```
pip install pyinstaller pillow
pyinstaller --onefile --windowed --name WeChatFileOrganizer \
    --exclude-module numpy --exclude-module PIL.ImageQt --exclude-module PIL.ImageShow \
    main.py
# 产物在 dist/WeChatFileOrganizer.app
```

### 9.2 GitHub Actions 新增 macOS job

在 `.github/workflows/build-and-sign.yml` 里加 `macos-latest` job（与 windows job 平行）：
- 构建命令同上（pyinstaller 在 macos runner 上直接产出 .app）
- `--onefile` 在 mac 上产物是 app 目录，上传 asset 前用 `ditto -c -k --keepParent` 打成 zip（或改用 `--onedir` + zip）
- Release asset 追加 `WeChatFileOrganizer-mac.zip`
- mac 代码签名/公证需要 Apple Developer 证书（$99/年）+ notarization，属用户侧一次性配置；
  未配置时**跳过签名直接发未公证 zip**（与 Windows SignPath 的「优雅降级」模式一致），
  首次打开需右键-打开绕过 Gatekeeper，README 注明。

---

## 10. 落地顺序建议

1. **P0 安全**：§3.2 回收站加固（Windows 侧同步受益：失败不再永久删除）
2. §3.1 `open_in_system` 替换 6 处 `os.startfile`（纯重构，Windows 行为不变）
3. §4 目录探测 darwin 分支 + §4.2/4.3 账号发现
4. §5 `collect_mac_files` + `collect_sources` 分支
5. §6 UI 文案微调、§7 加密类守卫
6. Windows 回归（无头 smoke 21 项）+ mac 实机验收（§8）
7. §9 CI 加 mac job，发布 v1.16.0

预计 1-2 个完整工作会话可完成代码；mac 实机验证 + Apple 开发者证书申请是外部依赖，需用户侧配合。
