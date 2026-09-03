#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信文件自动归类 - 图形界面版 (wechat-file-organizer-gui)
扫描微信文件目录（兼容传统 FileStorage/File 与新型自定义目录），
按类型/月份归类、去重、生成报告，一键复制到独立目录。

设计原则：
- 零运行时依赖：仅用 Python 标准库（tkinter 自带）。
- 安全优先：默认只扫描、生成预览报告，绝不改动任何源文件；点「一键归类」才复制。
- 「清理原始文件」为可选显式操作：选定后移入回收站（可恢复），默认不永久删除；批量清理时会提示哪些文件已有归类副本、哪些尚未归类，由用户确认后再执行。
- 中文路径/文件名友好。
- v1.5.0：文件列表拆分为「原始文件」与「归类副本」两个标签页，删除后自动刷新。
- v1.6.0：新增「按修改时间筛选」（最近7/30/90天、今年、自定义日期区间）与「输出目录结构自定义」（按类型/月份/年份/组合 + 自定义路径模板如 {type}/{yyyy}/{mm}）。
- v1.7.0：新增「按文件大小筛选」（全部/≥1MB/≥10MB/≥100MB/自定义MB）与自定义模板「实时路径预览」。
- v1.8.0：新增「检查更新」——启动后静默查询 GitHub Releases 最新版本，有新版本时状态栏提示；点「检查更新」显示下载链接（纯本地 urllib 查询，无依赖、不收集信息）。
- v1.9.0：简化「输出目录结构」选择，去掉模板令牌/自定义模板，改为直白的固定选项（按文件类型/按月份/按年份/按类型+月份/按类型+年份/按年份+月份/按类型+年份+月份），并实时显示整理示例。
- v1.10.0：启动后自动扫描（微信目录有效时），并顶部显示三步使用提示，让普通用户打开即见结果。
- v1.11.0：一键归类完成后自动打开输出文件夹；兼容模式标签改为更直白的「扫描整个文件夹（适合新版微信）」。
- v1.12.0：记住用户上次使用的整理方式、筛选、类别勾选和输出目录；新增「打开输出文件夹」按钮，让重复使用更顺手。
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
APP_VERSION = "1.12.0"
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
}
EXT_TO_CAT = {}
for _cat, _exts in CATEGORIES.items():
    for _e in _exts:
        EXT_TO_CAT[_e] = _cat

# 整理方式：界面显示名 -> 内部键（去掉程序员风格的模板令牌，只留直白选项）
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

# Tk 的 PhotoImage 能直接显示的常见图片格式（jpg 需 PIL，故回退到系统打开）
IMAGE_PREVIEW_EXTS = {".png", ".gif", ".bmp", ".tiff", ".tif"}

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


def default_output_dir():
    """默认输出目录：桌面下的『微信文件整理』，普通用户最容易找到。"""
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.isdir(desktop):
        return os.path.join(desktop, "微信文件整理")
    return os.path.join(os.path.expanduser("~"), "Documents", "微信文件整理")


def discover_accounts(root):
    if os.path.basename(root).lower() == "filestorage":
        return [root]
    accounts = []
    if os.path.isdir(root):
        for name in os.listdir(root):
            d = os.path.join(root, name)
            if os.path.isdir(d) and os.path.isdir(os.path.join(d, "FileStorage")):
                accounts.append(os.path.join(d, "FileStorage"))
    if not accounts and os.path.isdir(os.path.join(root, "File")):
        accounts = [root]
    return accounts


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


