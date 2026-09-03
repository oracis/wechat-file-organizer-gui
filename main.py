#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信文件自动归类 - 图形界面版 (wechat-file-organizer-gui)
扫描微信文件目录（兼容传统 FileStorage/File 与新型自定义目录），
按类型/月份归类、去重、生成报告，一键复制到独立目录。

设计原则：
- 零运行时依赖：仅用 Python 标准库（tkinter 自带）。
- 安全优先：默认只扫描、生成预览报告，绝不改动任何源文件；点「开始整理」才复制。
- 「清理原始文件」为可选显式操作：选定后移入回收站（可恢复），默认不永久删除；批量清理时会提示哪些文件已有归类副本、哪些尚未归类，由用户确认后再执行。
- 中文路径/文件名友好。
- v1.5.0：文件列表拆分为「原始文件」与「归类副本」两个标签页，删除后自动刷新。
- v1.6.0：新增「按修改时间筛选」（最近7/30/90天、今年、自定义日期区间）与「输出目录结构自定义」。
- v1.7.0：新增「按文件大小筛选」与自定义模板「实时路径预览」。
- v1.8.0：新增「检查更新」——启动后静默查询 GitHub Releases 最新版本。
- v1.9.0：简化「输出目录结构」选择，去掉模板令牌/自定义模板，改为直白的固定选项，并实时显示整理示例。
- v1.10.0：启动后自动扫描（微信目录有效时），并顶部显示三步使用提示，让普通用户打开即见结果。
- v1.11.0：一键归类完成后自动打开输出文件夹；兼容模式标签改为更直白的「扫描整个文件夹（适合新版微信）」。
- v1.12.0：记住用户上次使用的整理方式、筛选、类别勾选和输出目录；新增「打开输出文件夹」按钮。
- v1.13.0：当自动探测不到微信目录时给出红色引导提示；一键归类后自动生成「整理清单.csv」，方便用户核对文件去向。
- v1.14.0：界面重做为「三步向导式」单屏布局——① 选微信文件夹 ② 勾选要整理的类型 ③ 预览并整理。
          进阶项（整理方式 7 种 / 去重 / 扫描范围 / 时间 / 大小筛选 / 文件清单 / 日志）收进可折叠区，默认不显示，
          让小白用户打开即用，老手展开仍有全部能力；新增大号主按钮「开始整理」与更友好的引导文案。
- v1.15.0：新增「多微信账号合并扫描」——自动探测电脑上所有微信文件根目录（新版 xwechat_files 与旧版
          WeChat Files 可同时存在），合并扫描并在文件清单中标注每个文件属于哪个账号；
          新增「图片内嵌缩略图预览」——用 Pillow 解码，选中图片即在清单下方显示缩略图，双击可看大图（支持 jpg）。
"""
import os
import re
import sys
import time
import json
import shutil
import threading
import unicodedata
import urllib.request
from datetime import datetime

# 当前版本与更新检查仓库（公开 Release）
APP_VERSION = "1.15.0"
UPDATE_REPO = "oracis/wechat-file-organizer-gui"

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext, Menu
except ImportError:
    sys.exit("本程序需要 Tkinter 图形库（Python 标准库自带，正常情况下已包含）。")

MB = 1024 * 1024

# 用户设置持久化文件
CONFIG_PATH = os.path.join(os.path.expanduser("~"),
                           ".wechat_file_organizer_config.json")

# 分类规则：扩展名 -> 类别
CATEGORIES = {
    "文档":   ["pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "md",
               "csv", "rtf", "wps", "ofd", "pages", "key", "numbers", "epub", "mobi"],
    "图片":   ["png", "jpg", "jpeg", "gif", "bmp", "webp", "heic", "tiff", "tif", "svg"],
    "压缩包": ["zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "zst"],
    "视频":   ["mp4", "mov", "avi", "mkv", "wmv", "flv", "webm", "m4v"],
    "音频":   ["mp3", "wav", "m4a", "aac", "flac", "ogg", "wma"],
    "其他":   [],
    "微信加密文件": [],  # 由扫描逻辑单独收集（微信收藏/附件，被微信私有加密）
}
# 微信加密文件的类别名（第 7 类），复制后可能无法直接打开
ENCRYPTED_CAT = "微信加密文件"
EXT_TO_CAT = {}
for _cat, _exts in CATEGORIES.items():
    for _e in _exts:
        EXT_TO_CAT[_e] = _cat

# 整理方式：界面显示名 -> 内部键（直白选项）
SCHEME_LABELS = {
    "按文件类型": "type",
    "按月份": "month",
    "按年份": "year",
    "按类型+月份": "type-month",
    "按类型+年份": "type-year",
    "按年份+月份": "year-month",
    "按类型+年份+月份": "type-year-month",
}

# 扫描筛选：时间范围
TIME_RANGES = ["全部", "最近7天", "最近30天", "最近90天", "今年", "自定义"]

# 扫描筛选：文件大小
SIZE_FILTERS = ["全部", "≥1MB", "≥10MB", "≥100MB", "自定义(MB)"]

# 常见图片格式：优先用 Pillow 解码（支持 jpg/jpeg/webp），
# Pillow 不可用时回退到 Tk PhotoImage（仅支持 png/gif/bmp/ppm）。
IMAGE_PREVIEW_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".ico",
}
# Tk PhotoImage 原生支持的格式（无 Pillow 时的降级范围）
TK_NATIVE_IMAGE_EXTS = {".png", ".gif", ".bmp", ".tif", ".tiff", ".ppm", ".pgm"}

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except Exception:      # 未安装 Pillow 时不影响主流程，仅缩略图/大图预览降级
    Image = ImageTk = None
    PIL_AVAILABLE = False

# 缩略图缓存上限（避免大目录选中浏览时内存持续增长）
THUMB_CACHE_MAX = 24
# 超过此大小的图片不再生成缩略图（防止大图解码卡顿）
THUMB_MAX_BYTES = 40 * 1024 * 1024

MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")

# 兼容模式（递归扫描）时跳过的微信系统/缓存目录
SYSTEM_DIRS = {
    "all_users", "db_storage", "apm_record", "business", "resource",
    "cache", "backup", "temp", "tmp", "logs", "log", "config", "mmkv",
    "thumb", "thumbs", ".thumbnails", "favorite", "emoticon", "sns",
    "xweb", "xeditor", "migrate", "InputTemp", "MsgAttach",
}

# 兼容模式时跳过的微信内部文件扩展名（非用户主动保存的文件）
SKIP_EXTS = {
    ".dat", ".db", ".db-wal", ".db-shm", ".mmkv", ".crc", ".kvdb",
    ".kvdb-wal", ".kvdb-shm", ".ini", ".lock", ".tmp", ".temp", ".bak",
    ".shm", ".wal", ".sqlite", ".sqlitedb",
}


# ---------- 中文宽度对齐 ----------
def wlen(s):
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in str(s))


def wpad(s, width, fill=" "):
    s = str(s)
    return s + fill * max(0, width - wlen(s))


def human(n):
    if n >= 1024 ** 3:
        return "%.1f GB" % (n / 1024 ** 3)
    if n >= MB:
        return "%.1f MB" % (n / MB)
    if n >= 1024:
        return "%.1f KB" % (n / 1024)
    return "%d B" % n


def cat_of(path):
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    return EXT_TO_CAT.get(ext, "其他")


def month_of(path):
    parent = os.path.basename(os.path.dirname(path))
    m = MONTH_RE.match(parent)
    if m:
        return "%s-%s" % (m.group(1), m.group(2))
    try:
        t = os.path.getmtime(path)
        return datetime.fromtimestamp(t).strftime("%Y-%m")
    except OSError:
        return "未知"


def sha256_of(p, chunk=1 << 20):
    import hashlib
    h = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            for b in iter(lambda: f.read(chunk), b""):
                h.update(b)
    except OSError:
        return "ERR"
    return h.hexdigest()


def send_to_recycle_bin(paths):
    """把文件/目录移入 Windows 回收站（可恢复）。

    返回 (ok, failures)，failures 为 (path, reason) 列表。
    若无法调用 Shell，则回退为永久删除并给出警告。
    注意：默认不弹系统确认框（FOF_NOCONFIRMATION），由调用方负责二次确认。
    """
    import ctypes
    from ctypes import wintypes
    try:
        shell32 = ctypes.windll.shell32
    except Exception:
        failures = []
        for p in paths:
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p)
                else:
                    os.remove(p)
            except OSError as e:
                failures.append((p, "永久删除失败: " + str(e)))
        return len(paths) - len(failures), failures

    class SHFILEOPSTRUCT(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.UINT),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x40          # 移入回收站（可恢复）
    FOF_NOCONFIRMATION = 0x10     # 不弹系统确认框
    FOF_NOERRORUI = 0x0400
    FOF_SILENT = 0x0004

    from_str = "\0".join(paths) + "\0\0"
    op = SHFILEOPSTRUCT()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = from_str
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT
    rc = shell32.SHFileOperationW(ctypes.byref(op))
    if rc == 0 and not op.fAnyOperationsAborted:
        return len(paths), []
    # 部分失败：统计仍未删除的
    failures = [(p, "仍存在于磁盘 (rc=%r)" % rc) for p in paths if os.path.exists(p)]
    return len(paths) - len(failures), failures


# ---------- 路径发现 ----------
def _scan_for_wechat(root, max_depth=4):
    """在 root 下有限深度搜索微信文件根目录（含 FileStorage 或 msg 特征目录）。"""
    if not os.path.isdir(root):
        return None
    for dirpath, dirnames, _ in os.walk(root):
        depth = dirpath[len(root):].count(os.sep)
        if depth > max_depth:
            dirnames[:] = []
            continue
        for marker in ("FileStorage", "msg"):
            if marker in dirnames:
                return dirpath
    return None


def find_wechat_files_dir():
    """自动探测微信文件目录，兼容传统与新版/自定义结构。

    返回最浅的『微信根目录』（如 .../xwechat_files 或 .../wxid_xxx），
    供 GUI 直接填入源目录框；扫描时会自动启用兼容模式。
    """
    # 1. 环境变量（高级用户手动指定）
    env = os.environ.get("WECHAT_FILES_DIR")
    if env and os.path.isdir(env):
        return env
    home = os.path.expanduser("~")
    # 2. 常见候选目录名（传统 / 新版 / 自定义）
    candidates = [
        os.path.join(home, "Documents", "WeChat Files"),
        os.path.join(home, "Documents", "Weixin Files"),
        os.path.join(home, "Documents", "xwechat_files"),
        os.path.join(home, "WeChat Files"),
        os.path.join(home, "Weixin Files"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    # 3. 有限深度扫描文档目录，按微信特征目录定位
    docs = os.path.join(home, "Documents")
    found = _scan_for_wechat(docs, max_depth=4)
    if found:
        return found
    return None


def _is_account_dir(d):
    """是否为单个微信账号目录（含 msg 或 FileStorage 特征子目录）。"""
    return (os.path.isdir(os.path.join(d, "msg"))
            or os.path.isdir(os.path.join(d, "FileStorage")))


def _scan_for_wechat_all(root, max_depth=4, limit=8):
    """在 root 下有限深度搜索所有微信文件根目录。

    命中含 FileStorage/msg 的账号目录后，取其父目录作为『微信文件根目录』。
    """
    found = []
    if not os.path.isdir(root):
        return found
    root = os.path.normpath(root)
    try:
        for dirpath, dirnames, _ in os.walk(root):
            depth = dirpath[len(root):].count(os.sep)
            if depth > max_depth:
                dirnames[:] = []
                continue
            if "FileStorage" in dirnames or "msg" in dirnames:
                p = os.path.dirname(dirpath) or dirpath
                if p not in found:
                    found.append(p)
                if len(found) >= limit:
                    break
    except OSError:
        pass
    return found


def find_all_wechat_dirs():
    """探测电脑上所有微信文件根目录。

    一台机器可能同时存在多个（新版 xwechat_files、旧版 WeChat Files、
    自定义路径等）。返回去重后的列表，保持优先级顺序。
    """
    out = []

    def add(p):
        if not p or not os.path.isdir(p):
            return
        p = os.path.normpath(p)
        low = p.lower()
        if all(low != x.lower() for x in out):
            out.append(p)

    env = os.environ.get("WECHAT_FILES_DIR")
    if env:
        add(env)
    home = os.path.expanduser("~")
    for name in ("WeChat Files", "Weixin Files", "xwechat_files"):
        add(os.path.join(home, "Documents", name))
        add(os.path.join(home, name))
    for p in _scan_for_wechat_all(os.path.join(home, "Documents"),
                                  max_depth=4):
        add(p)
    return out


def discover_account_dirs(root):
    """返回 root 下的微信账号目录列表（新旧结构均可）。"""
    accounts = []
    if not root or not os.path.isdir(root):
        return accounts
    root = os.path.normpath(root)
    if _is_account_dir(root):
        accounts.append(root)
    try:
        for name in sorted(os.listdir(root)):
            d = os.path.join(root, name)
            if os.path.isdir(d) and _is_account_dir(d) and d not in accounts:
                accounts.append(d)
    except OSError:
        pass
    return accounts


def account_label_of(path, root):
    """判断文件属于哪个微信账号，返回账号目录名（用于清单展示）。"""
    r = os.path.normpath(root)
    p = os.path.normpath(path)
    if _is_account_dir(r):
        return os.path.basename(r)
    if p.lower().startswith(r.lower() + os.sep):
        parts = os.path.relpath(p, r).split(os.sep)
        if len(parts) > 1:
            return parts[0]
    return os.path.basename(r)


def default_output_dir():
    """默认输出目录：桌面下的『微信文件整理』，普通用户最容易找到。"""
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.isdir(desktop):
        return os.path.join(desktop, "微信文件整理")
    return os.path.join(os.path.expanduser("~"), "Documents", "微信文件整理")


def discover_accounts(root):
    """返回所有『用户接收文件目录』（每个账户存放收到文件的地方）。

    兼容两种微信结构：
      - 旧版微信: <root>/<wxid>/FileStorage     （文件在 FileStorage/File 下）
      - 新版微信: <root>/<账户>/msg/file         （如 xwechat_files/oracis_dfa0/msg/file）
      - 精简结构: <root>/File 直接存在
    """
    if os.path.basename(root).lower() == "filestorage":
        return [root]
    targets = []
    if not os.path.isdir(root):
        return targets
    # 直接就是 File 目录（如旧版 FileStorage/File 被整体选中）
    if os.path.isdir(os.path.join(root, "File")):
        targets.append(root)
        return targets
    # 直接就是账户目录本身（用户手动选中 <账户> 而非更上层）
    mf_self = os.path.join(root, "msg", "file")
    if os.path.isdir(mf_self):
        targets.append(mf_self)
    fs_self = os.path.join(root, "FileStorage")
    if os.path.isdir(fs_self):
        targets.append(fs_self)
    # 遍历账户子目录，识别旧版 FileStorage 与 新版 msg/file
    for name in os.listdir(root):
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            continue
        fs = os.path.join(d, "FileStorage")
        if os.path.isdir(fs):
            targets.append(fs)
        mf = os.path.join(d, "msg", "file")
        if os.path.isdir(mf):
            targets.append(mf)
    return targets


def recursive_collect(root):
    """兼容模式：递归扫描整个目录，跳过微信系统目录与内部文件。"""
    files = []
    seen = set()
    for dirpath, dirnames, fnames in os.walk(root):
        # 过滤系统目录（不区分大小写）
        dirnames[:] = [d for d in dirnames
                       if d.lower() not in SYSTEM_DIRS and not d.startswith(".")]
        for fn in fnames:
            ext = os.path.splitext(fn)[1].lower()
            if ext in SKIP_EXTS:
                continue
            p = os.path.join(dirpath, fn)
            if p in seen:
                continue
            seen.add(p)
            files.append(p)
    return files


def collect_wechat_internal(root):
    """收集微信『加密存储的内部文件』：收藏（business/favorite/data）与聊天附件
    （msg/attach/*.dat）。这些文件被微信私有加密，原文件名与内容均不可直接读取，
    复制出来通常无法直接打开；本工具仅将它们列出/备份，不做解密。

    返回文件路径列表。
    """
    result = []
    if not os.path.isdir(root):
        return result

    def scan_account(acct):
        # 微信收藏：business/favorite/data/<hash>/<hash>（无扩展名，加密）
        fav = os.path.join(acct, "business", "favorite", "data")
        if os.path.isdir(fav):
            for dp, _, fns in os.walk(fav):
                for fn in fns:
                    ext = os.path.splitext(fn)[1].lower()
                    if ext in (".db", ".db-wal", ".db-shm", ".sqlite"):
                        continue
                    result.append(os.path.join(dp, fn))
        # 聊天附件：msg/attach/**/*.dat（加密图片/视频）
        att = os.path.join(acct, "msg", "attach")
        if os.path.isdir(att):
            for dp, _, fns in os.walk(att):
                for fn in fns:
                    if fn.lower().endswith(".dat"):
                        result.append(os.path.join(dp, fn))

    # root 可能是顶层（含多个账户），也可能是单个账户目录
    if (os.path.isdir(os.path.join(root, "business", "favorite", "data"))
            or os.path.isdir(os.path.join(root, "msg", "attach"))):
        scan_account(root)
    else:
        try:
            for name in os.listdir(root):
                d = os.path.join(root, name)
                if os.path.isdir(d):
                    scan_account(d)
        except OSError:
            pass
    return result


