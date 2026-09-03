#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信文件自动归类 - 图形界面版 (wechat-file-organizer-gui)
扫描微信文件目录（兼容传统 FileStorage/File 与新型自定义目录），
按类型/月份归类、去重、生成报告，一键复制到独立目录。

设计原则：
- 零运行时依赖：仅用 Python 标准库（tkinter 自带）。
- 安全优先：默认只扫描、生成预览报告，绝不改动任何源文件；点「一键归类」才复制。
- 源文件永不被删除或移动，只复制到输出目录。
- 中文路径/文件名友好。
- v1.3.0：分类勾选过滤、文件列表与双击预览、删除已归类副本（仅输出目录）。
"""
import os
import re
import sys
import shutil
import threading
import unicodedata
from datetime import datetime

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext, Menu
except ImportError:
    sys.exit("本程序需要 Tkinter 图形库（Python 标准库自带，正常情况下已包含）。")

MB = 1024 * 1024

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

# 归类方式：界面显示名 -> 内部键
SCHEME_LABELS = {
    "按类型": "type",
    "按月份": "month",
    "按类型+月份": "type-month",
}

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


def dest_path(dest_root, scheme, cat, month, fname, used):
    if scheme == "month":
        rel = os.path.join(month, fname)
    elif scheme == "type-month":
        rel = os.path.join(cat, month, fname)
    else:  # type
        rel = os.path.join(cat, fname)
    base, ext = os.path.splitext(fname)
    cand = rel
    i = 1
    while os.path.join(dest_root, cand) in used or os.path.exists(os.path.join(dest_root, cand)):
        cand = os.path.join(os.path.dirname(rel), "%s_%d%s" % (base, i, ext))
        i += 1
    used.add(os.path.join(dest_root, cand))
    return os.path.join(dest_root, cand)


# ---------- 图形界面 ----------
class OrganizerApp:
    def __init__(self, master):
        self.master = master
        master.title("微信文件自动归类")
        master.geometry("880x820")
        master.minsize(720, 640)

        self.records = []
        self.file_list = []          # 与文件列表树行一一对应的记录
        self.copy_map = {}           # 归类后 src -> dst 映射
        self.has_organized = False

        self.source_dir = tk.StringVar()
        self.dest_dir = tk.StringVar()
        self.scheme = tk.StringVar(value="按类型")
        self.dedupe = tk.BooleanVar(value=False)
        self.deep_scan = tk.BooleanVar(value=False)
        self.scanning = False

        # 分类勾选（默认全勾）
        self.cat_enabled = {c: tk.BooleanVar(value=True) for c in CATEGORIES}

        detected = find_wechat_files_dir() or ""
        self.source_dir.set(detected)
        self.dest_dir.set(default_output_dir())

        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 10, "pady": 5}

        # 源目录
        f_src = ttk.LabelFrame(self.master, text="微信文件目录")
        f_src.pack(fill="x", **pad)
        ttk.Entry(f_src, textvariable=self.source_dir, width=78).pack(
            side="left", fill="x", expand=True, padx=(8, 4), pady=8)
        ttk.Button(f_src, text="浏览...", command=self.browse_source).pack(
            side="left", padx=(0, 8), pady=8)

        # 设置
        f_set = ttk.LabelFrame(self.master, text="归类设置")
        f_set.pack(fill="x", **pad)
        ttk.Label(f_set, text="归类方式:").pack(side="left", padx=(8, 4), pady=8)
        cb = ttk.Combobox(f_set, textvariable=self.scheme, width=20, state="readonly")
        cb["values"] = tuple(SCHEME_LABELS.keys())
        cb.pack(side="left", padx=(0, 12), pady=8)
        ttk.Checkbutton(f_set, text="去重（相同内容只保留一份）", variable=self.dedupe).pack(
            side="left", padx=(4, 8), pady=8)
        ttk.Checkbutton(f_set, text="兼容模式（递归扫描任意目录）",
                        variable=self.deep_scan).pack(side="left", padx=(4, 8), pady=8)

        # 归类类别勾选
        f_cat = ttk.LabelFrame(self.master, text="归类类别（取消勾选则不整理该类）")
        f_cat.pack(fill="x", **pad)
        for c in CATEGORIES:
            ttk.Checkbutton(
                f_cat, text=c, variable=self.cat_enabled[c],
                command=self._refresh_will).pack(side="left", padx=(8, 6), pady=6)

        # 输出目录
        f_dst = ttk.LabelFrame(self.master, text="输出目录（源文件不会被删除，只复制到此处）")
        f_dst.pack(fill="x", **pad)
        ttk.Entry(f_dst, textvariable=self.dest_dir, width=78).pack(
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
        self.progress = ttk.Progressbar(f_act, mode="indeterminate", length=160)
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

        # 文件列表
        f_files = ttk.LabelFrame(self.master, text="文件列表（双击预览/打开，右键可删除已归类副本）")
        f_files.pack(fill="both", expand=True, **pad)
        fcols = ("name", "cat", "size", "mtime", "will")
        self.tree_files = ttk.Treeview(f_files, columns=fcols, show="headings", height=9)
        self.tree_files.heading("name", text="文件名")
        self.tree_files.heading("cat", text="类别")
        self.tree_files.heading("size", text="大小")
        self.tree_files.heading("mtime", text="修改时间")
        self.tree_files.heading("will", text="将归类")
        self.tree_files.column("name", width=320)
        self.tree_files.column("cat", width=80)
        self.tree_files.column("size", width=100)
        self.tree_files.column("mtime", width=120)
        self.tree_files.column("will", width=70)
        vsb = ttk.Scrollbar(f_files, orient="vertical", command=self.tree_files.yview)
        self.tree_files.configure(yscrollcommand=vsb.set)
        self.tree_files.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=6)
        vsb.pack(side="right", fill="y", pady=6, padx=(0, 10))
        self.tree_files.bind("<Double-1>", self._on_file_double)
        self.tree_files.bind("<Button-3>", self._on_file_right)

        # 右键菜单
        self.file_menu = Menu(self.master, tearoff=0)
        self.file_menu.add_command(label="打开 / 预览", command=self._menu_open)
        self.file_menu.add_command(label="打开所在文件夹", command=self._menu_open_folder)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="删除此文件的归类副本", command=self._menu_delete_one)

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

    def log(self, msg):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", msg + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    # ---------- 浏览 ----------
    def browse_source(self):
        d = filedialog.askdirectory(title="选择微信文件目录")
        if d:
            self.source_dir.set(d)
            self.apply_btn.configure(state="disabled")
            self.tree.delete(*self.tree.get_children())
            self.tree_files.delete(*self.tree_files.get_children())
            self.file_list = []
            self.has_organized = False

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
                        human(r["size"]), mt, will))

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
            self.tree_files.item(iid, values=(v[0], v[1], v[2], v[3], will))

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
        scheme = SCHEME_LABELS.get(self.scheme.get(), "type")
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
            dp = dest_path(dest, scheme, r["cat"], r["month"],
                           os.path.basename(r["path"]), used)
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
        self.status.set("归类完成")
        self.log("[OK] 已归类完成，复制 %d 个文件到: %s" % (copied, dest))
        if skipped_dup:
            self.log("      去重跳过 %d 个重复文件" % skipped_dup)
        if skipped_cat:
            self.log("      因未勾选类别跳过 %d 个文件" % skipped_cat)
        self.log("源文件未做任何改动。如需清理，可在文件列表右键「删除此文件的归类副本」或点「清空归类文件夹」。")
        messagebox.showinfo("完成", "已复制 %d 个文件到:\n%s" % (copied, dest))

    # ---------- 预览 / 打开 ----------
    def _rec_from_iid(self, iid):
        try:
            i = int(iid)
            return self.file_list[i]
        except (ValueError, IndexError):
            return None

    def _on_file_double(self, event):
        sel = self.tree_files.selection()
        if not sel:
            return
        rec = self._rec_from_iid(sel[0])
        if rec:
            self._preview_file(rec["path"])

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
        iid = self.tree_files.identify_row(event.y)
        if iid:
            self.tree_files.selection_set(iid)
            self.file_menu.post(event.x_root, event.y_root)

    def _menu_open(self):
        sel = self.tree_files.selection()
        if not sel:
            return
        rec = self._rec_from_iid(sel[0])
        if rec:
            self._preview_file(rec["path"])

    def _menu_open_folder(self):
        sel = self.tree_files.selection()
        if not sel:
            return
        rec = self._rec_from_iid(sel[0])
        if not rec:
            return
        d = os.path.dirname(rec["path"])
        try:
            os.startfile(d)
        except Exception as e:
            self.log("[WARN] 无法打开文件夹: " + str(e))

    # ---------- 删除已归类副本（仅输出目录） ----------
    def _menu_delete_one(self):
        sel = self.tree_files.selection()
        if not sel:
            messagebox.showinfo("提示", "请先扫描并归类后再删除副本。")
            return
        rec = self._rec_from_iid(sel[0])
        if not rec:
            return
        dst = self.copy_map.get(rec["path"])
        if not dst or not os.path.exists(dst):
            messagebox.showinfo("提示", "该文件尚未归类，没有可删除的副本。")
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
        self.log("[DEL] 已清空归类文件夹，删除 %d 个文件: %s" % (removed, dest))
        self.status.set("已清空归类文件夹（%d 个文件）" % removed)
        messagebox.showinfo("完成", "已删除 %d 个归类副本文件。" % removed)


def main():
    root = tk.Tk()
    OrganizerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