def collect_sources(root, include_media=False, scan_all=False, recursive=False):
    if recursive:
        files = recursive_collect(root)
        if not include_media:
            files = [f for f in files
                     if os.path.splitext(f)[1].lower() != ".dat"]
        return files
    files = []
    targets = []
    for fs in discover_accounts(root):
        file_dir = os.path.join(fs, "File")
        if os.path.isdir(file_dir):
            targets.append(file_dir)
        if scan_all:
            for sub in ("Image", "Video", "Voice", "Attachment", "CustomEmotion", "Fav"):
                sd = os.path.join(fs, sub)
                if os.path.isdir(sd):
                    targets.append(sd)
    seen = set()
    for t in targets:
        for dirpath, _, fnames in os.walk(t):
            for fn in fnames:
                p = os.path.join(dirpath, fn)
                if not include_media and os.path.splitext(fn)[1].lower() == ".dat":
                    continue
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
        master.title("微信文件自动归类")
        master.geometry("1000x960")
        master.minsize(800, 720)

        self.records = []
        self.file_list = []          # 原始文件记录（与 tree_files 行一一对应）
        self.output_list = []        # 归类副本记录（与 tree_outputs 行一一对应）
        self.copy_map = {}           # 归类后 src -> dst 映射
        self.has_organized = False

        self.source_dir = tk.StringVar()
        self.dest_dir = tk.StringVar()
        self.scheme = tk.StringVar(value="按文件类型")
        self.dedupe = tk.BooleanVar(value=False)
        self.deep_scan = tk.BooleanVar(value=False)
        self.time_range = tk.StringVar(value="全部")
        self.date_from = tk.StringVar()
        self.date_to = tk.StringVar()
        self.size_filter = tk.StringVar(value="全部")
        self.min_mb = tk.StringVar()
        self.scanning = False

        # 分类勾选（默认全勾）
        self.cat_enabled = {c: tk.BooleanVar(value=True) for c in CATEGORIES}

        # 加载上次使用的设置（源目录仍每次自动探测，不保存）
        self._load_config()

        detected = find_wechat_files_dir() or ""
        self.source_dir.set(detected)
        if not self.dest_dir.get().strip():
            self.dest_dir.set(default_output_dir())

        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 10, "pady": 5}

        # 顶部友好提示横幅（不玩术语，普通用户一看就懂）
        banner = tk.Label(
            self.master,
            text="三步完成整理：① 扫描出微信文件 → ② 勾选想搬的类别 → ③ 点「一键归类」复制到桌面（微信原文件不会动）。",
            relief="groove", borderwidth=1, bg="#E8F0FE", fg="#1a1a1a",
            font=("Microsoft YaHei UI", 10), anchor="w", padx=10, pady=8)
        banner.pack(fill="x", **pad)

        # 源目录
        f_src = ttk.LabelFrame(self.master, text="微信文件目录")
        f_src.pack(fill="x", **pad)
        ttk.Entry(f_src, textvariable=self.source_dir, width=86).pack(
            side="left", fill="x", expand=True, padx=(8, 4), pady=8)
        ttk.Button(f_src, text="浏览...", command=self.browse_source).pack(
            side="left", padx=(0, 8), pady=8)

        # 设置
        f_set = ttk.LabelFrame(self.master, text="归类设置")
        f_set.pack(fill="x", **pad)
        line1 = ttk.Frame(f_set)
        line1.pack(fill="x", padx=8, pady=(4, 0))
        ttk.Label(line1, text="整理方式:").pack(side="left", padx=(0, 4))
        cb = ttk.Combobox(line1, textvariable=self.scheme, width=22,
                          state="readonly")
        cb["values"] = tuple(SCHEME_LABELS.keys())
        cb.bind("<<ComboboxSelected>>", self._on_scheme_change)
        cb.pack(side="left", padx=(0, 12))
        ttk.Checkbutton(line1, text="去重（相同内容只保留一份）",
                        variable=self.dedupe).pack(side="left", padx=(4, 8))
        ttk.Checkbutton(line1, text="扫描整个文件夹（适合新版微信）",
                        variable=self.deep_scan).pack(side="left", padx=(4, 8))
        self.scheme_preview = ttk.Label(
            f_set, text="", foreground="#666666", wraplength=900)
        self.scheme_preview.pack(anchor="w", padx=10, pady=(2, 6))

        # 扫描筛选：时间范围
        f_filter = ttk.LabelFrame(
            self.master, text="扫描筛选（按修改时间，扫描时生效）")
        f_filter.pack(fill="x", **pad)
        tf = ttk.Frame(f_filter)
        tf.pack(fill="x", padx=10, pady=6)
        ttk.Label(tf, text="时间范围:").pack(side="left", padx=(0, 4))
        cb_t = ttk.Combobox(tf, textvariable=self.time_range, width=14,
                            state="readonly")
        cb_t["values"] = tuple(TIME_RANGES)
        cb_t.bind("<<ComboboxSelected>>", self._on_time_range_change)
        cb_t.pack(side="left", padx=(0, 10))
        ttk.Label(tf, text="起:").pack(side="left", padx=(0, 2))
        self.date_from_entry = ttk.Entry(tf, textvariable=self.date_from,
                                         width=12, state="disabled")
        self.date_from_entry.pack(side="left", padx=(0, 6))
        ttk.Label(tf, text="止:").pack(side="left", padx=(0, 2))
        self.date_to_entry = ttk.Entry(tf, textvariable=self.date_to,
                                        width=12, state="disabled")
        self.date_to_entry.pack(side="left", padx=(0, 6))
        ttk.Label(tf, text="格式 YYYY-MM-DD（仅「自定义」时可用）").pack(
            side="left", padx=(4, 0))
        self._on_time_range_change()

        # 第二行：大小筛选
        tf2 = ttk.Frame(f_filter)
        tf2.pack(fill="x", padx=10, pady=(0, 6))
        ttk.Label(tf2, text="文件大小:").pack(side="left", padx=(0, 4))
        cb_s = ttk.Combobox(tf2, textvariable=self.size_filter, width=14,
                            state="readonly")
        cb_s["values"] = tuple(SIZE_FILTERS)
        cb_s.bind("<<ComboboxSelected>>", self._on_size_filter_change)
        cb_s.pack(side="left", padx=(0, 10))
        ttk.Label(tf2, text="最小(MB):").pack(side="left", padx=(0, 2))
        self.min_mb_entry = ttk.Entry(tf2, textvariable=self.min_mb,
                                      width=12, state="disabled")
        self.min_mb_entry.pack(side="left", padx=(0, 6))
        ttk.Label(tf2, text="（仅「自定义(MB)」时可用）").pack(
            side="left", padx=(4, 0))
        self._on_size_filter_change()

        # 归类类别勾选
        f_cat = ttk.LabelFrame(self.master, text="归类类别（取消勾选则不整理该类）")
        f_cat.pack(fill="x", **pad)
        for c in CATEGORIES:
            ttk.Checkbutton(
                f_cat, text=c, variable=self.cat_enabled[c],
                command=self._refresh_will).pack(side="left", padx=(8, 6), pady=6)

        # 输出目录
        f_dst = ttk.LabelFrame(self.master, text="输出目录（归类副本存放处；原始文件可在确认后直接移入回收站，无需先归类）")
        f_dst.pack(fill="x", **pad)
        ttk.Entry(f_dst, textvariable=self.dest_dir, width=86).pack(
            side="left", fill="x", expand=True, padx=(8, 4), pady=8)
        ttk.Button(f_dst, text="浏览...", command=self.browse_dest).pack(
            side="left", padx=(0, 8), pady=8)

        # 操作按钮
        f_act = ttk.Frame(self.master)
        f_act.pack(fill="x", **pad)
        self.scan_btn = ttk.Button(f_act, text="扫描（只读预览）", command=self.scan)
        self.scan_btn.pack(side="left", padx=(0, 10))
        self.apply_btn = ttk.Button(f_act, text="一键归类（复制勾选类别）",
                                    command=self.apply_organize, state="disabled")
        self.apply_btn.pack(side="left", padx=(0, 10))
        self.clear_btn = ttk.Button(f_act, text="清空归类文件夹",
                                    command=self.clear_output)
        self.clear_btn.pack(side="left", padx=(0, 10))
        self.del_orig_btn = ttk.Button(f_act, text="清理原始文件（回收站）",
                                       command=self.delete_originals, state="disabled")
        self.del_orig_btn.pack(side="left", padx=(0, 10))
        self.open_dest_btn = ttk.Button(f_act, text="打开输出文件夹",
                                        command=self.open_dest_folder)
        self.open_dest_btn.pack(side="left", padx=(0, 10))
        self.update_btn = ttk.Button(f_act, text="检查更新",
                                     command=lambda: self._check_update(verbose=True))
        self.update_btn.pack(side="left", padx=(0, 10))
        self.progress = ttk.Progressbar(f_act, mode="indeterminate", length=120)
        self.progress.pack(side="left", padx=(12, 0))

        # 统计
        f_stat = ttk.LabelFrame(self.master, text="统计预览")
        f_stat.pack(fill="x", **pad)
        self.stat_var = tk.StringVar(value="尚未扫描")
        ttk.Label(f_stat, textvariable=self.stat_var, justify="left").pack(
            anchor="w", padx=10, pady=6)

        # 类别汇总
        f_tree = ttk.LabelFrame(self.master, text="按类型统计（将归类的类别）")
        f_tree.pack(fill="x", **pad)
        cols = ("cat", "count", "size", "will")
        self.tree = ttk.Treeview(f_tree, columns=cols, show="headings", height=5)
        self.tree.heading("cat", text="类别")
        self.tree.heading("count", text="文件数")
        self.tree.heading("size", text="大小")
        self.tree.heading("will", text="将归类")
        self.tree.column("cat", width=120)
        self.tree.column("count", width=90)
        self.tree.column("size", width=130)
        self.tree.column("will", width=80)
        self.tree.pack(fill="x", padx=10, pady=6)

        # 文件列表（Notebook：原始文件 / 归类副本）
        f_files = ttk.LabelFrame(
            self.master,
            text="文件列表（双击预览/打开；「原始文件」页右键可清理源文件，「归类副本」页右键可删副本）")
        f_files.pack(fill="both", expand=True, **pad)
        self.file_notebook = ttk.Notebook(f_files)
        self.file_notebook.pack(fill="both", expand=True, padx=10, pady=6)

        # 原始文件页
        f_orig = ttk.Frame(self.file_notebook)
        self.file_notebook.add(f_orig, text="原始文件")
        fcols = ("name", "cat", "size", "mtime", "will", "status")
        self.tree_files = ttk.Treeview(f_orig, columns=fcols, show="headings", height=9)
        self.tree_files.heading("name", text="文件名")
        self.tree_files.heading("cat", text="类别")
        self.tree_files.heading("size", text="大小")
        self.tree_files.heading("mtime", text="修改时间")
        self.tree_files.heading("will", text="将归类")
        self.tree_files.heading("status", text="状态")
        self.tree_files.column("name", width=300)
        self.tree_files.column("cat", width=70)
        self.tree_files.column("size", width=90)
        self.tree_files.column("mtime", width=120)
        self.tree_files.column("will", width=60)
        self.tree_files.column("status", width=80)
        vsb = ttk.Scrollbar(f_orig, orient="vertical", command=self.tree_files.yview)
        self.tree_files.configure(yscrollcommand=vsb.set)
        self.tree_files.pack(side="left", fill="both", expand=True, padx=(0, 0), pady=0)
        vsb.pack(side="right", fill="y", pady=0, padx=(0, 0))

        # 归类副本页
        f_out = ttk.Frame(self.file_notebook)
        self.file_notebook.add(f_out, text="归类副本")
        ocols = ("name", "cat", "size", "mtime", "src")
        self.tree_outputs = ttk.Treeview(f_out, columns=ocols, show="headings", height=9)
        self.tree_outputs.heading("name", text="文件名")
        self.tree_outputs.heading("cat", text="类别")
        self.tree_outputs.heading("size", text="大小")
        self.tree_outputs.heading("mtime", text="修改时间")
        self.tree_outputs.heading("src", text="原始文件路径")
        self.tree_outputs.column("name", width=220)
        self.tree_outputs.column("cat", width=70)
        self.tree_outputs.column("size", width=90)
        self.tree_outputs.column("mtime", width=120)
        self.tree_outputs.column("src", width=260)
        vsb2 = ttk.Scrollbar(f_out, orient="vertical", command=self.tree_outputs.yview)
        self.tree_outputs.configure(yscrollcommand=vsb2.set)
        self.tree_outputs.pack(side="left", fill="both", expand=True, padx=(0, 0), pady=0)
        vsb2.pack(side="right", fill="y", pady=0, padx=(0, 0))

        self.tree_files.bind("<Double-1>", self._on_file_double)
        self.tree_files.bind("<Button-3>", self._on_file_right)
        self.tree_outputs.bind("<Double-1>", self._on_file_double)
        self.tree_outputs.bind("<Button-3>", self._on_file_right)

        # 右键菜单（动态构建）
        self.file_menu = Menu(self.master, tearoff=0)

        # 日志
        f_log = ttk.LabelFrame(self.master, text="日志")
        f_log.pack(fill="both", expand=True, **pad)
        self.logbox = scrolledtext.ScrolledText(f_log, height=7, state="disabled",
                                                wrap="word")
        self.logbox.pack(fill="both", expand=True, padx=10, pady=6)

        # 状态栏
        self.status = tk.StringVar(value="就绪")
        ttk.Label(self.master, textvariable=self.status, relief="sunken",
                  anchor="w").pack(fill="x", side="bottom")

        # 初始化整理方式示例
        self._update_scheme_preview()

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
            self.apply_btn.configure(state="disabled")
            self.del_orig_btn.configure(state="disabled")
            self.tree.delete(*self.tree.get_children())
            self.tree_files.delete(*self.tree_files.get_children())
            self.tree_outputs.delete(*self.tree_outputs.get_children())
            self.file_list = []
            self.output_list = []
            self.has_organized = False
            self.copy_map = {}

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
        self.apply_btn.configure(state="disabled")
        self.progress.start()
        self.status.set("正在扫描...")
        self.log("开始扫描: " + root)

        # 未检测到传统结构时，自动启用兼容模式
        if not discover_accounts(root) and not self.deep_scan.get():
            self.deep_scan.set(True)
            self.log("[INFO] 未检测到传统 FileStorage/File 结构，已自动启用兼容模式（递归扫描）。")

        threading.Thread(target=self._do_scan, args=(root,), daemon=True).start()

    def _do_scan(self, root):
        recursive = self.deep_scan.get()
        files = collect_sources(root, include_media=False, scan_all=False,
                                recursive=recursive)
        files = self._apply_time_filter(files)
        files = self._apply_size_filter(files)
        if not files:
            self.master.after(0, self._on_scan_empty, root)
            return
        records = []
        for p in files:
            try:
                st = os.stat(p)
                records.append({
                    "path": p, "cat": cat_of(p), "month": month_of(p),
                    "size": st.st_size, "hash": sha256_of(p), "mtime": st.st_mtime,
                })
            except OSError:
                continue
        self.records = records
        self.master.after(0, self._on_scan_done, root)

    def _on_scan_empty(self, root):
        self.scanning = False
        self.progress.stop()
        self.scan_btn.configure(state="normal")
        self.status.set("未发现可归类的文件")
        self.stat_var.set("未发现可归类的文件（目录下可能没有可整理的文件）")
        self.log("[SKIP] 未发现可归类的文件。")
        self.log("提示：已尝试兼容模式（递归扫描）仍无结果。")
        self.log("      请确认所选目录下确实包含微信接收的文档/图片/视频等文件。")

    def _on_scan_done(self, root):
        self.scanning = False
        self.progress.stop()
        self.scan_btn.configure(state="normal")
        self.apply_btn.configure(state="normal")
        self.del_orig_btn.configure(state="normal")

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

        # 类别汇总（反映勾选）
        self.tree.delete(*self.tree.get_children())
        for cat in CATEGORIES:
            if cat in by_cat:
                c = by_cat[cat]
                will = "是" if self.cat_enabled[cat].get() else "否"
                self.tree.insert("", "end", values=(cat, c[0], human(c[1]), will))

        # 文件列表
        self._populate_file_list(records)
        self._populate_output_list([])

        self.stat_var.set(
            "文件总数 %d | 总大小 %s | 将归类 %d 个 | 重复 %d 个（去重可省 %s）"
            % (total, human(total_size),
               sum(1 for r in records if self.cat_enabled[r["cat"]].get()),
               dup_count, human(dup_recover)))

        self.log("扫描完成: 共 %d 个文件，总大小 %s" % (total, human(total_size)))
        self.log("按类型: " + "，".join(
            "%s %d" % (k, v[0]) for k, v in sorted(by_cat.items(),
                                                   key=lambda kv: -kv[1][1])))
        if dup_count:
            self.log("发现重复文件 %d 个，去重可节省 %s（勾选「去重」后归类会跳过重复项）"
                     % (dup_count, human(dup_recover)))
        self.log("预览就绪。点击「一键归类」将勾选类别复制到输出目录（源文件不动）。")
        self.status.set("扫描完成，可归类")

    def _status_of(self, rec):
        if rec["path"] in self.copy_map and os.path.exists(self.copy_map[rec["path"]]):
            return "已归类"
        return "待归类"

    def _populate_file_list(self, records):
        self.tree_files.delete(*self.tree_files.get_children())
        self.file_list = list(records)
        for i, r in enumerate(records):
            will = "是" if self.cat_enabled[r["cat"]].get() else "否"
            try:
                mt = datetime.fromtimestamp(r["mtime"]).strftime("%Y-%m-%d %H:%M")
            except OSError:
                mt = "未知"
            self.tree_files.insert(
                "", "end", iid=str(i),
                values=(os.path.basename(r["path"]), r["cat"],
                        human(r["size"]), mt, will, self._status_of(r)))

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

    def _refresh_will(self):
        # 勾选变化时，刷新两类表格的「将归类」列
        for cat in CATEGORIES:
            if cat in self.cat_enabled:
                will = "是" if self.cat_enabled[cat].get() else "否"
                for row in self.tree.get_children():
                    if self.tree.item(row, "values")[0] == cat:
                        v = self.tree.item(row, "values")
                        self.tree.item(row, values=(cat, v[1], v[2], will))
        for iid in self.tree_files.get_children():
            i = int(iid)
            rec = self.file_list[i]
            will = "是" if self.cat_enabled[rec["cat"]].get() else "否"
            v = self.tree_files.item(iid, "values")
            self.tree_files.item(iid, values=(v[0], v[1], v[2], v[3], will, self._status_of(rec)))

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
            messagebox.showwarning("提示", "没有勾选任何类别，无法归类。")
            return
        n = len(enabled)
        ans = messagebox.askyesno(
            "确认归类",
            "即将把 %d 个文件（已勾选类别）复制到:\n%s\n\n源文件不会被删除或移动（仅复制）。\n是否继续？"
            % (n, dest))
        if not ans:
            return
        self.scan_btn.configure(state="disabled")
        self.apply_btn.configure(state="disabled")
        self.progress.start()
        self.status.set("正在归类...")
        self.log("开始归类到: " + dest)
        threading.Thread(target=self._do_apply, args=(dest,), daemon=True).start()

    def _do_apply(self, dest):
        label = self.scheme.get()
        dedupe = self.dedupe.get()
        os.makedirs(dest, exist_ok=True)
        used = set()
        seen_hash = set()
        copied = skipped_dup = skipped_cat = 0
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
                self.copy_map[r["path"]] = dp
                copied += 1
            except OSError as e:
                self.master.after(0, lambda m=str(e): self.log("[WARN] 复制失败: " + m))
        self.has_organized = True
        self.master.after(0, self._on_apply_done, dest, copied, skipped_dup, skipped_cat)

    def _on_apply_done(self, dest, copied, skipped_dup, skipped_cat):
        self.progress.stop()
        self.scan_btn.configure(state="normal")
        self.apply_btn.configure(state="normal")
        self.del_orig_btn.configure(state="normal")
        self._populate_output_list_from_copy_map()
        self._populate_file_list(self.file_list)  # 刷新「状态」列
        self.file_notebook.select(1)              # 归类完成自动切换到「归类副本」
        self.status.set("归类完成")
        self.log("[OK] 已归类完成，复制 %d 个文件到: %s" % (copied, dest))
        if skipped_dup:
            self.log("      去重跳过 %d 个重复文件" % skipped_dup)
        if skipped_cat:
            self.log("      因未勾选类别跳过 %d 个文件" % skipped_cat)
        self.log("源文件未做任何改动。如需清理，可在「归类副本」页右键删除副本，或点「清空归类文件夹」。")
        messagebox.showinfo("完成", "已复制 %d 个文件到:\n%s" % (copied, dest))
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
        if rec:
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

    def _show_image_preview(self, path):
        win = tk.Toplevel(self.master)
        win.title("预览: " + os.path.basename(path))
        try:
            img = tk.PhotoImage(file=path)
            # 限制最大显示尺寸
            max_w, max_h = 760, 560
            w, h = img.width(), img.height()
            if w > max_w or h > max_h:
                ratio = min(max_w / w, max_h / h)
                img = img.subsample(max(1, int(1 / ratio)))
            lbl = ttk.Label(win, image=img)
            lbl.image = img
            lbl.pack(padx=10, pady=10)
            win.geometry("%dx%d" % (min(w, max_w) + 20, min(h, max_h) + 20))
        except Exception as e:
            win.destroy()
            try:
                os.startfile(path)
            except Exception:
                self.log("[WARN] 图片预览失败，改用系统打开: " + str(e))

    def _on_file_right(self, event):
        tree = self._current_tree()
        iid = tree.identify_row(event.y)
        if iid:
            tree.selection_set(iid)
            self._build_context_menu(tree)
            self.file_menu.post(event.x_root, event.y_root)

    def _build_context_menu(self, tree):
        self.file_menu.delete(0, "end")
        self.file_menu.add_command(label="打开 / 预览", command=self._menu_open)
        self.file_menu.add_command(label="打开所在文件夹", command=self._menu_open_folder)
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
        if rec:
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
            messagebox.showinfo("提示", "输出文件夹不存在或尚未归类。")
            return
        files = []
        for dp, _, fnames in os.walk(dest):
            for fn in fnames:
                files.append(os.path.join(dp, fn))
        if not files:
            messagebox.showinfo("提示", "输出文件夹为空，无需清理。")
            return
        ans = messagebox.askyesno(
            "确认清空归类文件夹",
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
        self.log("[DEL] 已清空归类文件夹，删除 %d 个文件: %s" % (removed, dest))
        self.status.set("已清空归类文件夹（%d 个文件）" % removed)
        messagebox.showinfo("完成", "已删除 %d 个归类副本文件。" % removed)

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
            msg = ("【注意】该文件尚未归类（没有输出目录副本）。\n"
                   "仍要把微信【原始文件】移入回收站（可在回收站恢复）：\n%s\n\n"
                   "大小：%s\n\n是否继续？") % (src, human(os.path.getsize(src)))
        ans = messagebox.askyesno("删除原始文件（回收站）", msg)
        if not ans:
            return
        ok, failures = send_to_recycle_bin([src])
        if ok:
            self.log("[DEL-ORIG] 已移入回收站: " + src)
            self.status.set("已清理 1 个原始文件（回收站）")
            # 若该文件曾在 copy_map 中，清理掉已不存在的源
            if src in self.copy_map and not os.path.exists(src):
                self.copy_map.pop(src, None)
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
                   "%d 个尚未归类。\n\n"
                   "文件总大小：%s\n\n是否继续？"
                   % (len(files), copied_count, len(files) - copied_count, human(total)))
        else:
            msg = ("即将把 %d 个微信【原始文件】移入回收站（可在回收站里恢复）。\n"
                   "这些文件都尚未归类（没有输出目录副本）。\n\n"
                   "文件总大小：%s\n\n是否继续？"
                   % (len(files), human(total)))
        ans = messagebox.askyesno("清理原始文件（回收站）", msg)
        if not ans:
            return
        self.scan_btn.configure(state="disabled")
        self.apply_btn.configure(state="disabled")
        self.del_orig_btn.configure(state="disabled")
        self.progress.start()
        self.status.set("正在清理原始文件（回收站）...")
        self.log("开始清理原始文件（移入回收站），共 %d 个，%s"
                 % (len(files), human(total)))
        threading.Thread(target=self._do_delete_originals, args=(files,),
                         daemon=True).start()

    def _do_delete_originals(self, files):
        ok_total, failures = send_to_recycle_bin(files)
        # 清理 copy_map 中已被移入回收站的原始项
        done_srcs = {p for p in files if not os.path.exists(p)}
        for p in done_srcs:
            self.copy_map.pop(p, None)
        self.master.after(0, self._on_delete_originals_done,
                          ok_total, len(files), failures)

    def _on_delete_originals_done(self, ok, total, failures):
        self.progress.stop()
        self.scan_btn.configure(state="normal")
        self.apply_btn.configure(state="normal")
        self._refresh_original_list()
        if self.file_list:
            self.del_orig_btn.configure(state="normal")
        else:
            self.del_orig_btn.configure(state="disabled")
        self.status.set("原始文件清理完成")
        self.log("[OK] 已将 %d / %d 个原始文件移入回收站" % (ok, total))
        for p, r in failures:
            self.log("[WARN] 删除原始文件失败: " + p + " (" + r + ")")
        if failures:
            messagebox.showwarning(
                "部分完成",
                "已将 %d / %d 个原始文件移入回收站，%d 个失败（详见日志）。"
                % (ok, total, len(failures)))
        else:
            messagebox.showinfo(
                "完成",
                "已将 %d 个微信原始文件移入回收站（可在回收站恢复），\n输出目录的副本已保留。"
                % ok)

    # ---------- 界面联动 ----------
    def _on_scheme_change(self, *a):
        self._update_scheme_preview()

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
            messagebox.showinfo("提示", "输出文件夹尚不存在，请先「一键归类」。")
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