def collect_sources(root, include_media=False, scan_all=False, recursive=False):
    if recursive:
        files = recursive_collect(root)
        if not include_media:
            files = [f for f in files
                     if os.path.splitext(f)[1].lower() != ".dat"]
        return files
    files = []
    seen = set()
    targets = discover_accounts(root)
    if not targets:
        # 兜底：所选目录无法匹配已知微信结构时，直接扫描该目录本身
        # （仍会套用 SKIP_EXTS，跳过微信系统与缓存文件）
        targets = [root]
    for target in targets:
        # target 可能是旧版 FileStorage 目录，或新版 msg/file 目录
        dirs_to_walk = []
        if os.path.basename(target).lower() == "filestorage":
            file_dir = os.path.join(target, "File")
            if os.path.isdir(file_dir):
                dirs_to_walk.append(file_dir)
            if scan_all:
                for sub in ("Image", "Video", "Voice", "Attachment",
                            "CustomEmotion", "Fav"):
                    sd = os.path.join(target, sub)
                    if os.path.isdir(sd):
                        dirs_to_walk.append(sd)
        else:
            # 新版结构：msg/file 直接就是文件根目录
            dirs_to_walk.append(target)
        for t in dirs_to_walk:
            for dirpath, _, fnames in os.walk(t):
                for fn in fnames:
                    ext = os.path.splitext(fn)[1].lower()
                    if ext in SKIP_EXTS:
                        continue
                    if not include_media and ext == ".dat":
                        continue
                    p = os.path.join(dirpath, fn)
                    if p in seen:
                        continue
                    seen.add(p)
                    files.append(p)
    return files


def preset_rel(key, cat, month, fname):
    year = month.split("-")[0] if "-" in month else "未知"
    if key == "type":
        parts = [cat]
    elif key == "month":
        parts = [month]
    elif key == "year":
        parts = [year]
    elif key == "type-month":
        parts = [cat, month]
    elif key == "type-year":
        parts = [cat, year]
    elif key == "year-month":
        parts = [year, month]
    elif key == "type-year-month":
        parts = [cat, year, month]
    else:
        parts = [cat]
    return os.path.join(*parts, fname)


def dest_path(dest_root, rel, used):
    """根据相对路径 rel（含文件名）计算去重后的目标路径。"""
    if not rel:
        rel = os.path.basename(rel) or "file"
    base, ext = os.path.splitext(os.path.basename(rel))
    cand = rel
    i = 1
    while os.path.join(dest_root, cand) in used or os.path.exists(os.path.join(dest_root, cand)):
        d = os.path.dirname(rel)
        cand = os.path.join(d, "%s_%d%s" % (base, i, ext))
        i += 1
    used.add(os.path.join(dest_root, cand))
    return os.path.join(dest_root, cand)


