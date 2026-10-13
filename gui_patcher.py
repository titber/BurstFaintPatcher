#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BurstFaintPatcher GUI
=====================
圣光爆发(Burst)晕厥判定的跨版本静态补丁工具。

底层引擎 BurstFaintEngine(.exe) 用 dnlib 按 *名字* 定位
nel.M2PrSkill.runBurst 里的晕厥判定：
    if (X.XORSP() < this.BurstSel.fainted_ratio || num == 0f)
改为：
    if (this.BurstSel.fainted_ratio >= 1f || num == 0f)
即只在晕厥概率 >= 100% 时才晕厥。

因为按名字(而非硬编码 token)定位，所以对任意 IL 形态相同的游戏版本都能用；
若 DLL 被人为改动/不兼容导致找不到标准特征码，则拒绝修改并弹窗提示，保证不破坏程序集。

改进点（本版本）：
  1. 选择/修改 DLL 路径时自动清除「打补丁」按钮的可用状态（需重新检测）。
  2. 「检测状态 / 打补丁」在后台线程执行，界面不再卡住；引擎在上启动时预热，
     首跑不再承担 .NET 运行时解压的冷启动开销。

用法：python gui_patcher.py   或   打包后的 BurstFaintPatcher.exe
"""
import os
import sys
import json
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

APP_TITLE = "圣光爆发晕厥判定补丁工具 (跨版本)"

# 内嵌 MIT 协议文本（与同目录 LICENSE 文件一致），保证打包后的 exe 内也可查看
MIT_LICENSE = """MIT License

Copyright (c) 2026 titber

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def find_engine():
    if getattr(sys, "frozen", False):
        # PyInstaller 单文件：引擎被解压到 _MEIPASS
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.join(base, "BurstFaintEngine.exe")
    if not os.path.isfile(cand):
        # 兜底：与脚本/可执行文件同目录
        cand2 = os.path.join(os.path.dirname(sys.executable), "BurstFaintEngine.exe")
        if os.path.isfile(cand2):
            return cand2
        return cand
    return cand


ENGINE = find_engine()


