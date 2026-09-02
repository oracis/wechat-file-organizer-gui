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
"""
import os
import re
import sys
import hashlib
import shutil
import threading
import unicodedata
from datetime import datetime

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext
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
    h = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            for b in iter(lambda: f.read(chunk), b""):
                h.update(b)
    except OSError:
        return "ERR"
    return h.hexdigest()


# ---------- 路径发现 ----------
def find_wechat_files_dir():
    env = os.environ.get("WECHAT_FILES_DIR")
    if env and os.path.isdir(env):
        return env
    base = os.path.expanduser(os.path.join("~", "Documents", "WeChat Files"))
    if os.path.isdir(base):
        return base
    return None


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
        master.geometry("780x720")
        master.minsize(680, 560)

        self.records = []
        self.source_dir = tk.StringVar()
        self.dest_dir = tk.StringVar()
        self.scheme = tk.StringVar(value="type")
        self.dedupe = tk.BooleanVar(value=False)
        self.deep_scan = tk.BooleanVar(value=False)
        self.scanning = False

        detected = find_wechat_files_dir() or ""
        self.source_dir.set(detected)
        if detected:
            self.dest_dir.set(os.path.join(
                os.path.dirname(os.path.abspath(detected)), "WeChatFiles_Organized"))

        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 10, "pady": 5}

        # 源目录
        f_src = ttk.LabelFrame(self.master, text="微信文件目录")
        f_src.pack(fill="x", **pad)
        ttk.Entry(f_src, textvariable=self.source_dir, width=70).pack(
            side="left", fill="x", expand=True, padx=(8, 4), pady=8)
        ttk.Button(f_src, text="浏览...", command=self.browse_source).pack(
            side="left", padx=(0, 8), pady=8)

        # 设置
        f_set = ttk.LabelFrame(self.master, text="归类设置")
        f_set.pack(fill="x", **pad)
        ttk.Label(f_set, text="归类方式:").pack(side="left", padx=(8, 4), pady=8)
        cb = ttk.Combobox(f_set, textvariable=self.scheme, width=22, state="readonly")
        cb["values"] = ("type", "month", "type-month")
        cb.pack(side="left", padx=(0, 12), pady=8)
        ttk.Label(f_set, text="（type=按类型 / month=按月份 / type-month=类型+月份）").pack(
            side="left", padx=(0, 8), pady=8)
        ttk.Checkbutton(f_set, text="去重（相同内容只保留一份）", variable=self.dedupe).pack(
            side="left", padx=(4, 8), pady=8)
        ttk.Checkbutton(f_set, text="兼容模式（递归扫描任意目录，忽略微信系统文件）",
                        variable=self.deep_scan).pack(side="left", padx=(4, 8), pady=8)

        # 输出目录
        f_dst = ttk.LabelFrame(self.master, text="输出目录（源文件不会被删除，只复制到此处）")
        f_dst.pack(fill="x", **pad)
        ttk.Entry(f_dst, textvariable=self.dest_dir, width=70).pack(
            side="left", fill="x", expand=True, padx=(8, 4), pady=8)
        ttk.Button(f_dst, text="浏览...", command=self.browse_dest).pack(
            side="left", padx=(0, 8), pady=8)

        # 操作按钮
        f_act = ttk.Frame(self.master)
        f_act.pack(fill="x", **pad)
        self.scan_btn = ttk.Button(f_act, text="扫描（只读预览）", command=self.scan)
        self.scan_btn.pack(side="left", padx=(0, 10))
        self.apply_btn = ttk.Button(f_act, text="一键归类（复制文件）",
                                    command=self.apply_organize, state="disabled")
        self.apply_btn.pack(side="left")
        self.progress = ttk.Progressbar(f_act, mode="indeterminate", length=180)
        self.progress.pack(side="left", padx=(12, 0))

        # 统计
        f_stat = ttk.LabelFrame(self.master, text="统计预览")
        f_stat.pack(fill="x", **pad)
        self.stat_var = tk.StringVar(value="尚未扫描")
        ttk.Label(f_stat, textvariable=self.stat_var, justify="left").pack(
            anchor="w", padx=10, pady=6)

        # 类别表格
        f_tree = ttk.LabelFrame(self.master, text="按类型统计")
        f_tree.pack(fill="both", expand=True, **pad)
        cols = ("cat", "count", "size")
        self.tree = ttk.Treeview(f_tree, columns=cols, show="headings", height=6)
        self.tree.heading("cat", text="类别")
        self.tree.heading("count", text="文件数")
        self.tree.heading("size", text="大小")
        self.tree.column("cat", width=120)
        self.tree.column("count", width=100)
        self.tree.column("size", width=140)
        self.tree.pack(fill="both", expand=True, padx=10, pady=6)

        # 日志
        f_log = ttk.LabelFrame(self.master, text="日志")
        f_log.pack(fill="both", expand=True, **pad)
        self.logbox = scrolledtext.ScrolledText(f_log, height=8, state="disabled",
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

    def browse_source(self):
        d = filedialog.askdirectory(title="选择微信 WeChat Files 目录")
        if d:
            self.source_dir.set(d)
            self.dest_dir.set(os.path.join(
                os.path.dirname(os.path.abspath(d)), "WeChatFiles_Organized"))
            self.apply_btn.configure(state="disabled")
            self.tree.delete(*self.tree.get_children())

    def browse_dest(self):
        d = filedialog.askdirectory(title="选择归类输出目录")
        if d:
            self.dest_dir.set(d)

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
        self.stat_var.set("未发现可归类的文件（目录下可能没有 FileStorage/File）")
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

        self.tree.delete(*self.tree.get_children())
        for cat in CATEGORIES:
            if cat in by_cat:
                c = by_cat[cat]
                self.tree.insert("", "end", values=(cat, c[0], human(c[1])))
        if "其他" not in by_cat and any(k not in CATEGORIES for k in by_cat):
            for cat in by_cat:
                if cat not in CATEGORIES:
                    c = by_cat[cat]
                    self.tree.insert("", "end", values=(cat, c[0], human(c[1])))

        self.stat_var.set(
            "文件总数 %d | 总大小 %s | 重复文件 %d 个（去重可省 %s）"
            % (total, human(total_size), dup_count, human(dup_recover)))

        self.log("扫描完成: 共 %d 个文件，总大小 %s" % (total, human(total_size)))
        self.log("按类型: " + "，".join(
            "%s %d" % (k, v[0]) for k, v in sorted(by_cat.items(),
                                                   key=lambda kv: -kv[1][1])))
        if dup_count:
            self.log("发现重复文件 %d 个，去重可节省 %s（勾选「去重」后归类会跳过重复项）"
                     % (dup_count, human(dup_recover)))
        self.log("预览就绪。点击「一键归类」将文件复制到输出目录（源文件不动）。")
        self.status.set("扫描完成，可归类")

    def apply_organize(self):
        if not self.records:
            return
        dest = self.dest_dir.get().strip()
        if not dest:
            messagebox.showerror("错误", "请先选择输出目录。")
            return
        n = len(self.records)
        ans = messagebox.askyesno(
            "确认归类",
            "即将把 %d 个文件复制到:\n%s\n\n源文件不会被删除或移动（仅复制）。\n是否继续？"
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
        scheme = self.scheme.get()
        dedupe = self.dedupe.get()
        os.makedirs(dest, exist_ok=True)
        used = set()
        seen_hash = set()
        copied = skipped_dup = 0
        for r in self.records:
            if dedupe and r["hash"] in seen_hash:
                skipped_dup += 1
                continue
            seen_hash.add(r["hash"])
            dp = dest_path(dest, scheme, r["cat"], r["month"],
                           os.path.basename(r["path"]), used)
            try:
                os.makedirs(os.path.dirname(dp), exist_ok=True)
                shutil.copy2(r["path"], dp)
                copied += 1
            except OSError as e:
                self.master.after(0, lambda m=str(e): self.log("[WARN] 复制失败: " + m))
        self.master.after(0, self._on_apply_done, dest, copied, skipped_dup)

    def _on_apply_done(self, dest, copied, skipped_dup):
        self.progress.stop()
        self.scan_btn.configure(state="normal")
        self.status.set("归类完成")
        self.log("[OK] 已归类完成，复制 %d 个文件到: %s" % (copied, dest))
        if skipped_dup:
            self.log("      去重跳过 %d 个重复文件" % skipped_dup)
        self.log("源文件未做任何改动。")
        messagebox.showinfo("完成", "已复制 %d 个文件到:\n%s" % (copied, dest))


def main():
    root = tk.Tk()
    OrganizerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