# ---------- 图形界面 ----------
class OrganizerApp:
    def __init__(self, master):
        self.master = master
        master.title("微信文件整理助手")
        master.geometry("940x780")
        master.minsize(820, 640)

        self.records = []
        self.file_list = []          # 原始文件记录（与 tree_files 行一一对应）
        self.output_list = []        # 归类副本记录（与 tree_outputs 行一一对应）
        self.copy_map = {}           # 归类后 src -> dst 映射
        self.has_organized = False

        self.source_dir = tk.StringVar()
        self.dest_dir = tk.StringVar()
        self.scheme = tk.StringVar(value="按文件类型")
        self.dedupe = tk.BooleanVar(value=False)
        # 新版微信多用 xwechat_files 自定义结构，默认直接扫描整个文件夹更省心
        self.deep_scan = tk.BooleanVar(value=True)
        # 多微信账号合并扫描：默认开启，把电脑上探测到的其他微信根目录一起扫描
        self.merge_accounts = tk.BooleanVar(value=True)
        # 本次扫描实际覆盖的微信根目录列表
        self.roots = []
        # 缩略图缓存 {(path, mtime, size): PhotoImage} 与当前展示图（防止被回收）
        self._thumb_cache = {}
        self._thumb_image = None
        self.time_range = tk.StringVar(value="全部")
        self.date_from = tk.StringVar()
        self.date_to = tk.StringVar()
        self.size_filter = tk.StringVar(value="全部")
        self.min_mb = tk.StringVar()
        self.scanning = False

        # 分类勾选（默认全勾）
        self.cat_enabled = {c: tk.BooleanVar(value=True) for c in CATEGORIES}
        # 一键归档+清理：整理后是否自动清理微信原始文件（回收站）
        self.clean_after = tk.BooleanVar(value=False)

        # 加载上次使用的设置（源目录仍每次自动探测，不保存）
        self._load_config()

        # 微信加密文件默认不勾选（复制后可能无法直接打开）
        if ENCRYPTED_CAT in self.cat_enabled:
            self.cat_enabled[ENCRYPTED_CAT].set(False)
        # 勾选「微信加密文件」时是否已弹过说明（本次运行只提示一次）
        self._enc_warned = False

        detected = find_wechat_files_dir() or ""
        self.source_dir.set(detected)
        if not self.dest_dir.get().strip():
            self.dest_dir.set(default_output_dir())

        self._build_ui()

    # ---------- 折叠区工具 ----------
    def _make_collapsible(self, parent, title, default_open=False):
        """在 parent 内创建可折叠区，返回 (container, body, title_var)。

        标题以蓝字链接样式显示，带 ▼/▶ 箭头，整行可点，明显可交互。
        title_var 可被外部修改以动态更新标题（如显示数量）。
        """
        container = ttk.Frame(parent)
        title_var = tk.StringVar(value=title)
        header_var = tk.StringVar(
            value=("▼ " if default_open else "▶ ") + title)

        # 可点击的标题按钮（看起来像一个展开/收起链接）
        head = tk.Button(
            container,
            textvariable=header_var,
            command=None,
            relief="flat",
            borderwidth=0,
            bg="#f0f0f0",
            fg="#1a73e8",
            activebackground="#f0f0f0",
            activeforeground="#1558b0",
            font=("Microsoft YaHei UI", 10, "bold", "underline"),
            cursor="hand2",
            anchor="w",
            padx=4,
            pady=2,
        )
        head.pack(fill="x")

        body = ttk.Frame(container)
        if default_open:
            body.pack(fill="x", padx=(20, 0), pady=(4, 0))

        def _refresh_header(*_):
            arrow = "▼" if body.winfo_ismapped() else "▶"
            header_var.set(f"{arrow} {title_var.get()}")

        def _toggle(event=None):
            if body.winfo_ismapped():
                body.pack_forget()
                header_var.set(f"▶ {title_var.get()}")
            else:
                body.pack(fill="x", padx=(20, 0), pady=(4, 0))
                header_var.set(f"▼ {title_var.get()}")

        head.configure(command=_toggle)
        title_var.trace_add("write", _refresh_header)

        container.pack(fill="x", padx=8, pady=(4, 2))
        return container, body, title_var

    def _step_frame(self, parent, title):
        f = ttk.LabelFrame(parent, text=title)
        f.pack(fill="x", padx=14, pady=6)
        return f

    # ---------- UI 构建 ----------
    def _build_ui(self):
        # 顶部品牌横幅
        header = tk.Frame(self.master, bg="#1a73e8")
        header.pack(fill="x", side="top")
        tk.Label(header, text="微信文件整理助手", bg="#1a73e8", fg="white",
                 font=("Microsoft YaHei UI", 16, "bold"), anchor="w",
                 padx=16).pack(anchor="w", pady=(10, 2))
        tk.Label(header,
                 text="帮你把微信里收到的文件，按类型分好类，整理到桌面文件夹（微信里的原文件不会被改动）",
                 bg="#1a73e8", fg="white", font=("Microsoft YaHei UI", 10, "bold"),
                 anchor="w", padx=16).pack(anchor="w", pady=(0, 10))

        # 状态栏（底部常驻）
        self.status = tk.StringVar(value="就绪")
        ttk.Label(self.master, textvariable=self.status, relief="sunken",
                  anchor="w").pack(fill="x", side="bottom")

        # 可滚动内容区
        canvas = tk.Canvas(self.master)
        scroll = ttk.Scrollbar(self.master, orient="vertical",
                               command=canvas.yview)
        content = ttk.Frame(canvas)
        content.bind("<Configure>",
                     lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        win_id = canvas.create_window((0, 0), window=content, anchor="nw")

        def _sync_width(_event=None):
            # 让内容区宽度始终跟随窗口（canvas）宽度，缩放窗口时内容随之拉伸
            w = canvas.winfo_width()
            if w > 1:
                canvas.itemconfigure(win_id, width=w)

        canvas.bind("<Configure>", _sync_width)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.bind("<MouseWheel>",
                    lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # ===== ① 选择微信文件夹 =====
        step1 = self._step_frame(content, "① 选择微信文件所在的文件夹")
        row = ttk.Frame(step1)
        row.pack(fill="x", padx=8, pady=(4, 0))
        ttk.Label(row, text="微信文件夹：").pack(side="left")
        ttk.Entry(row, textvariable=self.source_dir, width=70,
                  state="readonly").pack(side="left", fill="x", expand=True,
                                          padx=4)
        ttk.Button(row, text="换个文件夹", command=self.browse_source).pack(
            side="left", padx=4)
        self.src_hint = ttk.Label(step1, text="", foreground="#b00020")
        self.src_hint.pack(anchor="w", padx=10, pady=(2, 0))
        if not self.source_dir.get().strip():
            self.src_hint.configure(
                text="未自动找到微信文件夹，请点「换个文件夹」手动选择")
        # 多账号合并扫描提示（扫描完成后填充）
        self.acct_hint = ttk.Label(step1, text="", foreground="#1a73e8")
        self.acct_hint.pack(anchor="w", padx=10, pady=(2, 0))
        self.scan_btn = ttk.Button(step1, text="重新扫描",
                                   command=self.scan)
        self.scan_btn.pack(anchor="w", padx=8, pady=(4, 8))
        ttk.Separator(step1, orient="horizontal").pack(fill="x", padx=8, pady=2)

        # ===== ② 选择要整理的文件类型 =====
        step2 = self._step_frame(content, "② 选择要整理的文件类型（默认全部）")
        chips = ttk.Frame(step2)
        chips.pack(fill="x", padx=8, pady=6)
        for c in CATEGORIES:
            if c == ENCRYPTED_CAT:
                continue  # 微信加密文件单独一行展示，并附说明
            ttk.Checkbutton(chips, text=c, variable=self.cat_enabled[c],
                            command=self._refresh_will).pack(
                side="left", padx=6, pady=2)
        # 微信加密文件：单独一行，明确说明复制后可能无法直接打开
        enc_row = ttk.Frame(step2)
        enc_row.pack(fill="x", padx=8, pady=(2, 6))
        ttk.Checkbutton(enc_row, text="微信加密文件",
                        variable=self.cat_enabled[ENCRYPTED_CAT],
                        command=self._on_encrypted_toggle).pack(side="left", padx=6)
        ttk.Label(enc_row,
                  text="（微信收藏/附件，已被加密，复制后无法直接打开；勾选后可在文件清单右键查看说明）",
                  foreground="#888888",
                  font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(0, 6))

        # 高级选项（默认折叠）
        _, adv_body, _ = self._make_collapsible(
            step2, "高级选项（整理方式 / 去重 / 扫描范围 / 筛选）",
            default_open=False)
        # 整理方式
        la = ttk.Frame(adv_body)
        la.pack(fill="x", padx=8, pady=4)
        ttk.Label(la, text="整理方式：").pack(side="left", padx=(0, 4))
        cb = ttk.Combobox(la, textvariable=self.scheme, width=22,
                          state="readonly")
        cb["values"] = tuple(SCHEME_LABELS.keys())
        cb.bind("<<ComboboxSelected>>", self._on_scheme_change)
        cb.pack(side="left", padx=(0, 10))
        ttk.Checkbutton(la, text="去重（相同内容只保留一份）",
                        variable=self.dedupe).pack(side="left", padx=(4, 8))
        self.scheme_preview = ttk.Label(
            adv_body, text="", foreground="#666666", wraplength=820)
        self.scheme_preview.pack(anchor="w", padx=10, pady=(0, 6))
        # 扫描范围
        lm = ttk.Frame(adv_body)
        lm.pack(fill="x", padx=8, pady=2)
        ttk.Label(lm, text="扫描范围：").pack(side="left", padx=(0, 4))
        ttk.Radiobutton(lm, text="只整理微信收到的文件",
                        variable=self.deep_scan, value=False,
                        command=self._on_scan_mode_change).pack(
            side="left", padx=4)
        ttk.Radiobutton(lm, text="扫描整个文件夹（适合新版微信）",
                        variable=self.deep_scan, value=True,
                        command=self._on_scan_mode_change).pack(
            side="left", padx=4)
        ttk.Checkbutton(lm, text="合并扫描其他微信账号（多账号时有用）",
                        variable=self.merge_accounts,
                        command=self.scan).pack(side="left", padx=(12, 4))
        # 时间筛选
        lf = ttk.Frame(adv_body)
        lf.pack(fill="x", padx=8, pady=2)
        ttk.Label(lf, text="时间范围：").pack(side="left", padx=(0, 4))
        cb_t = ttk.Combobox(lf, textvariable=self.time_range, width=14,
                            state="readonly")
        cb_t["values"] = tuple(TIME_RANGES)
        cb_t.bind("<<ComboboxSelected>>", self._on_time_range_change)
        cb_t.pack(side="left", padx=(0, 6))
        ttk.Label(lf, text="起：").pack(side="left")
        self.date_from_entry = ttk.Entry(lf, textvariable=self.date_from,
                                         width=12, state="disabled")
        self.date_from_entry.pack(side="left", padx=(0, 4))
        ttk.Label(lf, text="止：").pack(side="left")
        self.date_to_entry = ttk.Entry(lf, textvariable=self.date_to,
                                        width=12, state="disabled")
        self.date_to_entry.pack(side="left", padx=(0, 4))
        ttk.Label(lf, text="（自定义时填 YYYY-MM-DD）").pack(side="left")
        self._on_time_range_change()
        # 大小筛选
        ls = ttk.Frame(adv_body)
        ls.pack(fill="x", padx=8, pady=(2, 6))
        ttk.Label(ls, text="文件大小：").pack(side="left", padx=(0, 4))
        cb_s = ttk.Combobox(ls, textvariable=self.size_filter, width=14,
                            state="readonly")
        cb_s["values"] = tuple(SIZE_FILTERS)
        cb_s.bind("<<ComboboxSelected>>", self._on_size_filter_change)
        cb_s.pack(side="left", padx=(0, 6))
        ttk.Label(ls, text="最小(MB)：").pack(side="left")
        self.min_mb_entry = ttk.Entry(ls, textvariable=self.min_mb,
                                      width=12, state="disabled")
        self.min_mb_entry.pack(side="left", padx=(0, 4))
        ttk.Label(ls, text="（选「自定义(MB)」时可用）").pack(side="left")
        self._on_size_filter_change()

        # ===== ③ 预览并整理 =====
        step3 = self._step_frame(content, "③ 预览并整理")
        self.preview_var = tk.StringVar(value="（请先扫描微信文件夹）")
        tk.Label(step3, textvariable=self.preview_var,
                 font=("Microsoft YaHei UI", 11, "bold"),
                 fg="#1a73e8").pack(anchor="w", padx=10, pady=(6, 2))

        # 文件清单（默认折叠）
        _, file_body, self.file_list_title = self._make_collapsible(
            step3, "查看文件清单", default_open=False)
        self.file_notebook = ttk.Notebook(file_body)
        self.file_notebook.pack(fill="both", expand=True, padx=10, pady=6)

        # 原始文件页
        f_orig = ttk.Frame(self.file_notebook)
        self.file_notebook.add(f_orig, text="原始文件")
        fcols = ("name", "cat", "acct", "size", "mtime", "status")
        # 「微信账号」列默认隐藏，仅当扫描到多个账号时才显示
        self.tree_files = ttk.Treeview(
            f_orig, columns=fcols, show="headings", height=8,
            displaycolumns=("name", "cat", "size", "mtime", "status"))
        self.tree_files.heading("name", text="文件名")
        self.tree_files.heading("cat", text="类别")
        self.tree_files.heading("acct", text="微信账号")
        self.tree_files.heading("size", text="大小")
        self.tree_files.heading("mtime", text="修改时间")
        self.tree_files.heading("status", text="状态")
        self.tree_files.column("name", width=260)
        self.tree_files.column("cat", width=90)
        self.tree_files.column("acct", width=120)
        self.tree_files.column("size", width=90)
        self.tree_files.column("mtime", width=120)
        self.tree_files.column("status", width=90)
        vsb = ttk.Scrollbar(f_orig, orient="vertical",
                            command=self.tree_files.yview)
        self.tree_files.configure(yscrollcommand=vsb.set)
        self.tree_files.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # 归类副本页
        f_out = ttk.Frame(self.file_notebook)
        self.file_notebook.add(f_out, text="归类副本")
        ocols = ("name", "cat", "size", "mtime", "src")
        self.tree_outputs = ttk.Treeview(f_out, columns=ocols, show="headings",
                                         height=8)
        self.tree_outputs.heading("name", text="文件名")
        self.tree_outputs.heading("cat", text="类别")
        self.tree_outputs.heading("size", text="大小")
        self.tree_outputs.heading("mtime", text="修改时间")
        self.tree_outputs.heading("src", text="原始文件路径")
        self.tree_outputs.column("name", width=200)
        self.tree_outputs.column("cat", width=70)
        self.tree_outputs.column("size", width=90)
        self.tree_outputs.column("mtime", width=120)
        self.tree_outputs.column("src", width=260)
        vsb2 = ttk.Scrollbar(f_out, orient="vertical",
                             command=self.tree_outputs.yview)
        self.tree_outputs.configure(yscrollcommand=vsb2.set)
        self.tree_outputs.pack(side="left", fill="both", expand=True)
        vsb2.pack(side="right", fill="y")

        self.tree_files.bind("<Double-1>", self._on_file_double)
        self.tree_files.bind("<Button-3>", self._on_file_right)
        self.tree_outputs.bind("<Double-1>", self._on_file_double)
        self.tree_outputs.bind("<Button-3>", self._on_file_right)
        # 选中行即在内嵌面板显示图片缩略图
        self.tree_files.bind("<<TreeviewSelect>>", self._on_file_select)
        self.tree_outputs.bind("<<TreeviewSelect>>", self._on_file_select)
        self.file_menu = Menu(self.master, tearoff=0)

        # ===== 内嵌缩略图预览（选中图片文件即显示） =====
        thumb_box = ttk.LabelFrame(file_body, text="图片预览")
        thumb_box.pack(fill="x", padx=10, pady=(0, 8))
        inner = ttk.Frame(thumb_box)
        inner.pack(fill="x", padx=8, pady=6)
        self.thumb_label = tk.Label(inner, bg="#f7f7f7", width=30, height=8,
                                    relief="solid", borderwidth=1)
        self.thumb_label.pack(side="left", padx=(0, 10))
        self.thumb_info = tk.Label(
            inner,
            text="选中一个图片文件，这里会显示缩略图；双击可看大图。",
            fg="#666666", font=("Microsoft YaHei UI", 9),
            justify="left", anchor="nw", wraplength=520)
        self.thumb_info.pack(side="left", fill="both", expand=True)
        if not PIL_AVAILABLE:
            self.thumb_info.configure(
                text="未检测到图片解码库（Pillow），缩略图预览不可用；"
                     "双击图片仍可用系统默认程序打开。")

        # 进度条
        self.progress = ttk.Progressbar(step3, mode="indeterminate")
        self.progress.pack(fill="x", padx=20, pady=(4, 0))

        # 大号主按钮：开始整理
        self.organize_btn = tk.Button(
            step3, text="开始整理",
            bg="#1a73e8", fg="white", activebackground="#1558b0",
            font=("Microsoft YaHei UI", 12, "bold"), height=2,
            command=self.apply_organize, state="disabled")
        self.organize_btn.pack(fill="x", padx=20, pady=(6, 2))
        ttk.Checkbutton(
            step3, text="整理后顺便清理微信原文件（移入回收站，可恢复）",
            variable=self.clean_after).pack(anchor="w", padx=24, pady=(2, 0))
        ttk.Label(step3,
                  text="整理结果会保存到桌面上的「微信文件整理」文件夹；不勾选上面的清理，微信原文件就保持不变。",
                  foreground="#666666", font=("Microsoft YaHei UI", 9)).pack(
            anchor="w", padx=22, pady=(0, 6))

        # 整理后的次要操作
        act = ttk.Frame(step3)
        act.pack(fill="x", padx=10, pady=(0, 8))
        self.open_dest_btn = ttk.Button(
            act, text="打开整理好的文件夹", command=self.open_dest_folder)
        self.open_dest_btn.pack(side="left", padx=4)
        self.clear_btn = ttk.Button(act, text="清空整理结果",
                                    command=self.clear_output)
        self.clear_btn.pack(side="left", padx=4)
        self.del_orig_btn = ttk.Button(
            act, text="清理微信原文件（回收站）", command=self.delete_originals,
            state="disabled")
        self.del_orig_btn.pack(side="left", padx=4)
        self.update_btn = ttk.Button(
            act, text="检查更新",
            command=lambda: self._check_update(verbose=True))
        self.update_btn.pack(side="left", padx=4)

        # 重复文件查找（腾空间）
        act2 = ttk.Frame(step3)
        act2.pack(fill="x", padx=10, pady=(0, 8))
        self.dup_btn = ttk.Button(
            act2, text="查找重复文件（清理微信里重复占空间的）",
            command=self.find_duplicates)
        self.dup_btn.pack(side="left", padx=4)

        # 日志（默认折叠）
        _, log_body, _ = self._make_collapsible(
            content, "查看运行日志", default_open=False)
        self.logbox = scrolledtext.ScrolledText(
            log_body, height=6, state="disabled", wrap="word")
        self.logbox.pack(fill="both", expand=True, padx=8, pady=4)

        # 初始化整理方式示例
        self._update_scheme_preview()
        self._update_preview_count()

        # 启动后静默检查更新（仅状态栏提示，不打扰）
        self.master.after(2000, lambda: self._check_update(verbose=False))

        # 启动后自动扫描（源目录有效时才扫，让用户打开就看到结果）
        src = self.source_dir.get().strip()
        if src and os.path.isdir(src):
            self.master.after(1200, self.scan)

        # 关闭窗口时保存当前设置
        self.master.protocol("WM_DELETE_WINDOW", self._on_close)

    def log(self, msg):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", msg + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def _current_tree(self):
        """返回当前激活的标签页对应的 Treeview。"""
        try:
            idx = self.file_notebook.index(self.file_notebook.select())
        except tk.TclError:
            idx = 0
        return (self.tree_files, self.tree_outputs)[idx]

    # ---------- 浏览 ----------
    def browse_source(self):
        d = filedialog.askdirectory(title="选择微信文件目录")
        if d:
            self.source_dir.set(d)
            self.src_hint.configure(text="")
            self.organize_btn.configure(state="disabled")
            self.del_orig_btn.configure(state="disabled")
            self.tree_files.delete(*self.tree_files.get_children())
            self.tree_outputs.delete(*self.tree_outputs.get_children())
            self.file_list = []
            self.output_list = []
            self.has_organized = False
            self.copy_map = {}
            # 选完自动重新扫描，让小白立刻看到结果
            self.master.after(300, self.scan)

    def browse_dest(self):
        d = filedialog.askdirectory(title="选择归类输出目录")
        if d:
            self.dest_dir.set(d)

    # ---------- 扫描 ----------
    def scan(self):
        if self.scanning:
            return
        root = self.source_dir.get().strip()
        if not root or not os.path.isdir(root):
            messagebox.showerror("错误", "请先选择有效的微信文件目录。")
            return
        self.scanning = True
        self.scan_btn.configure(state="disabled")
        self.organize_btn.configure(state="disabled")
        self.progress.start()
        self.status.set("正在扫描...")
        self.log("开始扫描: " + root)

        # 未检测到传统结构时，自动启用兼容模式
        if not discover_accounts(root) and not self.deep_scan.get():
            self.deep_scan.set(True)
            self.log("[INFO] 未检测到传统 FileStorage/File 结构，已自动启用兼容模式（递归扫描）。")

        # 多微信账号合并扫描：把电脑上探测到的其他微信根目录一起纳入
        roots = [root]
        if self.merge_accounts.get():
            for d in find_all_wechat_dirs():
                if os.path.normpath(d).lower() != os.path.normpath(root).lower():
                    roots.append(d)
        self.roots = roots
        if len(roots) > 1:
            self.log("[INFO] 多账号合并扫描已开启，共 %d 个微信目录：" % len(roots))
            for d in roots:
                self.log("       - " + d)
        else:
            self.log("[INFO] 未发现其他微信账号目录，只扫描当前选择的文件夹。")

        threading.Thread(target=self._do_scan, args=(roots,), daemon=True).start()

    def _do_scan(self, roots):
        """扫描一个或多个微信根目录，合并结果并标注每个文件所属账号。"""
        if isinstance(roots, str):
            roots = [roots]
        if not roots:
            roots = [""]
        recursive = self.deep_scan.get()
        self.log("[INFO] 扫描模式：%s" % ("扫描整个文件夹" if recursive
                                       else "只整理微信收到的文件"))

        files = []
        seen = set()
        owner = {}          # 文件路径 -> 所属根目录（用于判定账号名）

        def _merge(paths, bucket):
            got = 0
            for p in paths:
                key = os.path.normpath(p).lower()
                if key in seen:
                    continue
                seen.add(key)
                bucket.append(p)
                owner[p] = root
                got += 1
            return got

        for root in roots:
            got = collect_sources(root, include_media=False, scan_all=False,
                                  recursive=recursive)
            self.log("[INFO]   %s -> %d 个文件" % (root, _merge(got, files)))

        raw_count = len(files)
        files = self._apply_time_filter(files)
        after_time = len(files)
        files = self._apply_size_filter(files)
        after_size = len(files)

        # 收集微信加密内部文件（收藏/附件），这些不计入『可整理』统计，单独成类
        enc = []
        for root in roots:
            _merge(collect_wechat_internal(root), enc)
        enc_count = len(enc)

        if not files and not enc:
            self.master.after(0, self._on_scan_empty, roots, raw_count,
                              after_time, after_size, enc_count)
            return

        default_root = roots[0]

        def _rec(p, cat, encrypted):
            st = os.stat(p)
            return {
                "path": p, "cat": cat, "month": month_of(p),
                "size": st.st_size, "hash": sha256_of(p), "mtime": st.st_mtime,
                "encrypted": encrypted,
                "account": account_label_of(p, owner.get(p, default_root)),
            }

        records = []
        for p in files:
            try:
                records.append(_rec(p, cat_of(p), False))
            except OSError:
                continue
        for p in enc:
            try:
                records.append(_rec(p, ENCRYPTED_CAT, True))
            except OSError:
                continue
        self.records = records
        self.master.after(0, self._on_scan_done, roots, enc_count)

    def _on_scan_empty(self, roots, raw_count=0, after_time=0, after_size=0,
                       enc_count=0):
        root = roots[0] if isinstance(roots, (list, tuple)) and roots else \
            (roots if isinstance(roots, str) else "")
        self.scanning = False
        self.progress.stop()
        self.scan_btn.configure(state="normal")
        self.organize_btn.configure(state="normal")
        self.status.set("未发现可归类的文件")

        # 把无结果原因显示得更具体，方便用户自查
        if raw_count == 0:
            detail = "该目录下没找到可整理的非系统文件。"
        elif after_time == 0:
            detail = "时间筛选把文件都排除了，试试把时间范围改成「全部」。"
        elif after_size == 0:
            detail = "大小筛选把文件都排除了，试试把文件大小改成「全部」。"
        else:
            detail = "文件可能被微信系统扩展名过滤。"
        self.preview_var.set("没有发现可以整理的文件（%s）" % detail)
        self.log("[SKIP] 未发现可归类的文件。")
        self.log("[INFO] 扫描统计：原始命中 %d → 时间筛选后 %d → 大小筛选后 %d"
                 % (raw_count, after_time, after_size))
        self.log("提示：%s" % detail)
        self.log("      如果微信文件在新版自定义目录，请展开「高级选项」选择「扫描整个文件夹」。")
        self.log("      当前扫描目录：%s" % root)

    def _on_scan_done(self, roots, enc_count=0):
        self.scanning = False
        self.progress.stop()
        self.scan_btn.configure(state="normal")
        self.organize_btn.configure(state="normal")
        self.del_orig_btn.configure(state="normal")

        # 多账号：扫描到多个账号时才显示「微信账号」列
        accounts = []
        for r in self.records:
            a = r.get("account") or ""
            if a and a not in accounts:
                accounts.append(a)
        if len(accounts) > 1:
            self.tree_files.configure(
                displaycolumns=("name", "cat", "acct", "size", "mtime",
                                "status"))
            self.acct_hint.configure(
                text="已合并扫描 %d 个微信账号：%s（文件清单里可看到每个文件属于哪个账号）"
                     % (len(accounts), "、".join(accounts)))
        else:
            self.tree_files.configure(
                displaycolumns=("name", "cat", "size", "mtime", "status"))
            self.acct_hint.configure(text="")

        records = self.records
        total = len(records)
        total_size = sum(r["size"] for r in records)
        by_cat = {}
        for r in records:
            c = by_cat.setdefault(r["cat"], [0, 0])
            c[0] += 1
            c[1] += r["size"]
        groups = {}
        for r in records:
            groups.setdefault(r["hash"], []).append(r)
        dupes = {h: rs for h, rs in groups.items() if len(rs) > 1}
        dup_count = sum(len(rs) - 1 for rs in dupes.values())
        dup_recover = sum(max(x["size"] for x in rs) * (len(rs) - 1)
                          for rs in dupes.values())

        # 类别汇总（仅用于日志，界面用预览文字代替）
        self._populate_file_list(records)
        self._populate_output_list([])

        self._update_preview_count()

        self.log("扫描完成: 共 %d 个文件，总大小 %s" % (total, human(total_size)))
        if len(accounts) > 1:
            self.log("按微信账号: " + "，".join(
                "%s %d" % (a, sum(1 for r in records if r.get("account") == a))
                for a in accounts))
        self.log("按类型: " + "，".join(
            "%s %d" % (k, v[0]) for k, v in sorted(by_cat.items(),
                                                   key=lambda kv: -kv[1][1])))
        if dup_count:
            self.log("发现重复文件 %d 个，去重可节省 %s（勾选「去重」后归类会跳过重复项）"
                     % (dup_count, human(dup_recover)))
        if enc_count:
            self.log("另检测到 %d 个微信加密文件（收藏/附件），已被微信私有加密存储，"
                     "原文件名与内容均无法直接读取；如需备份请在「②」勾选「微信加密文件」"
                     "（复制后可能无法直接打开）。" % enc_count)
        self.log("预览就绪。点击「开始整理」将勾选类别复制到输出目录（源文件不动）。")
        self.status.set("扫描完成，可整理")

    def _status_of(self, rec):
        if rec["path"] in self.copy_map and os.path.exists(self.copy_map[rec["path"]]):
            return "已归类"
        return "待归类"

    def _populate_file_list(self, records):
        self.tree_files.delete(*self.tree_files.get_children())
        # 只显示已勾选类别的文件；未勾选的类别（如微信加密文件）不进入清单，
        # 保证清单与「开始整理」实际会复制的范围一致。
        visible = [r for r in records if self.cat_enabled[r["cat"]].get()]
        self.file_list = visible
        for i, r in enumerate(visible):
            try:
                mt = datetime.fromtimestamp(r["mtime"]).strftime("%Y-%m-%d %H:%M")
            except OSError:
                mt = "未知"
            name = os.path.basename(r["path"])
            if r.get("encrypted"):
                # 加密文件无原文件名，用哈希名占位并明确标注
                name = "%s（微信加密）" % name
            self.tree_files.insert(
                "", "end", iid=str(i),
                values=(name, r["cat"], r.get("account", ""),
                        human(r["size"]), mt, self._status_of(r)))

    def _populate_output_list(self, outputs):
        self.tree_outputs.delete(*self.tree_outputs.get_children())
        self.output_list = list(outputs)
        for i, r in enumerate(outputs):
            try:
                mt = datetime.fromtimestamp(r["mtime"]).strftime("%Y-%m-%d %H:%M")
            except OSError:
                mt = "未知"
            self.tree_outputs.insert(
                "", "end", iid="out-%d" % i,
                values=(os.path.basename(r["dst"]), r["cat"],
                        human(r["size"]), mt, r["src"]))

    def _encrypted_explain_text(self):
        return (
            "这是微信的「加密存储」文件，不是普通文件：\n\n"
            "· 文件名被替换成一串哈希，看不到原来的名字\n"
            "· 文件内容被微信私有加密，双击、复制出来都打不开\n\n"
            "为什么工具还会把它列出来？\n"
            "只是为了让你「看到」微信里存了哪些文件，方便你判断。\n\n"
            "想真正使用这些文件，正确做法：\n"
            "1. 打开电脑版微信 → 文件管理 / 聊天记录\n"
            "2. 找到对应文件，右键 → 「另存为」到桌面或普通文件夹\n"
            "3. 微信会自动解密并还原文件名\n"
            "4. 另存出来的文件，再用本工具整理即可\n\n"
            "也可以先「复制文件路径」，去微信里对照定位。")

    def _on_encrypted_toggle(self):
        # 勾选「微信加密文件」时提示一次说明；取消勾选不提示
        if self.cat_enabled[ENCRYPTED_CAT].get() and not self._enc_warned:
            self._enc_warned = True
            messagebox.showinfo(
                "关于「微信加密文件」", self._encrypted_explain_text())
        self._refresh_will()

    def _explain_encrypted(self):
        messagebox.showinfo("这是什么文件？", self._encrypted_explain_text())

    def _refresh_will(self):
        # 勾选变化会影响「哪些文件进入清单」，因此整表重刷（而非只改某一列）
        self._populate_file_list(self.records)
        self._update_preview_count()

    def _refresh_original_list(self):
        """删除原始文件后刷新列表，移除已不存在的项。"""
        self.file_list = [r for r in self.file_list if os.path.exists(r["path"])]
        self._populate_file_list(self.file_list)

    def _refresh_output_list(self):
        """删除归类副本后刷新列表，移除已不存在的项。"""
        self.output_list = [r for r in self.output_list if os.path.exists(r["dst"])]
        self._populate_output_list(self.output_list)

    def _populate_output_list_from_copy_map(self):
        outputs = []
        for src, dst in self.copy_map.items():
            if not os.path.exists(dst):
                continue
            try:
                st = os.stat(dst)
                outputs.append({
                    "src": src, "dst": dst, "cat": cat_of(dst),
                    "size": st.st_size, "mtime": st.st_mtime,
                })
            except OSError:
                continue
        self._populate_output_list(outputs)

    def _update_preview_count(self):
        """更新 ③ 处的预览文字与文件清单标题。"""
        if not getattr(self, "records", None):
            self.preview_var.set("（请先扫描微信文件夹）")
            if getattr(self, "file_list_title", None) is not None:
                self.file_list_title.set("查看文件清单")
            return
        will_records = [r for r in self.records if self.cat_enabled[r["cat"]].get()]
        will = len(will_records)
        total_size = sum(r["size"] for r in will_records)
        self.preview_var.set(
            "将整理 %d 个文件，共 %s（已按你勾选的类型）" % (will, human(total_size)))
        if getattr(self, "file_list_title", None) is not None:
            self.file_list_title.set("查看文件清单（%d 个）" % will)

    # ---------- 归类 ----------
    def apply_organize(self):
        if not self.records:
            return
        dest = self.dest_dir.get().strip()
        if not dest:
            messagebox.showerror("错误", "请先选择输出目录。")
            return
        enabled = [r for r in self.records if self.cat_enabled[r["cat"]].get()]
        if not enabled:
            messagebox.showwarning("提示", "没有勾选任何类别，无法整理。")
            return
        n = len(enabled)
        if self.clean_after.get():
            msg = ("即将把 %d 个文件（已勾选类别）复制到:\n%s\n\n"
                   "整理完成后，会把【已成功复制的】微信原始文件移入回收站"
                   "（可在回收站恢复），释放微信占用的空间。\n是否继续？"
                   % (n, dest))
        else:
            msg = ("即将把 %d 个文件（已勾选类别）复制到:\n%s\n\n"
                   "微信里的原文件不会被删除或移动（仅复制）。\n是否继续？"
                   % (n, dest))
        ans = messagebox.askyesno("确认整理", msg)
        if not ans:
            return
        self.scan_btn.configure(state="disabled")
        self.organize_btn.configure(state="disabled")
        self.progress.start()
        self.status.set("正在整理...")
        self.log("开始整理到: " + dest)
        threading.Thread(target=self._do_apply, args=(dest,), daemon=True).start()

    def _do_apply(self, dest):
        label = self.scheme.get()
        dedupe = self.dedupe.get()
        os.makedirs(dest, exist_ok=True)
        used = set()
        seen_hash = set()
        copied = skipped_dup = skipped_cat = verify_failed = 0
        self.copy_map = {}
        for r in self.records:
            if not self.cat_enabled[r["cat"]].get():
                skipped_cat += 1
                continue
            if dedupe and r["hash"] in seen_hash:
                skipped_dup += 1
                continue
            seen_hash.add(r["hash"])
            fname = os.path.basename(r["path"])
            rel = preset_rel(SCHEME_LABELS.get(label, "type"), r["cat"],
                             r["month"], fname)
            dp = dest_path(dest, rel, used)
            try:
                os.makedirs(os.path.dirname(dp), exist_ok=True)
                shutil.copy2(r["path"], dp)
            except OSError as e:
                self.master.after(0, lambda m=str(e): self.log("[WARN] 复制失败: " + m))
                continue
            # 副本完整性校验：核对复制前后内容哈希，确保副本可安全使用
            if sha256_of(dp) != r["hash"]:
                try:
                    os.remove(dp)
                except OSError:
                    pass
                verify_failed += 1
                self.master.after(0, lambda f=fname: self.log(
                    "[WARN] 副本校验未通过（已丢弃，不影响源文件）: " + f))
                continue
            self.copy_map[r["path"]] = dp
            copied += 1
        self.has_organized = True
        self._write_report(dest)
        # 一键归档+清理：勾选后，把已校验复制成功的原始文件移入回收站
        cleaned = 0
        released = 0
        if self.clean_after.get() and self.copy_map:
            srcs = [src for src, dst in self.copy_map.items()
                    if os.path.exists(src) and os.path.exists(dst)]
            if srcs:
                sizes = {src: os.path.getsize(src) for src in srcs
                         if os.path.exists(src)}
                ok, failures = send_to_recycle_bin(srcs)
                cleaned = ok
                released = sum(sz for src, sz in sizes.items()
                               if not os.path.exists(src))
                for p, reason in failures:
                    self.master.after(
                        0, lambda p=p, reason=reason: self.log(
                            "[WARN] 清理原始文件失败: " + p + " (" + reason + ")"))
        self.master.after(0, self._on_apply_done, dest, copied,
                          skipped_dup, skipped_cat, verify_failed,
                          cleaned, released)

    def _write_report(self, dest):
        """在输出目录写入整理清单 CSV，方便用户核对文件去向。"""
        if not self.copy_map:
            return
        import csv
        # 多账号合并扫描时，清单里一并记录每个文件来自哪个微信账号
        acct_of = {}
        for r in (getattr(self, "records", None) or []):
            acct_of[os.path.normpath(r["path"]).lower()] = r.get("account", "")
        path = os.path.join(dest, "整理清单.csv")
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["文件名", "类别", "微信账号", "大小",
                            "原始路径", "整理后路径"])
                for src, dst in self.copy_map.items():
                    if not os.path.exists(dst):
                        continue
                    w.writerow([
                        os.path.basename(dst),
                        cat_of(dst),
                        acct_of.get(os.path.normpath(src).lower(), ""),
                        human(os.path.getsize(dst)),
                        src,
                        dst,
                    ])
            self.master.after(
                0, lambda: self.log("[INFO] 已生成整理清单: " + path))
        except OSError as e:
            self.master.after(0, lambda m=str(e): self.log("[WARN] 写入整理清单失败: " + m))

    def _on_apply_done(self, dest, copied, skipped_dup, skipped_cat,
                       verify_failed=0, cleaned=0, released=0):
        self.progress.stop()
        self.scan_btn.configure(state="normal")
        self.organize_btn.configure(state="normal")
        self.del_orig_btn.configure(state="normal")
        self._populate_output_list_from_copy_map()
        if cleaned:
            self._refresh_original_list()   # 已清理的原始文件从清单移除
        else:
            self._populate_file_list(self.file_list)  # 刷新「状态」列
        self.file_notebook.select(1)              # 归类完成自动切换到「归类副本」
        self._update_preview_count()
        self.status.set("整理完成")
        self.log("[OK] 已整理完成，复制 %d 个文件到: %s" % (copied, dest))
        if skipped_dup:
            self.log("      去重跳过 %d 个重复文件" % skipped_dup)
        if skipped_cat:
            self.log("      因未勾选类别跳过 %d 个文件" % skipped_cat)
        if verify_failed:
            self.log("      %d 个文件副本校验未通过，已丢弃（源文件未受影响）" % verify_failed)
        msg = "已复制 %d 个文件到:\n%s" % (copied, dest)
        if cleaned:
            self.log("[OK] 已清理 %d 个微信原始文件（回收站），释放空间 %s"
                     % (cleaned, human(released)))
            msg += "\n\n已清理 %d 个微信原始文件（回收站）\n释放空间 %s" % (
                cleaned, human(released))
        messagebox.showinfo("完成", msg)
        # 归类完成后自动打开输出文件夹，让用户立刻看到结果
        try:
            if os.path.isdir(dest):
                os.startfile(dest)
        except Exception as e:
            self.log("[WARN] 无法自动打开输出文件夹: " + str(e))

    # ---------- 预览 / 打开 ----------
    def _rec_from_iid(self, iid, tree=None):
        if tree is None:
            tree = self._current_tree()
        try:
            if tree is self.tree_outputs:
                idx = int(iid.split("-", 1)[1])
                return self.output_list[idx]
            else:
                idx = int(iid)
                return self.file_list[idx]
        except (ValueError, IndexError):
            return None

    def _on_file_double(self, event):
        tree = self._current_tree()
        sel = tree.selection()
        if not sel:
            return
        rec = self._rec_from_iid(sel[0], tree)
        if not rec:
            return
        if rec.get("encrypted"):
            self._explain_encrypted()
            return
        path = rec["dst"] if tree is self.tree_outputs else rec["path"]
        self._preview_file(path)

    def _preview_file(self, path):
        if not os.path.exists(path):
            messagebox.showerror("错误", "文件不存在：\n" + path)
            return
        ext = os.path.splitext(path)[1].lower()
        if ext in IMAGE_PREVIEW_EXTS:
            self._show_image_preview(path)
        else:
            # 其他类型用系统默认程序打开（查看）
            try:
                os.startfile(path)
            except Exception as e:
                self.log("[WARN] 无法打开预览: " + str(e))

    # ---------- 图片缩略图 / 大图预览 ----------
    def _open_with_system(self, path):
        try:
            os.startfile(path)
        except Exception as e:
            self.log("[WARN] 无法用系统程序打开: " + str(e))

    def _open_folder_of(self, path):
        try:
            os.startfile(os.path.dirname(path))
        except Exception as e:
            self.log("[WARN] 无法打开所在文件夹: " + str(e))

    def _load_image(self, path):
        """用 Pillow 读取图片，失败返回 None。"""
        if not PIL_AVAILABLE:
            return None
        try:
            im = Image.open(path)
            im.load()
            return im
        except Exception:
            return None

    @staticmethod
    def _fit_size(w, h, max_w, max_h):
        if w <= 0 or h <= 0:
            return max_w, max_h
        ratio = min(max_w / float(w), max_h / float(h), 1.0)
        return max(1, int(w * ratio)), max(1, int(h * ratio))

    def _clear_thumb(self, msg="选中一个图片文件，这里会显示缩略图；双击可看大图。"):
        self._thumb_image = None
        try:
            self.thumb_label.configure(image="", text="缩略图", bg="#f7f7f7")
            self.thumb_info.configure(text=msg, fg="#666666")
        except tk.TclError:
            pass

    def _on_file_select(self, event=None):
        """清单选中行变化时，内嵌面板显示图片缩略图。"""
        if not PIL_AVAILABLE:
            return
        tree = getattr(event, "widget", None)
        if tree not in (self.tree_files, self.tree_outputs):
            tree = self._current_tree()
        sel = tree.selection()
        if not sel:
            self._clear_thumb()
            return
        rec = self._rec_from_iid(sel[0], tree)
        if not rec:
            self._clear_thumb()
            return
        path = rec.get("dst") if tree is self.tree_outputs else rec.get("path")
        if not path or not os.path.exists(path):
            self._clear_thumb("文件不存在（可能已被清理）。")
            return
        if rec.get("encrypted"):
            self._clear_thumb("这是微信加密文件，无法预览。\n"
                              "请到微信里「另存为」后再查看。")
            return
        ext = os.path.splitext(path)[1].lower()
        if ext not in IMAGE_PREVIEW_EXTS:
            self._clear_thumb(
                "这是「%s」文件，没有缩略图。\n双击可用系统默认程序打开。"
                % (rec.get("cat") or "非图片"))
            return
        try:
            size = os.path.getsize(path)
            mtime = int(os.path.getmtime(path))
        except OSError:
            self._clear_thumb()
            return
        if size > THUMB_MAX_BYTES:
            self._clear_thumb("图片太大（%s），跳过缩略图。\n"
                              "双击可用系统默认程序打开。" % human(size))
            return
        try:
            mt = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            mt = "未知"

        key = (path, mtime, size)
        cached = self._thumb_cache.get(key)
        if cached is not None:
            img, info = cached
        else:
            im = self._load_image(path)
            if im is None:
                self._clear_thumb("这个图片无法解码预览。\n"
                                  "双击试试用系统默认程序打开。")
                return
            w, h = im.size
            tw, th = self._fit_size(w, h, 220, 160)
            try:
                thumb = im.copy()
                thumb.thumbnail((tw, th))
                img = ImageTk.PhotoImage(thumb)
            except Exception:
                self._clear_thumb("这个图片无法解码预览。\n"
                                  "双击试试用系统默认程序打开。")
                return
            info = ("%s\n原始尺寸: %d × %d\n大小: %s\n修改时间: %s"
                    % (os.path.basename(path), w, h, human(size), mt))
            if len(self._thumb_cache) >= THUMB_CACHE_MAX:
                try:
                    self._thumb_cache.pop(next(iter(self._thumb_cache)))
                except (StopIteration, KeyError):
                    pass
            self._thumb_cache[key] = (img, info)

        self._thumb_image = img      # 保持引用，防止被 Python 垃圾回收
        self.thumb_label.configure(image=img, text="", bg="white")
        self.thumb_info.configure(text=info, fg="#333333")

    def _show_image_preview(self, path):
        """双击图片：弹出适配屏幕的大图预览（Pillow 解码，支持 jpg/png/webp）。"""
        win = tk.Toplevel(self.master)
        win.title("预览: " + os.path.basename(path))
        photo = None
        info = ""
        if PIL_AVAILABLE:
            im = self._load_image(path)
            if im is not None:
                w, h = im.size
                tw, th = self._fit_size(w, h,
                                        min(900, win.winfo_screenwidth() - 120),
                                        min(700, win.winfo_screenheight() - 160))
                try:
                    shown = im.copy()
                    shown.thumbnail((tw, th))
                    photo = ImageTk.PhotoImage(shown)
                except Exception:
                    photo = None
                info = "%d × %d　%s" % (w, h, human(os.path.getsize(path)))
        if photo is None:
            # 无 Pillow 或解码失败：回退 Tk 原生格式，再不行用系统程序
            ext = os.path.splitext(path)[1].lower()
            if ext in TK_NATIVE_IMAGE_EXTS:
                try:
                    photo = tk.PhotoImage(file=path)
                except Exception:
                    photo = None
            if photo is None:
                win.destroy()
                self._open_with_system(path)
                return
        lbl = ttk.Label(win, image=photo)
        lbl.image = photo            # 保持引用
        lbl.pack(padx=10, pady=(10, 4))
        if info:
            ttk.Label(win, text=info, foreground="#666666").pack(pady=(0, 4))
        bar = ttk.Frame(win)
        bar.pack(pady=(0, 10))
        ttk.Button(bar, text="用系统程序打开",
                   command=lambda: self._open_with_system(path)).pack(
            side="left", padx=4)
        ttk.Button(bar, text="打开所在文件夹",
                   command=lambda: self._open_folder_of(path)).pack(
            side="left", padx=4)
        ttk.Button(bar, text="关闭", command=win.destroy).pack(
            side="left", padx=4)
        win.geometry("%dx%d" % (photo.width() + 48, photo.height() + 120))
        win.transient(self.master)
        win.focus_set()

    def _on_file_right(self, event):
        tree = self._current_tree()
        iid = tree.identify_row(event.y)
        if iid:
            tree.selection_set(iid)
            self._build_context_menu(tree)
            self.file_menu.post(event.x_root, event.y_root)

    def _build_context_menu(self, tree):
        self.file_menu.delete(0, "end")
        sel = tree.selection()
        rec = self._rec_from_iid(sel[0], tree) if sel else None
        encrypted = bool(rec and rec.get("encrypted"))
        if encrypted:
            self.file_menu.add_command(label="这是什么？如何正确打开",
                                       command=self._explain_encrypted)
        else:
            self.file_menu.add_command(label="打开 / 预览", command=self._menu_open)
        self.file_menu.add_command(label="打开所在文件夹", command=self._menu_open_folder)
        self.file_menu.add_command(label="复制文件路径", command=self._menu_copy_path)
        self.file_menu.add_separator()
        if tree is self.tree_outputs:
            self.file_menu.add_command(label="删除此归类副本",
                                       command=self._menu_delete_one)
        else:
            self.file_menu.add_command(label="删除此原始文件（回收站）",
                                       command=self._menu_delete_original_one)

    def _menu_open(self):
        tree = self._current_tree()
        sel = tree.selection()
        if not sel:
            return
        rec = self._rec_from_iid(sel[0], tree)
        if not rec:
            return
        if rec.get("encrypted"):
            self._explain_encrypted()
            return
        path = rec["dst"] if tree is self.tree_outputs else rec["path"]
        self._preview_file(path)

    def _menu_open_folder(self):
        tree = self._current_tree()
        sel = tree.selection()
        if not sel:
            return
        rec = self._rec_from_iid(sel[0], tree)
        if not rec:
            return
        path = rec["dst"] if tree is self.tree_outputs else rec["path"]
        d = os.path.dirname(path)
        try:
            os.startfile(d)
        except Exception as e:
            self.log("[WARN] 无法打开文件夹: " + str(e))

    def _menu_copy_path(self):
        tree = self._current_tree()
        sel = tree.selection()
        if not sel:
            return
        rec = self._rec_from_iid(sel[0], tree)
        if not rec:
            return
        path = rec["dst"] if tree is self.tree_outputs else rec["path"]
        try:
            self.master.clipboard_clear()
            self.master.clipboard_append(path)
            self.status.set("已复制文件路径到剪贴板")
        except Exception as e:
            self.log("[WARN] 复制路径失败: " + str(e))

    # ---------- 删除已归类副本（仅输出目录） ----------
    def _menu_delete_one(self):
        tree = self._current_tree()
        sel = tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选择要删除的归类副本。")
            return
        rec = self._rec_from_iid(sel[0], tree)
        if not rec:
            return
        dst = rec["dst"]
        if not os.path.exists(dst):
            messagebox.showinfo("提示", "该归类副本已不存在。")
            self._refresh_output_list()
            return
        ans = messagebox.askyesno(
            "删除归类副本",
            "将删除输出目录中的副本（不影响微信源文件）:\n%s\n\n是否继续？" % dst)
        if not ans:
            return
        try:
            os.remove(dst)
            self.log("[DEL] 已删除归类副本: " + dst)
            self.status.set("已删除一个归类副本")
            self._refresh_output_list()
        except OSError as e:
            self.log("[WARN] 删除失败: " + str(e))

    def clear_output(self):
        dest = self.dest_dir.get().strip()
        if not dest or not os.path.isdir(dest):
            messagebox.showinfo("提示", "输出文件夹不存在或尚未整理。")
            return
        files = []
        for dp, _, fnames in os.walk(dest):
            for fn in fnames:
                files.append(os.path.join(dp, fn))
        if not files:
            messagebox.showinfo("提示", "输出文件夹为空，无需清理。")
            return
        ans = messagebox.askyesno(
            "确认清空整理结果",
            "将删除以下文件夹内的全部 %d 个文件（仅输出目录，不影响微信源文件）:\n%s\n\n此操作不可恢复！是否继续？"
            % (len(files), dest))
        if not ans:
            return
        removed = 0
        for f in files:
            try:
                os.remove(f)
                removed += 1
            except OSError as e:
                self.log("[WARN] 删除失败: " + str(e))
        self.copy_map = {k: v for k, v in self.copy_map.items() if os.path.exists(v)}
        self._refresh_output_list()
        self.log("[DEL] 已清空整理结果，删除 %d 个文件: %s" % (removed, dest))
        self.status.set("已清空整理结果（%d 个文件）" % removed)
        messagebox.showinfo("完成", "已删除 %d 个归类副本文件。" % removed)

    # ---------- 重复文件查找 ----------
    def find_duplicates(self):
        root = self.source_dir.get().strip()
        if not root or not os.path.isdir(root):
            messagebox.showerror("错误", "请先选择微信文件夹。")
            return
        self.dup_btn.configure(state="disabled")
        self.progress.start()
        self.status.set("正在查找重复文件...")
        self.log("[INFO] 开始查找重复文件（扫描整个文件夹，文件多时可能稍慢）...")
        threading.Thread(target=self._do_find_duplicates, args=(root,),
                         daemon=True).start()

    def _do_find_duplicates(self, root):
        from collections import defaultdict
        files = collect_sources(root, recursive=True)
        groups = defaultdict(list)
        total = 0
        for p in files:
            try:
                st = os.stat(p)
                if st.st_size == 0:
                    continue
            except OSError:
                continue
            h = sha256_of(p)
            if h == "ERR":
                continue
            groups[h].append({"path": p, "size": st.st_size,
                              "mtime": st.st_mtime})
            total += 1
        dups = {h: v for h, v in groups.items() if len(v) > 1}
        self.master.after(0, self._show_duplicates, dups, total)

    def _show_duplicates(self, dups, total):
        self.progress.stop()
        self.dup_btn.configure(state="normal")
        if not dups:
            self.status.set("未发现重复文件")
            self.log("[OK] 重复查找完成：扫描 %d 个文件，未发现重复。" % total)
            messagebox.showinfo("查找重复文件",
                                "扫描 %d 个文件，未发现重复。" % total)
            return
        n_dup_files = sum(len(v) for v in dups.values())
        reclaim = sum(v[0]["size"] * (len(v) - 1) for v in dups.values())
        self.status.set("发现 %d 组重复文件" % len(dups))
        self.log("[OK] 重复查找完成：%d 组重复（%d 个文件），最多可释放约 %s"
                 % (len(dups), n_dup_files, human(reclaim)))

        win = tk.Toplevel(self.master)
        win.title("重复文件查找结果")
        win.geometry("880x640")
        win.minsize(720, 480)
        win.transient(self.master)

        top = tk.Frame(win, bg="#1a73e8")
        top.pack(fill="x")
        tk.Label(top,
                 text="发现 %d 组重复文件，勾选后可释放约 %s"
                 % (len(dups), human(reclaim)),
                 bg="#1a73e8", fg="white",
                 font=("Microsoft YaHei UI", 13, "bold"),
                 anchor="w", padx=16).pack(anchor="w", pady=(12, 2))
        tk.Label(top,
                 text="每组默认保留一个（标 ● 保留），其余默认勾选删除；删除是移入回收站，可恢复。",
                 bg="#1a73e8", fg="#e8f0fe",
                 font=("Microsoft YaHei UI", 9),
                 anchor="w", padx=16).pack(anchor="w", pady=(0, 12))

        canvas = tk.Canvas(win, highlightthickness=0)
        scroll = ttk.Scrollbar(win, orient="vertical", command=canvas.yview)
        body = ttk.Frame(canvas)
        body.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        win_id = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)

        def _sync(e=None):
            w = canvas.winfo_width()
            if w > 1:
                canvas.itemconfigure(win_id, width=w)
        canvas.bind("<Configure>", _sync)
        canvas.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        scroll.pack(side="right", fill="y", pady=8)

        vars_map = {}
        ordered = sorted(dups.items(), key=lambda kv: -kv[1][0]["size"])
        for gi, (_, items) in enumerate(ordered):
            items.sort(key=lambda x: -x["mtime"])  # 最新在前，保留第一个
            grp = ttk.LabelFrame(
                body,
                text="重复组 %d：每个 %s，共 %d 个"
                % (gi + 1, human(items[0]["size"]), len(items)))
            grp.pack(fill="x", padx=8, pady=6)
            for i, it in enumerate(items):
                v = tk.BooleanVar(value=(i > 0))
                vars_map[it["path"]] = v
                row = ttk.Frame(grp)
                row.pack(fill="x", padx=10, pady=4)
                if i == 0:
                    ttk.Label(row, text="● 保留", foreground="#1a7f37",
                              font=("Microsoft YaHei UI", 9, "bold")).pack(
                        anchor="w")
                else:
                    ttk.Checkbutton(row, text="删除", variable=v).pack(
                        anchor="w")
                ttk.Label(row, text=os.path.basename(it["path"]),
                          font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
                ttk.Label(row, text=it["path"], foreground="#999999",
                          font=("Microsoft YaHei UI", 8)).pack(anchor="w")

        def do_delete():
            targets = [p for p, v in vars_map.items() if v.get()]
            if not targets:
                messagebox.showinfo("提示", "没有勾选要删除的文件。")
                return
            ans = messagebox.askyesno(
                "确认删除（回收站）",
                "将把 %d 个重复文件移入回收站（可恢复）。\n是否继续？"
                % len(targets))
            if not ans:
                return
            sizes = {p: os.path.getsize(p) for p in targets
                     if os.path.exists(p)}
            ok, failures = send_to_recycle_bin(targets)
            released = sum(sz for p, sz in sizes.items()
                           if not os.path.exists(p))
            self.log("[OK] 重复清理：%d / %d 个移入回收站，释放空间 %s"
                     % (ok, len(targets), human(released)))
            for p, reason in failures:
                self.log("[WARN] 删除重复文件失败: " + p + " (" + reason + ")")
            self.status.set("已清理 %d 个重复文件，释放 %s"
                            % (ok, human(released)))
            win.destroy()
            if failures:
                messagebox.showwarning(
                    "部分完成",
                    "已清理 %d 个重复文件，释放 %s，%d 个失败（详见日志）。"
                    % (ok, human(released), len(failures)))
            else:
                messagebox.showinfo(
                    "完成",
                    "已清理 %d 个重复文件，释放空间 %s。" % (ok, human(released)))
            # 重新扫描，刷新文件清单
            if self.source_dir.get().strip() and \
                    os.path.isdir(self.source_dir.get().strip()):
                self.master.after(500, self.scan)

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=12, pady=10)
        ttk.Button(bar, text="删除勾选的重复文件（回收站）",
                   command=do_delete).pack(side="left", padx=4)
        ttk.Button(bar, text="关闭", command=win.destroy).pack(side="right", padx=4)

    # ---------- 删除原始（源）文件：仅清理已成功复制过的，移入回收站 ----------
    def _verified_original_pairs(self):
        """返回 [(src, dst), ...]，其中 src 仍存在且 dst 已成功复制（可安全清理）。"""
        pairs = []
        for src, dst in self.copy_map.items():
            if os.path.exists(src) and os.path.exists(dst):
                pairs.append((src, dst))
        return pairs

    def _menu_delete_original_one(self):
        tree = self._current_tree()
        sel = tree.selection()
        if not sel:
            return
        rec = self._rec_from_iid(sel[0], tree)
        if not rec:
            return
        src = rec["path"]
        if not os.path.exists(src):
            messagebox.showinfo("提示", "原始文件已不存在：\n" + src)
            self._refresh_original_list()
            return
        dst = self.copy_map.get(src)
        has_copy = dst and os.path.exists(dst)
        if has_copy:
            msg = ("将把微信【原始文件】移入回收站（可在回收站恢复），输出目录中的副本会保留：\n%s\n\n"
                   "大小：%s\n\n是否继续？") % (src, human(os.path.getsize(src)))
        else:
            msg = ("【注意】该文件尚未整理（没有输出目录副本）。\n"
                   "仍要把微信【原始文件】移入回收站（可在回收站恢复）：\n%s\n\n"
                   "大小：%s\n\n是否继续？") % (src, human(os.path.getsize(src)))
        ans = messagebox.askyesno("删除原始文件（回收站）", msg)
        if not ans:
            return
        ok, failures = send_to_recycle_bin([src])
        if ok:
            self.log("[DEL-ORIG] 已移入回收站: " + src)
            self.status.set("已清理 1 个原始文件（回收站）")
            self._refresh_original_list()
        else:
            for p, r in failures:
                self.log("[WARN] 删除原始文件失败: " + p + " (" + r + ")")

    def delete_originals(self):
        files = [r["path"] for r in self.file_list if os.path.exists(r["path"])]
        if not files:
            messagebox.showinfo("提示", "当前没有可清理的原始文件。")
            return
        pairs = self._verified_original_pairs()
        copied_count = len(pairs)
        total = sum(os.path.getsize(p) for p in files)
        if copied_count:
            msg = ("即将把 %d 个微信【原始文件】移入回收站（可在回收站里恢复），\n"
                   "其中 %d 个已有归类副本（输出目录副本会保留），\n"
                   "%d 个尚未整理。\n\n"
                   "文件总大小：%s\n\n是否继续？"
                   % (len(files), copied_count, len(files) - copied_count, human(total)))
        else:
            msg = ("即将把 %d 个微信【原始文件】移入回收站（可在回收站里恢复）。\n"
                   "这些文件都尚未整理（没有输出目录副本）。\n\n"
                   "文件总大小：%s\n\n是否继续？"
                   % (len(files), human(total)))
        ans = messagebox.askyesno("清理原始文件（回收站）", msg)
        if not ans:
            return
        self.scan_btn.configure(state="disabled")
        self.organize_btn.configure(state="disabled")
        self.del_orig_btn.configure(state="disabled")
        self.progress.start()
        self.status.set("正在清理原始文件（回收站）...")
        self.log("开始清理原始文件（移入回收站），共 %d 个，%s"
                 % (len(files), human(total)))
        threading.Thread(target=self._do_delete_originals, args=(files,),
                         daemon=True).start()

    def _do_delete_originals(self, files):
        sizes = {p: os.path.getsize(p) for p in files if os.path.exists(p)}
        ok_total, failures = send_to_recycle_bin(files)
        released = sum(sz for p, sz in sizes.items() if not os.path.exists(p))
        self.master.after(0, self._on_delete_originals_done,
                          ok_total, len(files), failures, released)

    def _on_delete_originals_done(self, ok, total, failures, released=0):
        self.progress.stop()
        self.scan_btn.configure(state="normal")
        self.organize_btn.configure(state="normal")
        self._refresh_original_list()
        if self.file_list:
            self.del_orig_btn.configure(state="normal")
        else:
            self.del_orig_btn.configure(state="disabled")
        self.status.set("原始文件清理完成，释放 %s" % human(released))
        self.log("[OK] 已将 %d / %d 个原始文件移入回收站，释放空间 %s"
                 % (ok, total, human(released)))
        for p, r in failures:
            self.log("[WARN] 删除原始文件失败: " + p + " (" + r + ")")
        if failures:
            messagebox.showwarning(
                "部分完成",
                "已将 %d / %d 个原始文件移入回收站，释放空间 %s，%d 个失败（详见日志）。"
                % (ok, total, human(released), len(failures)))
        else:
            messagebox.showinfo(
                "完成",
                "已将 %d 个微信原始文件移入回收站（可在回收站恢复），\n释放空间 %s，输出目录的副本已保留。"
                % (ok, human(released)))

    # ---------- 界面联动 ----------
    def _on_scheme_change(self, *a):
        self._update_scheme_preview()

    def _on_scan_mode_change(self):
        # 切换扫描范围后立即重新扫描，让结果即时更新
        if self.source_dir.get().strip() and os.path.isdir(self.source_dir.get().strip()):
            self.scan()

    def _on_size_filter_change(self, *a):
        if self.size_filter.get() == "自定义(MB)":
            self.min_mb_entry.configure(state="normal")
        else:
            self.min_mb_entry.configure(state="disabled")

    def _on_time_range_change(self, *a):
        if self.time_range.get() == "自定义":
            self.date_from_entry.configure(state="normal")
            self.date_to_entry.configure(state="normal")
        else:
            self.date_from_entry.configure(state="disabled")
            self.date_to_entry.configure(state="disabled")

    def _apply_time_filter(self, files):
        """按选择的修改时间范围过滤文件，返回过滤后的列表。"""
        rng = self.time_range.get()
        now = time.time()
        lo = hi = None
        if rng == "最近7天":
            lo = now - 7 * 86400
        elif rng == "最近30天":
            lo = now - 30 * 86400
        elif rng == "最近90天":
            lo = now - 90 * 86400
        elif rng == "今年":
            lo = datetime(datetime.now().year, 1, 1).timestamp()
        elif rng == "自定义":
            fs = self.date_from.get().strip()
            ts = self.date_to.get().strip()
            try:
                if fs:
                    lo = datetime.strptime(fs, "%Y-%m-%d").timestamp()
                if ts:
                    hi = datetime.strptime(ts, "%Y-%m-%d").timestamp() + 86400
            except ValueError:
                self.log("[WARN] 自定义日期格式应为 YYYY-MM-DD，已忽略时间筛选。")
                return files
        if lo is None and hi is None:
            return files
        out, skipped = [], 0
        for p in files:
            try:
                m = os.path.getmtime(p)
            except OSError:
                continue
            if lo is not None and m < lo:
                skipped += 1
                continue
            if hi is not None and m > hi:
                skipped += 1
                continue
            out.append(p)
        if skipped:
            self.log("[FILTER] 时间范围「%s」已过滤 %d 个文件，剩余 %d 个"
                     % (rng, skipped, len(out)))
        return out

    def _apply_size_filter(self, files):
        """按选择的文件大小下限过滤，返回过滤后的列表。"""
        rng = self.size_filter.get()
        thr = None
        if rng == "≥1MB":
            thr = 1 * MB
        elif rng == "≥10MB":
            thr = 10 * MB
        elif rng == "≥100MB":
            thr = 100 * MB
        elif rng == "自定义(MB)":
            s = self.min_mb.get().strip()
            if not s:
                return files
            try:
                thr = float(s) * MB
            except ValueError:
                self.log("[WARN] 自定义最小大小应为数字(MB)，已忽略大小筛选。")
                return files
        if thr is None:
            return files
        out, skipped = [], 0
        for p in files:
            try:
                sz = os.path.getsize(p)
            except OSError:
                continue
            if sz < thr:
                skipped += 1
                continue
            out.append(p)
        if skipped:
            self.log("[FILTER] 大小筛选「≥%s」已过滤 %d 个文件，剩余 %d 个"
                     % (human(thr), skipped, len(out)))
        return out

    def _update_scheme_preview(self):
        """根据当前选择的整理方式，显示一个普通用户能看懂的示例路径。"""
        label = self.scheme.get()
        key = SCHEME_LABELS.get(label, "type")
        sample = preset_rel(key, "文档", "2026-03", "文件名.pdf")
        if key == "type":
            hint = "所有文件会按类型分文件夹，如"
        elif key == "month":
            hint = "所有文件会按月份分文件夹，如"
        elif key == "year":
            hint = "所有文件会按年份分文件夹，如"
        elif key == "type-month":
            hint = "先按类型、再按月份分文件夹，如"
        elif key == "type-year":
            hint = "先按类型、再按年份分文件夹，如"
        elif key == "year-month":
            hint = "先按年份、再按月份分文件夹，如"
        elif key == "type-year-month":
            hint = "先按类型、再按年份、最后按月份分文件夹，如"
        else:
            hint = "文件会被整理到"
        self.scheme_preview.configure(
            text="%s：%s" % (hint, sample.replace("\\", "/")))

    # ---------- 设置记忆 + 打开输出文件夹 ----------
    def _load_config(self):
        """从配置文件恢复上次使用的设置（不保存源目录）。"""
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(cfg, dict):
            return
        if cfg.get("scheme") in SCHEME_LABELS:
            self.scheme.set(cfg["scheme"])
        if cfg.get("dedupe") in (True, False):
            self.dedupe.set(cfg["dedupe"])
        if cfg.get("deep_scan") in (True, False):
            self.deep_scan.set(cfg["deep_scan"])
        if cfg.get("merge_accounts") in (True, False):
            self.merge_accounts.set(cfg["merge_accounts"])
        if cfg.get("time_range") in TIME_RANGES:
            self.time_range.set(cfg["time_range"])
        if cfg.get("size_filter") in SIZE_FILTERS:
            self.size_filter.set(cfg["size_filter"])
        if isinstance(cfg.get("min_mb"), str):
            self.min_mb.set(cfg["min_mb"])
        if isinstance(cfg.get("dest_dir"), str) and cfg["dest_dir"]:
            self.dest_dir.set(cfg["dest_dir"])
        cats = cfg.get("categories")
        if isinstance(cats, dict):
            for c, v in cats.items():
                if c in self.cat_enabled and v in (True, False):
                    self.cat_enabled[c].set(v)

    def _save_config(self):
        """保存当前设置到配置文件。"""
        cfg = {
            "scheme": self.scheme.get(),
            "dedupe": self.dedupe.get(),
            "deep_scan": self.deep_scan.get(),
            "merge_accounts": self.merge_accounts.get(),
            "time_range": self.time_range.get(),
            "size_filter": self.size_filter.get(),
            "min_mb": self.min_mb.get(),
            "dest_dir": self.dest_dir.get(),
            "categories": {c: v.get() for c, v in self.cat_enabled.items()},
        }
        try:
            d = os.path.dirname(CONFIG_PATH)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _on_close(self):
        """关闭窗口时保存设置并退出。"""
        self._save_config()
        self.master.destroy()

    def open_dest_folder(self):
        """手动打开输出文件夹。"""
        d = self.dest_dir.get().strip()
        if not d or not os.path.isdir(d):
            messagebox.showinfo("提示", "输出文件夹尚不存在，请先「开始整理」。")
            return
        try:
            os.startfile(d)
        except Exception as e:
            self.log("[WARN] 无法打开输出文件夹: " + str(e))

    # ---------- 更新检查 ----------
    @staticmethod
    def _parse_ver(s):
        s = s.strip().lstrip("vV")
        out = []
        for p in re.split(r"[.\-]", s):
            m = re.match(r"\d+", p)
            out.append(int(m.group(0)) if m else 0)
        return out

    def _is_newer(self, latest, current):
        return self._parse_ver(latest) > self._parse_ver(current)

    def _check_update(self, verbose=True):
        threading.Thread(target=self._do_check_update, args=(verbose,),
                         daemon=True).start()

    def _do_check_update(self, verbose):
        try:
            url = "https://api.github.com/repos/%s/releases/latest" % UPDATE_REPO
            req = urllib.request.Request(
                url, headers={"User-Agent": "WeChatFileOrganizer"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            tag = data.get("tag_name", "")
            html = data.get("html_url", "")
            if not tag or not self._is_newer(tag, APP_VERSION):
                if verbose:
                    self.master.after(
                        0, lambda: messagebox.showinfo(
                            "已是最新", "当前已是最新版本 %s。" % APP_VERSION))
                return
            dl = ""
            for a in data.get("assets", []):
                if a.get("name", "").endswith(".exe"):
                    dl = a.get("browser_download_url", "")
                    break
            self.master.after(
                0, lambda: self.status.set(
                    "发现新版本 %s，点「检查更新」查看下载链接" % tag))
            if verbose:
                msg = ("发现新版本 %s（当前 %s）。\n\n下载地址：\n%s"
                       % (tag, APP_VERSION, dl or html))
                self.master.after(
                    0, lambda: messagebox.showinfo("有新版本可用", msg))
        except Exception as e:
            if verbose:
                self.master.after(
                    0, lambda: messagebox.showwarning(
                        "检查更新失败", "无法连接更新服务器：%s" % e))


def main():
    root = tk.Tk()
    OrganizerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