def run_engine(args):
    """调用引擎，返回解析后的 dict；失败返回 {'status':'error','detail':...}"""
    if not os.path.isfile(ENGINE):
        return {"status": "error", "detail": "未找到补丁引擎 BurstFaintEngine.exe（请将其与本程序放在同一目录）", "out": ""}
    try:
        proc = subprocess.run(
            [ENGINE] + args,
            capture_output=True, timeout=120,
        )
        raw = proc.stdout or b""
        # 优先 utf-8；回退 GBK(中文系统管道常见)/latin-1，保证不抛异常
        out = None
        for enc in ("utf-8", "cp936", "latin-1"):
            try:
                out = raw.decode(enc)
                break
            except Exception:
                out = raw.decode("latin-1")
        out = (out or "").strip()
        for line in reversed(out.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                return json.loads(line)
        if out:
            return {"status": "error", "detail": "引擎输出无法解析: " + out[:200], "out": ""}
        return {"status": "error", "detail": (proc.stderr or b"").decode("latin-1", "replace").strip()[:200], "out": ""}
    except subprocess.TimeoutExpired:
        return {"status": "error", "detail": "引擎执行超时", "out": ""}
    except Exception as e:  # noqa
        return {"status": "error", "detail": "调用引擎异常: " + str(e), "out": ""}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        try:
            self.iconbitmap()  # noqa  (无图标时无副作用)
        except Exception:
            pass
        self.geometry("640x460")
        self.resizable(True, True)

        self.dll_path = tk.StringVar()
        self.inplace = tk.BooleanVar(value=False)
        self.last_scan = None        # 缓存最近一次 scan 结果
        self.busy = False            # 是否有后台任务在跑（防止重入/重复点击）
        self.scan_token = 0          # 路径变更计数，用于丢弃过期的后台结果

        self._build_ui()
        # 路径一旦变化（浏览选择 / 手动输入 / 清空），复位检测/打补丁状态
        self.dll_path.trace_add("write", self._on_path_changed)

        self._log(f"补丁引擎: {ENGINE}")
        if not os.path.isfile(ENGINE):
            self._log("⚠ 警告：未找到 BurstFaintEngine.exe，请将其与本程序放在同一目录。", warn=True)
        else:
            # 预热：后台触发引擎（提前完成 .NET 运行时解压），避免首次检测卡顿
            threading.Thread(target=self._warmup, daemon=True).start()

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}
        # ---- 文件选择 ----
        frm = ttk.LabelFrame(self, text="目标 DLL", padding=6)
        frm.pack(fill="x", padx=8, pady=6)
        self.path_entry = ttk.Entry(frm, textvariable=self.dll_path)
        self.path_entry.pack(side="left", fill="x", expand=True, **pad)
        self.btn_browse = ttk.Button(frm, text="浏览...", command=self._browse)
        self.btn_browse.pack(side="left", **pad)

        # ---- 操作 ----
        ops = ttk.Frame(self)
        ops.pack(fill="x", padx=8, pady=2)
        self.btn_scan = ttk.Button(ops, text="检测状态", command=self._on_scan)
        self.btn_scan.pack(side="left", **pad)
        self.btn_patch = ttk.Button(ops, text="打补丁", command=self._on_patch, state="disabled")
        self.btn_patch.pack(side="left", **pad)
        ttk.Checkbutton(ops, text="原地修改（自动备份 .bak）", variable=self.inplace).pack(side="left", **pad)
        ttk.Button(ops, text="关于/许可", command=self._on_about).pack(side="right", **pad)

        # ---- 状态 ----
        self.status_var = tk.StringVar(value="尚未检测")
        self.status_lbl = ttk.Label(self, textvariable=self.status_var, font=("Microsoft YaHei", 11, "bold"))
        self.status_lbl.pack(anchor="w", padx=10, pady=(2, 0))
        self.detail_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.detail_var, foreground="#555555", wraplength=600).pack(anchor="w", padx=10, pady=(0, 4))

        # ---- 日志 ----
        log_frm = ttk.LabelFrame(self, text="日志", padding=6)
        log_frm.pack(fill="both", expand=True, padx=8, pady=4)
        self.log = scrolledtext.ScrolledText(log_frm, height=12, font=("Consolas", 9))
        self.log.tag_configure("ok", foreground="#1a7f37")
        self.log.tag_configure("warn", foreground="#cf222e")
        self.log.pack(fill="both", expand=True)

    # ---------- 路径变更复位 ----------
    def _on_path_changed(self, *args):
        # 任何路径变化都视为“需要重新检测”：禁用打补丁、复位状态展示
        self.scan_token += 1
        if not self.busy:
            self.btn_patch.configure(state="disabled")
            self.status_var.set("尚未检测")
            self.detail_var.set("")
            self.last_scan = None

    def _browse(self):
        p = filedialog.askopenfilename(title="选择 Assembly-CSharp.dll", filetypes=[("DLL 文件", "*.dll"), ("所有文件", "*.*")])
        if p:
            self.dll_path.set(p)   # trace 会自动触发 _on_path_changed 复位
            self._log(f"已选择: {p}")

    # ---------- 关于 / 许可 ----------
    def _license_text(self):
        # 优先读取随程序分发的 LICENSE 文件；找不到则用内嵌文本
        try:
            if getattr(sys, "frozen", False):
                base = sys._MEIPASS
            else:
                base = os.path.dirname(os.path.abspath(__file__))
            for cand in (os.path.join(base, "LICENSE"),
                         os.path.join(os.path.dirname(sys.executable), "LICENSE")):
                if os.path.isfile(cand):
                    with open(cand, "r", encoding="utf-8") as f:
                        return f.read()
        except Exception:
            pass
        return MIT_LICENSE

    def _on_about(self):
        win = tk.Toplevel(self)
        win.title("关于 / 许可")
        win.geometry("620x460")
        win.transient(self)
        head = tk.Label(win, text="BurstFaintPatcher  —  圣光爆发晕厥判定补丁工具\n以 MIT 许可证发布",
                        font=("Microsoft YaHei", 11, "bold"), justify="left", anchor="w")
        head.pack(fill="x", padx=12, pady=(10, 4))
        txt = scrolledtext.ScrolledText(win, font=("Consolas", 9), wrap="word")
        txt.pack(fill="both", expand=True, padx=12, pady=4)
        txt.insert("1.0", self._license_text())
        txt.configure(state="disabled")
        ttk.Button(win, text="关闭", command=win.destroy).pack(pady=8)

    # ---------- 锁定 / 解锁界面 ----------
    def _set_busy(self, on):
        self.busy = on
        if on:
            self.config(cursor="wait")
            self.btn_scan.configure(state="disabled")
            self.btn_patch.configure(state="disabled")
            self.btn_browse.configure(state="disabled")
            self.path_entry.configure(state="disabled")
        else:
            self.config(cursor="")
            self.btn_scan.configure(state="normal")
            self.btn_browse.configure(state="normal")
            self.path_entry.configure(state="normal")
        self.update_idletasks()

    # ---------- 状态显示 ----------
    def _set_status(self, status, detail):
        colormap = {
            "pristine": ("可以打补丁（原始状态）", "#1a7f37"),
            "patched": ("已是目标状态（无需修改）", "#1f6feb"),
            "modified": ("⛔ 疑似被人为改动 / 不兼容，已拒绝修改", "#cf222e"),
            "ambiguous": ("⛔ 特征码不唯一，已拒绝修改", "#cf222e"),
            "error": ("⛔ 出错", "#cf222e"),
        }
        text, color = colormap.get(status, (status, "#555555"))
        self.status_var.set(text)
        self.detail_var.set(detail or "")
        self.status_lbl.configure(foreground=color)

    # ---------- 检测状态（后台线程） ----------
    def _on_scan(self):
        if self.busy:
            return
        dll = self.dll_path.get().strip()
        if not dll:
            messagebox.showwarning("提示", "请先选择 Assembly-CSharp.dll")
            return
        if not os.path.isfile(dll):
            messagebox.showerror("错误", "文件不存在:\n" + dll)
            return
        token = self.scan_token
        self._set_busy(True)
        self.status_var.set("检测中…")
        self.detail_var.set("")
        self.btn_patch.configure(state="disabled")
        self._log(f"[检测] {dll}")
        threading.Thread(target=self._scan_worker, args=(dll, token), daemon=True).start()

    def _scan_worker(self, dll, token):
        res = run_engine(["scan", dll])
        self.after(0, self._scan_done, res, token)

    def _scan_done(self, res, token):
        if token != self.scan_token:
            return  # 已切换文件，丢弃过期结果
        self._set_busy(False)
        self.last_scan = res
        self._set_status(res.get("status", "error"), res.get("detail", ""))
        self._log(f"  结果: {res.get('status')} - {res.get('detail')}")
        if res.get("status") == "pristine":
            self.btn_patch.configure(state="normal")
            self._log("  → 可以打补丁。", ok=True)
        else:
            self.btn_patch.configure(state="disabled")
            if res.get("status") in ("modified", "ambiguous", "error"):
                self._log("  → 拒绝修改（不会写入任何字节）。", warn=True)

    # ---------- 打补丁（后台线程） ----------
    def _on_patch(self):
        if self.busy:
            return
        dll = self.dll_path.get().strip()
        if not dll or not os.path.isfile(dll):
            messagebox.showerror("错误", "请先选择有效的 DLL")
            return
        use_inplace = self.inplace.get()
        args = ["patch", dll]
        if use_inplace:
            args.append("--inplace")
        token = self.scan_token
        self._set_busy(True)
        self.status_var.set("打补丁中…")
        self._log(f"[打补丁] {dll}  (原地={'是' if use_inplace else '否'})")
        threading.Thread(target=self._patch_worker, args=(args, token), daemon=True).start()

    def _patch_worker(self, args, token):
        res = run_engine(args)
        self.after(0, self._patch_done, res, token)

    def _patch_done(self, res, token):
        if token != self.scan_token:
            return
        self._set_busy(False)
        self.last_scan = res
        st = res.get("status")
        if st == "pristine_patched":
            out = res.get("out", "")
            self._set_status("patched", res.get("detail", ""))
            self.btn_patch.configure(state="disabled")
            self._log(f"  ✓ 成功！输出: {out}", ok=True)
            messagebox.showinfo("完成", "补丁已应用。\n" + (("输出文件: " + out) if out else "已原地修改（原文件已备份为 .bak）"))
        elif st == "patched":
            self._set_status("patched", res.get("detail", ""))
            self.btn_patch.configure(state="disabled")
            self._log("  → 已经是目标状态，未做任何修改。", ok=True)
            messagebox.showinfo("提示", "该 DLL 已经是目标状态，无需修改。")
        else:
            # modified / ambiguous / error：引擎已拒绝写入
            self._set_status(st, res.get("detail", ""))
            self.btn_patch.configure(state="disabled")
            self._log(f"  ⛔ 未修改：{res.get('detail')}", warn=True)
            messagebox.showerror("已中止", res.get("detail", "该 DLL 无法安全打补丁，已拒绝修改。"))

    # ---------- 引擎预热 ----------
    def _warmup(self):
        try:
            r = run_engine(["version"])
            if r.get("status") == "ok":
                self.after(0, lambda: self._log("引擎预热完成（.NET 运行时已就绪）。", ok=True))
        except Exception:
            pass

    # ---------- 日志 ----------
    def _log(self, msg, ok=False, warn=False):
        tag = None
        if ok:
            tag = "ok"
        elif warn:
            tag = "warn"
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n", tag)
        if tag:
            start = self.log.index("end-2c linestart")
            end = self.log.index("end-1c")
            self.log.tag_add(tag, start, end)
        self.log.configure(state="disabled")
        self.log.see("end")


if __name__ == "__main__":
    app = App()
    app.mainloop()
