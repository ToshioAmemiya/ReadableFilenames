# ReadableFilenames_WeakMid_Workscreen.py
# -*- coding: utf-8 -*-

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog


class GenreEditor(tk.Toplevel):
    def __init__(self, master, genres, current, on_done):
        super().__init__(master)
        self.title("ジャンル編集")
        self.geometry("420x300")
        self.resizable(True, True)

        self.genres = list(genres)
        self.on_done = on_done

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        ttk.Label(self, text="ジャンルのみ編集（式には影響しません）").grid(
            row=0, column=0, sticky="w", padx=10, pady=(10, 0)
        )

        self.listbox = tk.Listbox(self)
        self.listbox.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)

        btns = ttk.Frame(self)
        btns.grid(row=2, column=0, sticky="e", padx=10, pady=(0, 10))

        ttk.Button(btns, text="追加", command=self.add).pack(side="left", padx=4)
        ttk.Button(btns, text="名前変更", command=self.rename).pack(side="left", padx=4)
        ttk.Button(btns, text="削除", command=self.delete).pack(side="left", padx=4)
        ttk.Button(btns, text="OK", command=self.done).pack(side="left", padx=12)

        self.refresh()

        if current in self.genres:
            i = self.genres.index(current)
            self.listbox.selection_set(i)
            self.listbox.see(i)

        self.transient(master)
        self.grab_set()

    def refresh(self):
        self.listbox.delete(0, "end")
        for g in self.genres:
            self.listbox.insert("end", g)

    def add(self):
        name = simpledialog.askstring("追加", "ジャンル名：", parent=self)
        if name:
            name = name.strip()
            if name and name not in self.genres:
                self.genres.append(name)
                self.refresh()

    def rename(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        old = self.genres[sel[0]]
        name = simpledialog.askstring("名前変更", "新しい名前：", initialvalue=old, parent=self)
        if name:
            self.genres[sel[0]] = name.strip()
            self.refresh()

    def delete(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        del self.genres[sel[0]]
        if not self.genres:
            self.genres.append("未分類")
        self.refresh()

    def done(self):
        self.on_done(self.genres)
        self.destroy()


class WeakMidScreen(ttk.Frame):
    def __init__(self, master, strength, patterns, mid_flags=None, genres=None, on_apply=None):
        super().__init__(master, padding=8)
        self.master = master
        self.strength = strength  # "弱" or "中"
        self.on_apply = on_apply

        self.patterns = list(patterns)
        self.mid_flags = set(mid_flags or [])
        self.genres = list(genres or ["未分類"])
        self.current_genre = tk.StringVar(value=self.genres[0])

        # genre -> [patterns]
        self.genre_map = {g: [] for g in self.genres}

        self._build_ui()
        self._populate_lists()

    def _build_ui(self):
        self.master.title(f"WEAK / MID 作業画面（{self.strength}）")
        self.master.geometry("1100x650")

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.grid(row=0, column=0, sticky="nsew")

        # 左：式リスト（材料）
        left = ttk.LabelFrame(panes, text="式リスト（材料）", padding=8)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        self.left_list = tk.Listbox(left, selectmode="extended")
        self.left_list.grid(row=0, column=0, sticky="nsew")

        sb1 = ttk.Scrollbar(left, orient="vertical", command=self.left_list.yview)
        sb1.grid(row=0, column=1, sticky="ns")
        self.left_list.config(yscrollcommand=sb1.set)

        ops_l = ttk.Frame(left)
        ops_l.grid(row=1, column=0, columnspan=2, sticky="w", pady=6)

        ttk.Button(ops_l, text="↑", width=4, state="disabled").pack(side="left", padx=2)
        ttk.Button(ops_l, text="↓", width=4, state="disabled").pack(side="left", padx=2)
        ttk.Button(ops_l, text="追加 →", command=self.add_to_genre).pack(side="left", padx=12)

        # 右：ジャンル箱
        right = ttk.LabelFrame(panes, text="ジャンル箱", padding=8)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        top = ttk.Frame(right)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="ジャンル").grid(row=0, column=0, sticky="w")
        self.genre_combo = ttk.Combobox(
            top, values=self.genres, textvariable=self.current_genre, state="readonly"
        )
        self.genre_combo.grid(row=0, column=1, sticky="ew", padx=6)
        self.genre_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_right())

        ttk.Button(top, text="編集", width=8, command=self.edit_genres).grid(row=0, column=2)

        self.right_list = tk.Listbox(right, selectmode="extended")
        self.right_list.grid(row=1, column=0, sticky="nsew")

        sb2 = ttk.Scrollbar(right, orient="vertical", command=self.right_list.yview)
        sb2.grid(row=1, column=1, sticky="ns")
        self.right_list.config(yscrollcommand=sb2.set)

        ops_r = ttk.Frame(right)
        ops_r.grid(row=2, column=0, columnspan=2, sticky="w", pady=6)

        ttk.Button(ops_r, text="← 削除", command=self.remove_from_genre).pack(side="left")

        panes.add(left, weight=1)
        panes.add(right, weight=1)

        # 下部（最小）
        bottom = ttk.Frame(self)
        bottom.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        bottom.columnconfigure(0, weight=1)

        self.status = ttk.Label(
            bottom, text=f"ジャンル：{self.current_genre.get()}　／　強度：{self.strength}"
        )
        self.status.grid(row=0, column=0, sticky="w")

        ttk.Button(bottom, text="適用", command=self.apply).grid(row=0, column=1, sticky="e")

        self.pack(fill="both", expand=True)

    def _populate_lists(self):
        self.left_list.delete(0, "end")
        for p in self.patterns:
            mark = "⚠ " if p in self.mid_flags else ""
            self.left_list.insert("end", mark + p)
        self.refresh_right()

    def refresh_right(self):
        g = self.current_genre.get()
        self.status.config(text=f"ジャンル：{g}　／　強度：{self.strength}")
        self.right_list.delete(0, "end")
        for p in self.genre_map.get(g, []):
            self.right_list.insert("end", p)

    def move_left(self, delta):
        sel = self.left_list.curselection()
        if len(sel) != 1:
            return
        i = sel[0]
        j = i + delta
        if not (0 <= j < len(self.patterns)):
            return
        self.patterns[i], self.patterns[j] = self.patterns[j], self.patterns[i]
        self._populate_lists()
        self.left_list.selection_set(j)

    def add_to_genre(self):
        g = self.current_genre.get()
        for i in self.left_list.curselection():
            p = self.patterns[i]
            if p not in self.genre_map[g]:
                self.genre_map[g].append(p)
        self.refresh_right()

    def remove_from_genre(self):
        g = self.current_genre.get()
        for i in reversed(self.right_list.curselection()):
            del self.genre_map[g][i]
        self.refresh_right()

    def edit_genres(self):
        def done(new_genres):
            old = self.genre_map
            self.genres = list(new_genres) if new_genres else ["未分類"]
            self.genre_map = {g: old.get(g, []) for g in self.genres}
            self.genre_combo["values"] = self.genres
            self.current_genre.set(self.genres[0])
            self.refresh_right()

        GenreEditor(self.master, self.genres, self.current_genre.get(), done)

    def apply(self):
        payload = {
            "strength": self.strength,
            "order": list(self.patterns),
            "genres": {g: list(v) for g, v in self.genre_map.items()},
        }
        if "未分類" not in payload["genres"]:
            payload["genres"]["未分類"] = []
        if self.on_apply:
            try:
                self.on_apply(payload)
            except Exception as e:
                messagebox.showerror("エラー", f"適用に失敗しました:\n{e}", parent=self.master)
                return
        else:
            print(payload)
        messagebox.showinfo("適用", "検索画面に反映しました。", parent=self.master)


# ---- 単体起動デモ ----
def main():
    patterns = [
        "(Album Version)",
        "[Batch]",
        "[1080p]",
        "[RAW]",
    ]
    mid_flags = {"[Batch]"}  # ⚠ 表示対象（静的）

    root = tk.Tk()
    WeakMidScreen(
        root,
        strength="中",
        patterns=patterns,
        mid_flags=mid_flags,
        genres=["アニメ", "映画", "その他"],
    )
    root.mainloop()


if __name__ == "__main__":
    main()
