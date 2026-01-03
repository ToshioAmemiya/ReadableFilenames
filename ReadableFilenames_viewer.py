# -*- coding: utf-8 -*-
"""
AIタイトルビューア（検索＋工房モード）
- 検索モード：
  左：検索候補（整形タイトル / key）…数字主体/短すぎはスライダーで非表示
  右：親フォルダ（左の選択に追従表示、フルパスは出さずSUB表示のみ）
  ダブルクリック＝デフォルト検索、右クリック＝検索エンジン選択
  検索エンジンはアプリ内で編集・保存可

- 工房モード（チュートリアル）：
  フォルダ選択 → 開始 → 送信（ノイズ入り→送信、ノイズなし→送信）で工房を前面表示
  工房モードでは検索エンジン関連UIとジャンルUIは非表示

※ 収集モードは廃案のため、このファイルには存在しません
"""
import os
import sys
import json
import time
import subprocess
import urllib.parse
import webbrowser
import re
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import importlib.util
import traceback


# =====================
# Tooltip (simple, no external deps)
# =====================
class ToolTip:
    def __init__(self, widget, text, *, delay_ms=450):
        self.widget = widget
        self.text = text or ""
        self.delay_ms = int(delay_ms)
        self._win = None
        self._after = None
        self._x = 0
        self._y = 0

        widget.bind("<Enter>", self._on_enter, add=True)
        widget.bind("<Leave>", self._on_leave, add=True)
        widget.bind("<Motion>", self._on_motion, add=True)

    def set_text(self, text):
        self.text = text or ""

    def _on_motion(self, e):
        self._x = e.x_root
        self._y = e.y_root

    def _on_enter(self, _e=None):
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except Exception:
                pass
        # Ensure we have a sane anchor position even if the mouse doesn't move
        try:
            self._x = int(self.widget.winfo_pointerx())
            self._y = int(self.widget.winfo_pointery())
        except Exception:
            try:
                self._x = int(self.widget.winfo_rootx()) + 10
                self._y = int(self.widget.winfo_rooty()) + int(self.widget.winfo_height()) + 10
            except Exception:
                self._x, self._y = 20, 20
        self._after = self.widget.after(self.delay_ms, self._show)

    def _on_leave(self, _e=None):
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except Exception:
                pass
            self._after = None
        self._hide()

    def _show(self):
        self._after = None
        if self._win is not None:
            return
        if not self.text.strip():
            return
        try:
            win = tk.Toplevel(self.widget)
            win.wm_overrideredirect(True)
            win.attributes("-topmost", True)
            lbl = tk.Label(
                win,
                text=self.text,
                justify="left",
                background="#ffffe0",
                relief="solid",
                borderwidth=1,
                font=("Segoe UI", 9),
                padx=8,
                pady=6,
            )
            lbl.pack()
            x = (self._x or self.widget.winfo_rootx()) + 12
            y = (self._y or self.widget.winfo_rooty()) + 18
            win.wm_geometry(f"+{x}+{y}")
            self._win = win
        except Exception:
            self._win = None

    def _hide(self):
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None

def _load_workshop_panel():
    """Load WorkshopPanel / StrongSaveWindow from ReadableFilenames_workshop.py next to this viewer.
    Avoids CWD issues and shows the real import error when it fails.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    ws_path = os.path.join(base_dir, "ReadableFilenames_workshop.py")
    if not os.path.exists(ws_path):
        return None, None, f"ReadableFilenames_workshop.py が見つかりません\n{ws_path}"
    try:
        spec = importlib.util.spec_from_file_location("ReadableFilenames_workshop", ws_path)
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)  # type: ignore
        return getattr(mod, "WorkshopPanel", None), getattr(mod, "StrongSaveWindow", None), None
    except Exception:
        return None, None, traceback.format_exc()


WorkshopPanel, StrongSaveWindow, _WORKSHOP_IMPORT_ERROR = _load_workshop_panel()
APP_NAME = "Readable Filenames"
WORKSHOP_PY = "ReadableFilenames_workshop.py"

LOCK_FILE = "_ai_title_workshop_lock.json"
IPC_INBOX = "_ai_title_workshop_inbox.jsonl"
SETTINGS_JSON = "_ai_title_viewer_settings.json"
IGNORE_JSON = "_ai_title_ignore_words.json"
SAMPLES_JSON = "ReadableFilenames_samples.json"
STATE_JSON = "ReadableFilenames_last_send.json"

N_TARGET = 10
K_TARGET = 10

DEFAULT_ENGINE_DEFS = [
    {"name": "AI", "url": "https://www.perplexity.ai/search?q={q}"},
    {"name": "Google", "url": "https://www.google.com/search?q={q}"},
    {"name": "Bing", "url": "https://www.bing.com/search?q={q}"},
]
DEFAULT_GENRES = ["アニメ", "音楽", "ドラマ", "映画", "その他"]


def app_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))

def get_app_dir() -> str:
    # Backward-compatible alias
    return app_dir()



def load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def normalize_engine_defs(defs):
    out = []
    seen = set()
    if isinstance(defs, list):
        for d in defs:
            if not isinstance(d, dict):
                continue
            name = str(d.get("name", "") or "").strip()
            url = str(d.get("url", "") or "").strip()
            if not name or not url:
                continue
            if name in seen:
                continue
            seen.add(name)
            out.append({"name": name, "url": url})
    if not out:
        out = [dict(x) for x in DEFAULT_ENGINE_DEFS]
    return out


def engine_names(defs):
    return [d["name"] for d in normalize_engine_defs(defs)]


def open_url(engine: str, text: str, engine_defs):
    q = urllib.parse.quote(text)
    defs = normalize_engine_defs(engine_defs)
    url = None
    for d in defs:
        if d.get("name") == engine:
            url = str(d.get("url") or "")
            break
    if not url:
        url = "https://www.google.com/search?q={q}"
    if "{q}" in url:
        url = url.replace("{q}", q)
    else:
        # 保険：{q} が無い場合は末尾に付ける
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}q={q}"
    webbrowser.open_new_tab(url)


def minimal_clean_for_search(title: str) -> str:
    s = (title or "").strip()
    while "  " in s:
        s = s.replace("  ", " ")
    return s



def safe_load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def safe_save_json(path: str, data) -> bool:
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def apply_patterns_for_genre(raw_title: str, applied_state: dict, genre_name: str) -> str:
    """保存工房で最後に［適用］した式を、検索モードの候補生成にだけ反映する。

    ルール（確定仕様）:
    - 検索モードは「最後に適用した状態」だけを見る
    - ジャンルを選ぶと、そのジャンルに属する式（＋未分類）を混ぜて適用する
    - 失敗（例: 正規表現エラー）はその式だけ無視する（候補生成を止めない）
    """
    s = (raw_title or "")
    if not applied_state or not isinstance(applied_state, dict):
        return s

    genres = applied_state.get("genres")
    if not isinstance(genres, dict):
        return s

    # mix: 未分類 + 選択ジャンル
    selected = []
    for g in ("未分類", genre_name):
        lst = genres.get(g)
        if isinstance(lst, list):
            selected.extend([str(x) for x in lst])

    if not selected:
        return s

    order = applied_state.get("order")
    if isinstance(order, list) and order:
        ordered = [p for p in order if p in selected]
        # allow patterns present but not in order
        tail = [p for p in selected if p not in ordered]
        patterns = ordered + tail
    else:
        patterns = selected

    for pat in patterns:
        try:
            s = re.sub(pat, " ", s)
        except Exception:
            # ignore broken patterns; user responsibility
            continue
    # normalize spaces after removals (avoid word-join accidents)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def process_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def lock_running(lock_path, max_age_sec=None) -> bool:
    """Return True if the workshop process is alive.

    We rely on PID liveness. Timestamp refresh is optional and should not be required,
    so the lock file does not need to be rewritten constantly.
    """
    d = load_json(lock_path, None)
    if not isinstance(d, dict):
        return False
    pid = int(d.get("pid", 0) or 0)
    if not process_alive(pid):
        return False
    # If timestamp is present and max_age_sec is set, use it as an extra safety check.
    if max_age_sec is not None:
        try:
            ts = float(d.get("ts", 0.0) or 0.0)
        except Exception:
            ts = 0.0
        if ts and (time.time() - ts) > float(max_age_sec):
            return False
    return True


def reset_workshop_inbox():
    # 今回分だけ確実に渡すため、inbox と cursor/state を初期化
    try:
        inbox = os.path.join(app_dir(), IPC_INBOX)
        with open(inbox, "w", encoding="utf-8") as f:
            f.write("")
    except Exception:
        pass
    for fn in ("_ai_title_workshop_cursor.json", "_ai_title_workshop_state.json"):
        try:
            p = os.path.join(app_dir(), fn)
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass


def append_inbox(msg: dict):
    path = os.path.join(app_dir(), IPC_INBOX)
    msg = dict(msg)
    msg.setdefault("id", f"v_{int(time.time()*1000)}_{os.getpid()}")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(msg, ensure_ascii=False) + "\n")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1200x860")
        self.minsize(1040, 760)

        # paths
        self._settings_path = os.path.join(app_dir(), SETTINGS_JSON)
        self._ignore_path = os.path.join(app_dir(), IGNORE_JSON)
        self._ws_lock_path = os.path.join(app_dir(), LOCK_FILE)
        self._ws_py_path = os.path.join(app_dir(), WORKSHOP_PY)

        # load settings
        st = load_json(self._settings_path, {})
        self.engine_defs = normalize_engine_defs(st.get("engine_defs"))
        cur_names = engine_names(self.engine_defs)
        de = str(st.get("default_engine", "AI") or "AI")
        if de not in cur_names:
            de = cur_names[0] if cur_names else "Google"

        self.default_engine = tk.StringVar(value=de)
        self.genre = tk.StringVar(value=st.get("genre", DEFAULT_GENRES[0] if DEFAULT_GENRES else "その他"))
        self.mode = tk.StringVar(value="SEARCH")
        self.folder = st.get("last_folder", "")

        # data rows: {"raw":..., "key":..., "parent_name":..., "parent_path":...}
        self.rows = []

        # workshop tutorial state
        self.tutorial_phase = 0
        self.queue = []

        # filter strength
        self.var_filter_strength = tk.IntVar(value=int(st.get("filter_strength", 60) or 60))

        # parent display state
        self._key_to_parents = {}  # key -> list of dicts {name, path}
        self._current_parent_name_for_search = ""

        self._build_ui()

        # --- applied snapshot (from 保存工房［適用］) ---
        self.applied_current = None
        self.applied_prev = None
        self._applied_mtime = None
        self.after(400, self._poll_applied_state)

        # initial folder load
        if self.folder and os.path.isdir(self.folder):
            self.lbl_folder.config(text=self.folder)
            self._load_folder(self.folder)

        # apply initial mode
        self.set_mode(self.mode.get())

        # close handler
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- UI ----------
    def _build_ui(self):
        # menubar
        menubar = tk.Menu(self)
        m_app = tk.Menu(menubar, tearoff=0)
        m_app.add_command(label="保存工房（強）", command=self.open_strong_save_front)
        m_app.add_separator()
        m_app.add_command(label="強リポの管理…", command=self.open_strong_repo_settings)
        m_app.add_command(label="使い方（準備中）", state="disabled")
        menubar.add_cascade(label="メニュー", menu=m_app)
        try:
            self.config(menu=menubar)
        except Exception:
            pass

        # header
        header = ttk.Frame(self)
        header.pack(fill="x", padx=10, pady=(10, 6))
        ttk.Label(header, text=APP_NAME, font=("Segoe UI", 14, "bold")).pack(side="left")
        # mode badge (helps users instantly know where they are)
        self.lbl_mode_badge = ttk.Label(header, text="", font=("Segoe UI", 11, "bold"))
        self.lbl_mode_badge.pack(side="right")

        # tooltip on mode badge (right-top) - shows 'how to' guidance
        self._tt_mode_badge = ToolTip(self.lbl_mode_badge, "", delay_ms=450)


        # big pillar buttons
        pillars = ttk.Frame(self)
        pillars.pack(fill="x", padx=10, pady=(0, 8))
        self.btn_search = ttk.Button(pillars, text="検索モード", command=lambda: self.set_mode("SEARCH"))
        self.btn_work = ttk.Button(pillars, text="作業工房", command=lambda: self.set_mode("WORKSHOP"))

        # tooltips (mode guidance)
        self._tt_search = ToolTip(self.btn_search, "検索モード：\n1) フォルダを選ぶ\n2) 必要なら探索深さを変更\n3) サンプル/トークン素材が更新されます")
        self._tt_work = ToolTip(self.btn_work, "作業工房：\n- 検索モードで作った素材（samples）を使います\n- ここではフォルダは変更しません\n- AI回答を貼り付け→適用で式リストに反映")
        self.btn_search.pack(side="left", fill="x", expand=True)
        self.btn_work.pack(side="left", fill="x", expand=True)

        # mode visuals (buttons + badge color)
        self._init_mode_styles()
        self._apply_mode_visuals(self.mode.get())

        # purpose line
        self.lbl_purpose = ttk.Label(self, text="", foreground="#333")
        # controls row (shared; modeによって表示/非表示)
        controls = ttk.Frame(self)
        controls.pack(fill="x", padx=10, pady=(6, 6))

        ttk.Button(controls, text="フォルダ選択", command=self.pick_folder).pack(side="left")
        self.lbl_folder = ttk.Label(controls, text="（未選択）", foreground="#555")
        self.lbl_folder.pack(side="left", padx=10, fill="x", expand=True)

        # search controls chunk
        self.frm_search_controls = ttk.Frame(controls)
        self.frm_search_controls.pack(side="right")

        ttk.Label(self.frm_search_controls, text="デフォルト検索:").pack(side="left", padx=(0, 4))
        self.cb_engine = ttk.Combobox(
            self.frm_search_controls,
            textvariable=self.default_engine,
            values=engine_names(self.engine_defs),
            width=10,
            state="readonly",
        )
        self.cb_engine.pack(side="left")
        self.cb_engine.bind("<<ComboboxSelected>>", lambda e: self._save_settings())

        ttk.Button(self.frm_search_controls, text="検索エンジン編集…", command=self._open_engine_editor).pack(
            side="left", padx=(8, 0)
        )

        ttk.Label(self.frm_search_controls, text="ジャンル:").pack(side="left", padx=(14, 4))
        self.cb_genre = ttk.Combobox(
            self.frm_search_controls, textvariable=self.genre, values=DEFAULT_GENRES, width=12, state="readonly"
        )
        self.cb_genre.pack(side="left")
        self.cb_genre.bind("<<ComboboxSelected>>", lambda e: (self._save_settings(), self._refresh_previews()))

        self.btn_undo_apply = ttk.Button(self.frm_search_controls, text="戻す", command=self._undo_applied_state)
        self.btn_undo_apply.pack(side="left", padx=(8, 0))
        self.btn_undo_apply.config(state="disabled")

        # content area
        self.stack = ttk.Frame(self)
        self.stack.pack(fill="both", expand=True, padx=10, pady=(6, 6))

        self.frame_search = ttk.Frame(self.stack)
        self.frame_work = ttk.Frame(self.stack)
        for fr in (self.frame_search, self.frame_work):
            fr.place(relx=0, rely=0, relwidth=1, relheight=1)

        self._build_search_frame()
        self._build_workshop_frame()

        # bottom status
        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(bottom, text="選択解除", command=self.clear_selection).pack(side="left")
        self.lbl_status = ttk.Label(bottom, text="", foreground="#444")
        self.lbl_status.pack(side="left", padx=10)

    def _build_search_frame(self):
        fr = self.frame_search

        # --- Modern-ish: let OS theme decide base bg; remove heavy borders; emphasize selected query ---
        st = ttk.Style()
        bg = st.lookup("TFrame", "background")
        if not bg:
            try:
                bg = self.cget("background")
            except Exception:
                bg = "SystemButtonFace"

        # A dedicated Treeview style with minimal borders (theme-safe)
        try:
            # Some OS themes ignore per-style fieldbackground; also set the base Treeview style as a fallback.
            st.configure("Treeview", borderwidth=0, relief="flat", background=bg, fieldbackground=bg)
            st.configure("Treeview.Heading", borderwidth=0, relief="flat")
            st.map("Treeview", background=[("selected", "SystemHighlight")], foreground=[("selected", "SystemHighlightText")])

            st.configure("RF.Treeview", borderwidth=0, relief="flat", background=bg, fieldbackground=bg)
            st.configure("RF.Treeview", rowheight=26)
            st.configure("RF.Treeview", font=("Segoe UI", 11))
            st.configure("RF.Treeview.Heading", borderwidth=0, relief="flat")
            st.layout("RF.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
            st.map("RF.Treeview", background=[("selected", "SystemHighlight")], foreground=[("selected", "SystemHighlightText")])
        except Exception:
            pass

        # filter slider
        bar = ttk.Frame(fr)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="表示フィルター（数字/短い名前）: 弱←→強").pack(side="left")
        self._slider_filter = ttk.Scale(
            bar, from_=0, to=100, orient="horizontal", command=lambda v: self._on_filter_slider()
        )
        self._slider_filter.pack(side="left", fill="x", expand=True, padx=8)
        self._slider_filter.set(self.var_filter_strength.get())
        ttk.Label(bar, text="（強いほど表示を減らす）").pack(side="left")

        # Selected query display (read-only, follows selection)
        if not hasattr(self, "var_selected_query"):
            self.var_selected_query = tk.StringVar(value="（候補を選択）")

        header = tk.Frame(fr, bg=bg)
        header.pack(fill="x", pady=(0, 10))
        self.lbl_selected_query = tk.Label(
            header,
            textvariable=self.var_selected_query,
            bg=bg,
            fg="SystemHighlight",
            font=("Segoe UI", 12, "bold"),
            wraplength=900,
            justify="left",
        )
        self.lbl_selected_query.pack(anchor="w", pady=(2, 0))

        panes = ttk.Frame(fr)
        panes.pack(fill="both", expand=True)

        left = ttk.Frame(panes)
        left.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(panes)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))

        # candidates list (with scrollbar)
        left_list = ttk.Frame(left)
        left_list.pack(fill="both", expand=True)

        self.tree_key = ttk.Treeview(left_list, columns=("key",), show="headings", selectmode="browse", style="RF.Treeview")
        self.tree_key.heading("key", text="ダブルクリックで検索")
        self.tree_key.column("key", width=520)

        sb_y = ttk.Scrollbar(left_list, orient="vertical", command=self.tree_key.yview)
        self.tree_key.configure(yscrollcommand=sb_y.set)

        self.tree_key.pack(side="left", fill="both", expand=True)
        sb_y.pack(side="right", fill="y")

        # parent follows selection
        self.var_parent_disp = tk.StringVar(value="（左でタイトルを選ぶと、ここに親フォルダが出ます）")
        self.var_parent_sub = tk.StringVar(value="")

        box = ttk.Frame(right)
        box.pack(fill="both", expand=True)

        # parent folder display (hoverable)
        self.lbl_parent = tk.Label(
            box,
            textvariable=self.var_parent_disp,
            font=("Segoe UI", 10),
            anchor="w",
            justify="left"
        )
        self.lbl_parent.pack(anchor="w", pady=(6, 2))

        self.lbl_parent_sub = tk.Label(
            box,
            textvariable=self.var_parent_sub,
            anchor="w",
            justify="left"
        )
        self.lbl_parent_sub.pack(anchor="w")

        # hover effects for parent folder labels
        def _parent_hover_on(e):
            e.widget.configure(font=("Segoe UI", 10, "bold"), cursor="hand2")

        def _parent_hover_off(e):
            e.widget.configure(font=("Segoe UI", 10), cursor="")

        for w in (self.lbl_parent, self.lbl_parent_sub):
            w.bind("<Enter>", _parent_hover_on)
            w.bind("<Leave>", _parent_hover_off)

        # parent search actions
        self.lbl_parent.bind("<Double-1>", lambda e: self._search_from_parent_label(default=True))
        self.lbl_parent.bind("<Button-3>", lambda e: self._popup_search_menu_for_parent(e))
        self.lbl_parent_sub.bind("<Button-3>", lambda e: self._popup_search_menu_for_parent(e))

        # events
        self.tree_key.bind("<Double-1>", lambda e: self._search_from_tree(self.tree_key))
        self.tree_key.bind("<Button-3>", lambda e: self._popup_search_menu(e, self.tree_key))
        self.tree_key.bind("<<TreeviewSelect>>", lambda e: self._on_key_select())

        # right click menu (dynamic)
        self.menu_search = tk.Menu(self, tearoff=0)
        # filled in _rebuild_search_menu()
    def _build_workshop_frame(self):
        """作業工房（統合版）: 準備画面は廃止し、この枠に工房UIを移植する。"""
        # このフレーム自体は set_mode() で lift される
        for w in self.frame_work.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass

        if WorkshopPanel is None:
            ttk.Label(self.frame_work, text="作業工房の読み込みに失敗しました。\n\n【詳細】\n" + (_WORKSHOP_IMPORT_ERROR or "(不明)") + "").pack(
                padx=20, pady=20, anchor="w"
            )
            return

        # 既存の工房（作業）UIを、この画面内に埋め込み
        self.workshop_panel = WorkshopPanel(self.frame_work)
        self.workshop_panel.pack(fill="both", expand=True)


    # ---------- Applied snapshot (適用 / 戻す) ----------
    def _state_json_path(self) -> str:
        return os.path.join(app_dir(), STATE_JSON)

    def _extract_genre_values(self, applied_state: dict):
        # DEFAULT_GENRES + applied_state.genres のキー（重複除去、順序維持）
        vals = list(DEFAULT_GENRES)
        if isinstance(applied_state, dict):
            gs = applied_state.get("genres")
            if isinstance(gs, dict):
                for k in list(gs.keys()):
                    k = str(k)
                    if k and k not in vals:
                        vals.append(k)
        return vals

    def _load_applied_state_from_disk(self):
        st = safe_load_json(self._state_json_path(), {})
        cur = st.get("applied_current") if isinstance(st, dict) else None
        prev = st.get("applied_prev") if isinstance(st, dict) else None
        self.applied_current = cur if isinstance(cur, dict) else None
        self.applied_prev = prev if isinstance(prev, dict) else None

        # ジャンル一覧を更新（自動適用はしない。表示値だけ同期）
        try:
            values = self._extract_genre_values(self.applied_current or {})
            self.cb_genre["values"] = values
            if self.genre.get() not in values:
                self.genre.set(values[0] if values else "その他")
        except Exception:
            pass

    def _poll_applied_state(self):
        """STATE_JSONの更新だけ監視して、検索候補表示に必要な 'applied_current' を読み直す。"""
        try:
            p = self._state_json_path()
            mt = os.path.getmtime(p) if os.path.exists(p) else None
            if mt != self._applied_mtime:
                self._applied_mtime = mt
                self._load_applied_state_from_disk()
                # 検索候補表示を更新（keyは適用状態で変わるのでフォルダを再読込）
                try:
                    if getattr(self, "folder", None) and os.path.isdir(self.folder):
                        self._load_folder(self.folder)
                    else:
                        self._refresh_previews()
                except Exception:
                    pass
        finally:
            try:
                self.after(500, self._poll_applied_state)
            except Exception:
                pass

    def _undo_applied_state(self):
        """検索モード：ひとつ前の適用状態に戻す（確定仕様）。"""
        p = self._state_json_path()
        st = safe_load_json(p, {})
        if not isinstance(st, dict):
            st = {}

        cur = st.get("applied_current")
        prev = st.get("applied_prev")
        if not isinstance(prev, dict):
            # 戻せるものがない
            try:
                messagebox.showinfo(APP_TITLE, "戻せる適用状態がありません。")
            except Exception:
                pass
            return

        st["applied_current"], st["applied_prev"] = prev, (cur if isinstance(cur, dict) else None)
        safe_save_json(p, st)

        # すぐ反映（ポーリングを待たない）
        self._applied_mtime = None
        self._load_applied_state_from_disk()
        try:
            self._refresh_previews()
        except Exception:
            pass



    def _init_mode_styles(self):
        # Keep OS-native look; just add subtle weight + (best-effort) text color
        st = ttk.Style()
        try:
            st.configure("RF.Active.TButton", font=("Segoe UI", 11, "bold"))
            st.configure("RF.Inactive.TButton", font=("Segoe UI", 11))
            # Best-effort: some themes ignore foreground, but it helps on others
            st.map("RF.Active.TButton",
                   foreground=[("!disabled", "SystemHighlight")])
            st.map("RF.Inactive.TButton",
                   foreground=[("!disabled", "#666")])
        except Exception:
            pass
        try:
            st.configure("RF.ModeBadge.TLabel", font=("Segoe UI", 11, "bold"))
        except Exception:
            pass

    def _apply_mode_visuals(self, mode: str):
        mode = "WORKSHOP" if str(mode).upper().startswith("WORK") else "SEARCH"
        try:
            if mode == "SEARCH":
                self.btn_search.configure(style="RF.Active.TButton")
                self.btn_work.configure(style="RF.Inactive.TButton")
                if hasattr(self, "lbl_mode_badge"):
                                        self.lbl_mode_badge.configure(text="検索モードの使い方", style="RF.ModeBadge.TLabel", foreground="SystemHighlight")
                                        if hasattr(self, "_tt_mode_badge"):
                                            try:
                                                self._tt_mode_badge.set_text("検索モードの使い方\n\n1) フォルダを選ぶ\n2) 必要なら探索深さを変更\n3) 素材（samples）を作る\n\n次に：作業モードで式を整理")
                                            except Exception:
                                                pass
            else:
                self.btn_search.configure(style="RF.Inactive.TButton")
                self.btn_work.configure(style="RF.Active.TButton")
                if hasattr(self, "lbl_mode_badge"):
                    # slightly calmer but still clear
                                        self.lbl_mode_badge.configure(text="作業モードの使い方", style="RF.ModeBadge.TLabel", foreground="SystemHighlight")
                                        if hasattr(self, "_tt_mode_badge"):
                                            try:
                                                self._tt_mode_badge.set_text("作業モードの使い方\n\n- 検索モードで作った素材（samples）を使う\n- ここではフォルダは変更しない\n- AI回答を貼り付け→適用で式リストへ")
                                            except Exception:
                                                pass
        except Exception:
            pass

    def set_mode(self, mode: str):
        mode = "WORKSHOP" if str(mode).upper().startswith("WORK") else "SEARCH"
        self.mode.set(mode)

        if mode == "SEARCH":
            self.frame_search.lift()
            self.lbl_status.config(text="検索：ダブルクリック＝デフォルト検索／右クリック＝検索エンジン選択")
            # show search controls
            self.frm_search_controls.pack(side="right")
            self._refresh_previews()
        else:
            self.frame_work.lift()
            self.lbl_purpose.config(text="作業工房：AIの回答を貼り付けて式を適用し、プレビューで確認します")
            self.lbl_status.config(text="工房：貼り付け→適用→ON/OFF→プレビュー")
            # hide search controls
            self.frm_search_controls.pack_forget()

        self._apply_mode_visuals(mode)
        self._save_settings()

    # ---------- folder data ----------
    def pick_folder(self):
        d = filedialog.askdirectory()
        if not d:
            return
        self.folder = d
        self.lbl_folder.config(text=d)
        self._load_folder(d)
        self._save_settings()

    def _load_folder(self, folder: str):
        self.rows.clear()
        try:
            for root, _dirs, files in os.walk(folder):
                for name in files:
                    p = os.path.join(root, name)
                    if not os.path.isfile(p):
                        continue
                    raw = os.path.splitext(name)[0]
                    raw2 = apply_patterns_for_genre(raw, self.applied_current, self.genre.get())
                    key = minimal_clean_for_search(raw2)
                    parent_path = os.path.dirname(p)
                    parent_name = os.path.basename(parent_path) or ""
                    self.rows.append(
                        {"raw": raw, "key": key, "parent_name": parent_name, "parent_path": parent_path}
                    )
        except Exception as e:
            messagebox.showerror("読み込み失敗", f"フォルダ読み込みに失敗しました: {e}", parent=self)
            return

        self._refresh_previews()
        self._refresh_ws_tree()
        # --- Stage1: always refresh samples file for workshop (no UI change) ---
        try:
            self._write_samples_json(max_items=5000)
        except Exception:
            pass

        # --- Sync: update embedded workshop panel immediately (integrated single-window) ---
        try:
            wp = getattr(self, "workshop_panel", None)
            if wp is not None and hasattr(wp, "_maybe_reload_samples"):
                wp._maybe_reload_samples(force=True)
        except Exception:
            pass

    def _write_samples_json(self, max_items: int = 5000):
        """Write current folder-derived samples for workshop.
        No UI change. Used only as material; filenames are not sent to AI.
        """
        ad = app_dir()
        samples = []
        for r in getattr(self, "rows", []):
            s = str(r.get("raw", "") or "").strip()
            if s:
                samples.append(s)
            if len(samples) >= int(max_items):
                break
        try:
            with open(os.path.join(ad, SAMPLES_JSON), "w", encoding="utf-8") as f:
                json.dump({"samples": samples}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ---------- filter ----------
    def _on_filter_slider(self):
        try:
            self.var_filter_strength.set(int(float(self._slider_filter.get())))
        except Exception:
            pass
        self._refresh_previews()
        self._save_settings()

    def _filter_params(self):
        try:
            s = int(self.var_filter_strength.get())
        except Exception:
            s = 60
        strength = max(0.0, min(1.0, s / 100.0))
        ratio_threshold = 0.95 - 0.35 * strength  # 0.95 .. 0.60
        min_chars = int(1 + 7 * strength)  # 1 .. 8
        return ratio_threshold, min_chars

    def _is_numeric_dominant_key(self, s: str) -> bool:
        t = (s or "").strip()
        if not t:
            return True
        t2 = re.sub(r"[\s\-_.()\[\]{}<>【】『』「」]+", "", t)
        if not t2:
            return True

        ratio_th, min_chars = self._filter_params()
        if len(t2) < min_chars:
            return True

        digits = sum(ch.isdigit() for ch in t2)
        letters = sum(ch.isalpha() for ch in t2)

        if digits == 0:
            return False
        if letters == 0 and digits >= 3:
            return True
        if digits / max(1, digits + letters) >= ratio_th:
            return True
        return False

    def _short_parent_sub(self, folder_path: str) -> str:
        try:
            parts = re.split(r"[\\/]+", (folder_path or "").strip())
            parts = [p for p in parts if p]
            if len(parts) >= 2:
                return "..." + os.sep + os.sep.join(parts[-2:])
            if len(parts) == 1:
                return "..." + os.sep + parts[-1]
        except Exception:
            pass
        return ""

    # ---------- refresh ----------
    def _refresh_previews(self):
        if not hasattr(self, "tree_key"):
            return

        # rebuild mapping key -> parents
        self._key_to_parents = {}
        for r in self.rows:
            k = str(r.get("key", "")).strip()
            pn = str(r.get("parent_name", "")).strip()
            pp = str(r.get("parent_path", "")).strip()
            if not k or not pn:
                continue
            self._key_to_parents.setdefault(k, [])
            # unique by (pn, pp)
            if not any(x["name"] == pn and x["path"] == pp for x in self._key_to_parents[k]):
                self._key_to_parents[k].append({"name": pn, "path": pp})

        # left keys
        self.tree_key.delete(*self.tree_key.get_children())
        seen = set()
        i = 0
        for r in self.rows:
            k = str(r.get("key", "")).strip()
            if not k:
                continue
            if self._is_numeric_dominant_key(k):
                continue
            if k in seen:
                continue
            seen.add(k)
            self.tree_key.insert("", "end", iid=f"k{i}", values=(k,))
            i += 1

        # clear parent display
        self.var_parent_disp.set("（左でタイトルを選ぶと、ここに親フォルダが出ます）")
        self.var_parent_sub.set("")
        self._current_parent_name_for_search = ""

        # rebuild right click menu to reflect current engines
        self._rebuild_search_menu()

    def _rebuild_search_menu(self):
        try:
            self.menu_search.delete(0, "end")
        except Exception:
            self.menu_search = tk.Menu(self, tearoff=0)

        for name in engine_names(self.engine_defs):
            self.menu_search.add_command(label=f"{name}で検索", command=lambda n=name: self._search_selected(n))

    def _on_key_select(self):
        sel = self.tree_key.selection()
        # selected query display (search-mode hero)
        try:
            if hasattr(self, "var_selected_query"):
                if sel:
                    v = self.tree_key.item(sel[0], "values")
                    q = (v[0] if v else "")
                    self.var_selected_query.set(q if q else "（候補を選択）")
                else:
                    self.var_selected_query.set("（候補を選択）")
        except Exception:
            pass
        if not sel:
            self.var_parent_disp.set("（左でタイトルを選ぶと、ここに親フォルダが出ます）")
            self.var_parent_sub.set("")
            self._current_parent_name_for_search = ""
            return

        iid = sel[0]
        v = self.tree_key.item(iid, "values")
        key = str(v[0]).strip() if v else ""
        items = self._key_to_parents.get(key, [])
        if not items:
            self.var_parent_disp.set("（親フォルダなし）")
            self.var_parent_sub.set("")
            self._current_parent_name_for_search = ""
            return

        items = sorted(items, key=lambda x: (x["name"], x["path"]))
        first = items[0]
        name = first["name"]
        path = first["path"]

        if len(items) >= 2:
            self.var_parent_disp.set(f"{name}（他{len(items)-1}）")
        else:
            self.var_parent_disp.set(name)

        self.var_parent_sub.set(self._short_parent_sub(path))
        self._current_parent_name_for_search = name

    # ---------- selection ----------
    def clear_selection(self):
        for tname in ("tree_key", "tree_ws"):
            t = getattr(self, tname, None)
            if t:
                try:
                    t.selection_remove(t.selection())
                except Exception:
                    pass
        self.var_parent_disp.set("（左でタイトルを選ぶと、ここに親フォルダが出ます）")
        self.var_parent_sub.set("")
        self._current_parent_name_for_search = ""
        self.lbl_status.config(text="選択解除しました")

    # ---------- search actions ----------
    def _tree_selected_texts(self, tree):
        out = []
        for iid in tree.selection():
            vals = tree.item(iid, "values")
            if vals:
                out.append(str(vals[0]))
        return out

    def _search_from_tree(self, tree):
        if self.mode.get() != "SEARCH":
            return
        texts = self._tree_selected_texts(tree)
        if not texts:
            return
        open_url(self.default_engine.get(), texts[0], self.engine_defs)

    def _popup_search_menu(self, event, tree):
        if self.mode.get() != "SEARCH":
            return
        iid = tree.identify_row(event.y)
        if iid and iid not in tree.selection():
            tree.selection_set(iid)
        self._last_popup_tree = tree
        self._last_popup_texts = None
        try:
            self.menu_search.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu_search.grab_release()

    def _popup_search_menu_for_parent(self, event):
        q = (self._current_parent_name_for_search or "").strip()
        if not q:
            return
        self._last_popup_tree = None
        self._last_popup_texts = [q]
        try:
            self.menu_search.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu_search.grab_release()

    def _search_selected(self, engine):
        if self.mode.get() != "SEARCH":
            return
        tree = getattr(self, "_last_popup_tree", None)
        if tree is None:
            texts = getattr(self, "_last_popup_texts", []) or []
        else:
            texts = self._tree_selected_texts(tree)
        if not texts:
            messagebox.showinfo("検索", "行を選択してください。", parent=self)
            return
        open_url(engine, texts[0], self.engine_defs)

    def _search_from_parent_label(self, default: bool = True):
        if self.mode.get() != "SEARCH":
            return
        q = (self._current_parent_name_for_search or "").strip()
        if not q:
            return
        if default:
            open_url(self.default_engine.get(), q, self.engine_defs)

    # ---------- workshop ----------
    def ensure_workshop(self, background=True, show=False, extra_args=None):
        lock_ok = lock_running(self._ws_lock_path)
        if not lock_ok:
            if not os.path.exists(self._ws_py_path):
                messagebox.showerror("工房起動", f"{WORKSHOP_PY} が見つかりません。", parent=self)
                return False
            try:
                args = [sys.executable, self._ws_py_path, "--single"]
                if extra_args:
                    args += list(extra_args)
                if background:
                    args += ["--hidden"]

                log_path = os.path.join(app_dir(), "_ai_title_workshop_stdout_stderr.log")
                log_f = open(log_path, "a", encoding="utf-8")

                creationflags = 0
                if os.name == "nt":
                    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

                subprocess.Popen(
                    args,
                    cwd=app_dir(),
                    stdout=log_f,
                    stderr=log_f,
                    stdin=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
            except Exception as e:
                messagebox.showerror("工房起動", f"起動に失敗しました。\n{e}", parent=self)
                return False

        if show:
            append_inbox({"cmd": "SHOW"})
        return True

    def open_workshop_front(self):
        if self.ensure_workshop(background=False, show=True):
            self.lbl_status.config(text="工房（作業）を表示しました")
    def open_strong_save_front(self):
        # 保存工房（強）
        # この viewer は「作業工房」を同一プロセスに埋め込んでいる前提なので、
        # 可能なら外部プロセスを起動せず、ローカルに StrongSaveWindow を開く。
        try:
            wp = getattr(self, "workshop_panel", None)
            if StrongSaveWindow is not None and wp is not None:
                win = getattr(self, "_strong_save_win", None)
                if win is not None and win.winfo_exists():
                    try:
                        win.deiconify()
                    except Exception:
                        pass
                    try:
                        win.lift()
                        win.focus_force()
                    except Exception:
                        pass
                else:
                    win = StrongSaveWindow(self, workshop_panel=wp)
                    self._strong_save_win = win
                    try:
                        win.lift()
                        win.focus_force()
                    except Exception:
                        pass
                try:
                    self.lbl_status.config(text="保存工房（強）を表示しました")
                except Exception:
                    pass
                return
        except Exception:
            pass

        # 互換フォールバック（旧: 外部 workshop プロセス + IPC）
        try:
            append_inbox({"cmd": "OPEN_STRONG_SAVE"})
        except Exception:
            pass

        try:
            self.ensure_workshop(background=True, show=False, extra_args=["--open-strong-save"])
            try:
                self.lbl_status.config(text="保存工房（強）を表示しました")
            except Exception:
                pass
        except Exception:
            pass

    def open_strong_repo_settings(self):
        StrongRepoSettings(self)

    def _refresh_ws_tree(self):
        if not hasattr(self, "tree_ws"):
            return
        self.tree_ws.delete(*self.tree_ws.get_children())
        for i, r in enumerate(self.rows):
            self.tree_ws.insert("", "end", iid=f"ws{i}", values=(str(r.get("key", "")),))

    def start_tutorial(self):
        if self.mode.get() != "WORKSHOP":
            return
            return
        self.queue.clear()
        self._render_queue()
        self.tutorial_phase = 1
        messagebox.showinfo("チュートリアル", "ノイズを含むタイトルを10件くらい選んで下さい（色々なパターン）", parent=self)
        self.lbl_status.config(text="フェーズ1：左のKEYを選んでキューへ追加 → 送信")

    def ws_add_clicked(self, event=None):
        if self.mode.get() != "WORKSHOP":
            return
        iid = None
        if event is not None:
            try:
                iid = self.tree_ws.identify_row(event.y)
            except Exception:
                iid = None
        if iid and iid not in self.tree_ws.selection():
            self.tree_ws.selection_set(iid)

        sels = self._tree_selected_texts(self.tree_ws)
        if not sels:
            return
        for t in sels:
            t = str(t).strip()
            if t:
                self.queue.append(t)

        self._render_queue()

        if self.tutorial_phase == 1 and len(self.queue) >= N_TARGET:
            self.lbl_status.config(text="フェーズ1：必要数に達しました。送信してください。")
        elif self.tutorial_phase == 2 and len(self.queue) >= K_TARGET:
            self.lbl_status.config(text="フェーズ2：必要数に達しました。送信してください。")

    def _popup_queue_menu(self, event):
        if self.mode.get() != "WORKSHOP":
            return
        try:
            self.menu_queue.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu_queue.grab_release()

    def ws_queue_clear(self):
        self.queue.clear()
        self._render_queue()
        self.lbl_status.config(text="キューをクリアしました")

    def _render_queue(self):
        self.txt_queue.delete("1.0", "end")
        self.txt_queue.insert("1.0", "\n".join(self.queue))

    def _showinfo_then_open_workshop(self, title: str, message: str):
        messagebox.showinfo(title, message, parent=self)
        self.open_workshop_after_ok()

    def open_workshop_after_ok(self):
        try:
            append_inbox({"cmd": "SHOW"})
        except Exception:
            pass
        try:
            if not os.path.exists(self._ws_py_path):
                return
            args = [sys.executable, self._ws_py_path, "--single", "--show"]
            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
            subprocess.Popen(args, cwd=app_dir(), creationflags=creationflags)
        except Exception:
            pass

    def send_queue(self):
        if self.mode.get() != "WORKSHOP":
            return
        if self.tutorial_phase not in (1, 2):
            messagebox.showinfo("送信", "開始（チュートリアル）を押してください。", parent=self)
            return
        if not self.queue:
            messagebox.showinfo("送信", "キューが空です。左から追加してください。", parent=self)
            return

        if self.tutorial_phase == 1:
            reset_workshop_inbox()
            append_inbox({"dest": "NOISE", "titles": self.queue[:]})
            self.queue.clear()
            self._render_queue()
            messagebox.showinfo("送信", "第1フェーズを送信しました。次に、残したいタイトルを10件くらい選んで下さい。", parent=self)
            self.tutorial_phase = 2
            self.lbl_status.config(text="フェーズ2：残したいタイトルを集めて送信してください。")
        else:
            append_inbox({"dest": "KEEP", "titles": self.queue[:]})
            self.queue.clear()
            self._render_queue()
            self._showinfo_then_open_workshop("完了", "AI検索工房にタイトルを送りました。")
            self.tutorial_phase = 3
            self.lbl_status.config(text="チュートリアル完了")

    # ---------- settings ----------
    def _save_settings(self):
        st = {
            "default_engine": self.default_engine.get(),
            "genre": self.genre.get(),
            "mode": self.mode.get(),
            "last_folder": self.folder,
            "engine_defs": getattr(self, "engine_defs", None),
            "filter_strength": int(self.var_filter_strength.get()),
        }
        save_json(self._settings_path, st)

    def _on_close(self):
        try:
            self._save_settings()
        except Exception:
            pass
        self.destroy()

    # ---------- Engine Editor ----------
    def _open_engine_editor(self):
        win = tk.Toplevel(self)
        win.title("検索エンジン編集")
        win.geometry("720x420")
        win.transient(self)
        win.grab_set()

        defs = [dict(d) for d in normalize_engine_defs(getattr(self, "engine_defs", None))]

        frm = ttk.Frame(win, padding=10)
        frm.pack(fill="both", expand=True)

        left = ttk.Frame(frm)
        left.pack(side="left", fill="y")

        ttk.Label(left, text="エンジン一覧").pack(anchor="w")
        lb = tk.Listbox(left, height=14, exportselection=False)
        lb.pack(fill="y", expand=True, pady=(4, 8))

        right = ttk.Frame(frm)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))

        ttk.Label(right, text="名前").pack(anchor="w")
        name_var = tk.StringVar()
        ent_name = ttk.Entry(right, textvariable=name_var)
        ent_name.pack(fill="x", pady=(4, 10))

        ttk.Label(right, text="URL（{q} が検索語になります）").pack(anchor="w")
        url_var = tk.StringVar()
        ent_url = ttk.Entry(right, textvariable=url_var)
        ent_url.pack(fill="x", pady=(4, 10))

        help_txt = (
            "例）Google: https://www.google.com/search?q={q}\n"
            "例）AI: https://www.perplexity.ai/search?q={q}\n"
            "※ {q} がない場合、最後に ?q=... を付けて開きます。"
        )
        ttk.Label(right, text=help_txt, justify="left").pack(anchor="w", pady=(0, 10))

        btns = ttk.Frame(left)
        btns.pack(fill="x")
        ttk.Button(btns, text="追加", command=lambda: add_engine()).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(btns, text="削除", command=lambda: del_engine()).pack(side="left", fill="x", expand=True)

        bottom = ttk.Frame(win, padding=(10, 0, 10, 10))
        bottom.pack(fill="x")

        def refresh_list(select_idx=0):
            lb.delete(0, "end")
            for d in defs:
                lb.insert("end", d.get("name", ""))
            if defs:
                select_idx = max(0, min(select_idx, len(defs) - 1))
                lb.selection_clear(0, "end")
                lb.selection_set(select_idx)
                lb.activate(select_idx)
                lb.see(select_idx)
                name_var.set(defs[select_idx].get("name", ""))
                url_var.set(defs[select_idx].get("url", ""))
            else:
                name_var.set("")
                url_var.set("")

        def on_select(_evt=None):
            sel = lb.curselection()
            if not sel:
                return
            i = int(sel[0])
            if 0 <= i < len(defs):
                name_var.set(defs[i].get("name", ""))
                url_var.set(defs[i].get("url", ""))

        def apply_to_current():
            sel = lb.curselection()
            if not sel:
                return
            i = int(sel[0])
            if not (0 <= i < len(defs)):
                return
            defs[i]["name"] = (name_var.get() or "").strip()
            defs[i]["url"] = (url_var.get() or "").strip()
            refresh_list(i)

        def add_engine():
            defs.append({"name": "New", "url": "https://www.google.com/search?q={q}"})
            refresh_list(len(defs) - 1)

        def del_engine():
            sel = lb.curselection()
            if not sel:
                return
            i = int(sel[0])
            if 0 <= i < len(defs):
                defs.pop(i)
                refresh_list(max(0, i - 1))

        def save_and_close():
            apply_to_current()
            self.engine_defs = normalize_engine_defs(defs)

            # update combobox values
            try:
                self.cb_engine.config(values=engine_names(self.engine_defs))
            except Exception:
                pass

            cur = self.default_engine.get()
            names = engine_names(self.engine_defs)
            if cur not in names:
                self.default_engine.set(names[0] if names else "Google")

            self._rebuild_search_menu()
            self._save_settings()
            win.destroy()

        ttk.Button(bottom, text="保存", command=save_and_close).pack(side="right")
        ttk.Button(bottom, text="キャンセル", command=win.destroy).pack(side="right", padx=(0, 8))

        lb.bind("<<ListboxSelect>>", on_select)
        ent_name.bind("<FocusOut>", lambda e: apply_to_current())
        ent_url.bind("<FocusOut>", lambda e: apply_to_current())

        if not defs:
            defs = [dict(x) for x in DEFAULT_ENGINE_DEFS]
        refresh_list(0)

    # ---------- workshop(prep) helpers ----------
    def _ws_on_changed(self):
        """事故防止: 目的は必須。目的が未選択なら「進む」を無効。"""
        ok_purpose = bool(getattr(self, "var_ws_purpose", tk.StringVar()).get())
        ok_strength = bool(getattr(self, "var_ws_strength", tk.StringVar()).get())

        if hasattr(self, "btn_ws_send"):
            # purpose + strength must be chosen (safety). Checked words are optional (ユーザーがゼロでも良い場面がある)
            self.btn_ws_send.configure(state=("normal" if (ok_purpose and ok_strength) else "disabled"))

        if hasattr(self, "btn_ws_restore"):
            self.btn_ws_restore.configure(state=("normal" if getattr(self, "_ws_snapshot", None) else "disabled"))

        if hasattr(self, "var_ws_hint"):
            if not ok_purpose:
                self.var_ws_hint.set("1. 作成の目的を選択してください。")
            elif not ok_strength:
                self.var_ws_hint.set("3. 強度を選択してください（間違ってもOK）。")
            else:
                self.var_ws_hint.set("準備OK：必要なら語句を移動し、工房（作業）へ進めます。")
    def _ws_has_any_checked(self) -> bool:
        # at least one checked in either list; usually keep list has many checked by default
        for tree in (getattr(self, "tree_keep", None), getattr(self, "tree_ignore", None)):
            if tree is None:
                continue
            for iid in tree.get_children():
                try:
                    if tree.set(iid, "sel") == "☑":
                        return True
                except Exception:
                    continue
        return False

    def _ws_toggle_on_click(self, event, tree):
        """Toggle check mark when clicking on first column. Gate by purpose selection."""
        # do nothing if purpose not selected (avoid foreseeing)
        if not getattr(self, "var_ws_purpose", tk.StringVar()).get():
            return "break"
        region = tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        col = tree.identify_column(event.x)
        if col != "#1":  # sel column
            return
        iid = tree.identify_row(event.y)
        if not iid:
            return "break"
        cur = tree.set(iid, "sel")
        tree.set(iid, "sel", "☐" if cur == "☑" else "☑")
        self._ws_on_changed()
        return "break"

    def _ws_set_all_checks(self, tree, checked: bool):
        """工房へ: 指定リストのチェックを一括でON/OFFする（左右別々）"""
        try:
            mark = "☑" if checked else "☐"
            for iid in tree.get_children(""):
                vals = list(tree.item(iid, "values"))
                if not vals:
                    continue
                vals[0] = mark
                tree.item(iid, values=tuple(vals))
            self._ws_on_changed()
        except Exception:
            pass

    def _ws_toggle_tree_cell(self, event, tree):
        # Toggle checkbox when clicking on a row (sel column)
        try:
            region = tree.identify("region", event.x, event.y)
            if region not in ("cell", "tree"):
                return
            row = tree.identify_row(event.y)
            col = tree.identify_column(event.x)
            if not row:
                return
            # Only toggle when clicking sel/word columns (both are fine)
            cur = tree.set(row, "sel")
            tree.set(row, "sel", "☐" if cur == "☑" else "☑")
            self._ws_on_changed()
        except Exception:
            return

    def _ws_move_keep_to_ignore(self):
        # Move selected item from KEEP to IGNORE (no auto judgement)
        if not hasattr(self, "tree_keep") or not hasattr(self, "tree_ignore"):
            return
        sel = self.tree_keep.selection()
        if not sel:
            return
        iid = sel[0]
        word = self.tree_keep.set(iid, "word")
        selmark = self.tree_keep.set(iid, "sel")
        self.tree_keep.delete(iid)
        self.tree_ignore.insert("", "end", values=(selmark, word))
        self._ws_on_changed()

    def _ws_move_ignore_to_keep(self):
        # Move selected item from IGNORE to KEEP (bracketed allowed)
        if not hasattr(self, "tree_keep") or not hasattr(self, "tree_ignore"):
            return
        sel = self.tree_ignore.selection()
        if not sel:
            return
        iid = sel[0]
        word = self.tree_ignore.set(iid, "word")
        selmark = self.tree_ignore.set(iid, "sel")
        self.tree_ignore.delete(iid)
        self.tree_keep.insert("", "end", values=(selmark, word))
        self._ws_on_changed()

    def _ws_restore_snapshot(self):
        # Restore last snapshot captured when opening workshop (safety redo)
        snap = getattr(self, "_ws_snapshot", None)
        if not snap:
            messagebox.showinfo("復元", "復元できる準備データがありません。", parent=self)
            return
        # restore radios
        self.var_ws_purpose.set(snap.get("purpose", ""))
        self.var_ws_strength.set(snap.get("strength", ""))
        # restore trees
        try:
            for t in (self.tree_keep, self.tree_ignore):
                for iid in t.get_children():
                    t.delete(iid)
            for item in snap.get("keep_items", []):
                self.tree_keep.insert("", "end", values=("☑" if item.get("use") else "☐", item.get("word","")))
            for item in snap.get("ignore_items", []):
                self.tree_ignore.insert("", "end", values=("☑" if item.get("use") else "☐", item.get("word","")))
        except Exception:
            pass
        self._ws_on_changed()

    def _ws_populate_words(self):
        # NOTE: 起動高速化のため、自動実行はしません（必要なら将来手動実行）
        """Extract words from current rows and fill KEEP/IGNORE lists.

        初期配置:
        - 括弧なし → KEEP（☑）
        - 括弧付き → IGNORE（☐）
        """
        if not hasattr(self, "tree_keep") or not hasattr(self, "tree_ignore"):
            return
        self.tree_keep.delete(*self.tree_keep.get_children())
        self.tree_ignore.delete(*self.tree_ignore.get_children())

        keep_words, ignore_words = self._ws_extract_words()
        for w in keep_words:
            self.tree_keep.insert("", "end", values=("☑", w))
        for w in ignore_words:
            self.tree_ignore.insert("", "end", values=("☐", w))

        # hint
        if hasattr(self, "var_ws_hint"):
            if not getattr(self, "var_ws_purpose", tk.StringVar()).get():
                self.var_ws_hint.set("1. 作成の目的を選択してください。")
            else:
                self.var_ws_hint.set("単語はクリックで☑/☐を切替。必要なら移動ボタンで往復できます。")
    def _ws_extract_words(self):
        """Return (non_bracket_words, bracket_words) as sorted unique lists."""
        texts = []
        for r in getattr(self, "rows", []):
            k = str(r.get("key", "")).strip()
            if k:
                texts.append(k)

        bracketed = set()
        plain = set()

        # bracket pairs (same-type only)
        pairs = [
            ("(", ")"),
            ("（", "）"),
            ("[", "]"),
            ("［", "］"),
            ("【", "】"),
            ("〔", "〕"),
            ("〖", "〗"),
            ("〘", "〙"),
            ("〚", "〛"),
            ("『", "』"),
            ("「", "」"),
            ("｢", "｣"),
            ("{", "}"),
            ("｛", "｝"),
            ("<", ">"),
            ("＜", "＞"),
            ("〈", "〉"),
            ("《", "》"),
            ("«", "»"),
        ]

        # Extract bracketed segments
        for t in texts:
            for a, b in pairs:
                for m in re.finditer(re.escape(a) + r"[^\n\r]{1,80}?" + re.escape(b), t):
                    seg = m.group(0).strip()
                    if seg:
                        bracketed.add(seg)

        # Remove bracketed segments then split remaining into tokens
        for t in texts:
            t2 = t
            for seg in bracketed:
                t2 = t2.replace(seg, " ")
            # basic token split
            for tok in re.split(r"[\s\-_.:;,/\\]+", t2):
                tok = tok.strip()
                if not tok:
                    continue
                # ignore pure numbers / short junk
                if tok.isdigit():
                    continue
                if len(tok) <= 1:
                    continue
                plain.add(tok)

        # keep lists stable
        keep_words = sorted(plain, key=lambda s: (s.lower(), s))
        drop_words = sorted(bracketed, key=lambda s: (s.lower(), s))
        return keep_words, drop_words

    def _ws_send_to_workshop(self):
        """Open 工房（作業）. Preparation state is snapshotted so user can redo."""
        if not getattr(self, "var_ws_purpose", tk.StringVar()).get():
            messagebox.showinfo("確認", "「何を作りますか（必須）」を選んでください。", parent=self)
            return
        if not getattr(self, "var_ws_strength", tk.StringVar()).get():
            messagebox.showinfo("確認", "「強度」を選んでください（間違ってもOK）。", parent=self)
            return

        keep_items = []
        ignore_items = []
        if hasattr(self, "tree_keep"):
            for iid in self.tree_keep.get_children():
                keep_items.append({"word": self.tree_keep.set(iid, "word"),
                                   "use": (self.tree_keep.set(iid, "sel") == "☑")})
        if hasattr(self, "tree_ignore"):
            for iid in self.tree_ignore.get_children():
                ignore_items.append({"word": self.tree_ignore.set(iid, "word"),
                                     "use": (self.tree_ignore.set(iid, "sel") == "☑")})

        payload = {
            "app": "Readable Filenames",
            "stage": "PREP",
            "purpose": self.var_ws_purpose.get(),
            "strength": self.var_ws_strength.get(),
            "keep_items": keep_items,
            "ignore_items": ignore_items,
            "timestamp": time.time(),
        }

        # snapshot for redo
        self._ws_snapshot = json.loads(json.dumps(payload, ensure_ascii=False))
        self._ws_on_changed()
        # write samples/state for workshop (作業) to read
        try:
            ad = get_app_dir()
            # samples: use current folder rows (raw titles). keep it lightweight.
            samples = []
            for r in getattr(self, "rows", []):
                s = str(r.get("raw", "") or "").strip()
                if s:
                    samples.append(s)
                if len(samples) >= 5000:
                    break
            with open(os.path.join(ad, SAMPLES_JSON), "w", encoding="utf-8") as f:
                json.dump({"samples": samples}, f, ensure_ascii=False, indent=2)

            # last_send: workshop reads this first (more reliable than jsonl timing)
            with open(os.path.join(ad, STATE_JSON), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            messagebox.showerror("送信失敗", f"サンプル/状態の保存に失敗しました: {e}", parent=self)
            return



        # write IPC jsonl (append)
        try:
            app_dir = get_app_dir()
            inbox = os.path.join(app_dir, IPC_INBOX)
            os.makedirs(app_dir, exist_ok=True)
            with open(inbox, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as e:
            messagebox.showerror("送信失敗", f"準備データの保存に失敗しました: {e}", parent=self)
            return

        # launch workshop script (作業). Keep this window open so user can戻ってやり直せる.
        try:
            ws_path = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "ReadableFilenames_workshop.py")
            if not os.path.exists(ws_path):
                ws_path = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "ai_title_workshop.py")
            subprocess.Popen([sys.executable, ws_path, "--show"])
        except Exception as e:
            messagebox.showerror("起動失敗", f"工房（作業）の起動に失敗しました: {e}", parent=self)
            return



def main():
    App().mainloop()



# ------------------------------
# 強リポ（デフォルト）管理
# - 保存工房（強）が読む strong_repo_default.txt を安全に差し替える
# ------------------------------
DEFAULT_STRONG_REPO_FILE = "strong_repo_default.txt"

INITIAL_DEFAULT_STRONG_REPO = """ReadableFilenames
AI_REPOSITORY
stage: STRONG_FINAL_FOR_RENAMER

目的
materials（pattern 行）を材料として、リネーマでも使える「1行正規表現」を作る。

入力（最重要）
- 下に並ぶ materials 行のみを材料として扱うこと。
- materials の各行は改変しない（削除・省略・言い換え禁止）。
- 並び順は維持すること。

許可
- OR(|)、量指定子（+,*,?,{m,n}）、文字クラス、グルーピング。

禁止
- 判断・説明・要約・提案。
- 前置き、番号、空行、装飾、コードブロック、引用。
- 余計な文字の混入（コピーが壊れる出力）。
- 改行を含む pattern（pattern: 行は必ず1行で完結）。

出力（最重要・厳守）
答えはワンクリックでコピーできる形で提供してください。
出力してよいのは次の形の行だけです。

pattern: 正規表現

この形以外の文字は一切書かないでください。

守れない場合は、次の1行だけを書いて終了してください。
pattern: a^

materials
"""

class StrongRepoSettings(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("強リポの管理（設定）")
        self.geometry("820x620")
        self.transient(master)
        self.grab_set()
        self._repo_path = os.path.join(app_dir(), DEFAULT_STRONG_REPO_FILE)
        self._build()

    def _ensure_file(self):
        if not os.path.exists(self._repo_path):
            try:
                with open(self._repo_path, "w", encoding="utf-8") as f:
                    f.write(INITIAL_DEFAULT_STRONG_REPO)
            except Exception:
                pass

    def _build(self):
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        ttk.Label(
            frm,
            text=(
                "ここで編集する内容は、保存工房（強）のデフォルト強リポになります。\n"
                "・通常の運用では触る必要はありません\n"
                "・正しさはアプリは判断しません\n"
                "・戻れるようにバックアップを作ってから上書きします"
            ),
            foreground="darkred",
            justify="left",
        ).pack(anchor="w", pady=(0, 8))

        self._ensure_file()

        self.txt = tk.Text(frm, wrap="none")
        y = ttk.Scrollbar(frm, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=y.set)
        self.txt.pack(side="left", fill="both", expand=True)
        y.pack(side="left", fill="y", padx=(6, 0))

        try:
            with open(self._repo_path, "r", encoding="utf-8") as f:
                self.txt.insert("1.0", f.read())
        except Exception:
            self.txt.insert("1.0", INITIAL_DEFAULT_STRONG_REPO)

        row = ttk.Frame(frm)
        row.pack(fill="x", pady=(10, 0))

        ttk.Button(row, text="当初デフォルトに戻す…", command=self._reset).pack(side="left")
        ttk.Button(row, text="キャンセル", command=self.destroy).pack(side="right")
        ttk.Button(row, text="適用…", command=self._apply).pack(side="right", padx=(0, 8))

    def _apply(self):
        if not messagebox.askyesno(APP_TITLE, "デフォルト強リポを上書きします。続行しますか？", parent=self):
            return
        code = simpledialog.askstring(APP_TITLE, "確認のため DEFAULT と入力してください", parent=self)
        if code != "DEFAULT":
            messagebox.showinfo(APP_TITLE, "中止しました。", parent=self)
            return

        # backup
        try:
            if os.path.exists(self._repo_path):
                ts = time.strftime("%Y%m%d_%H%M%S")
                bak = os.path.join(app_dir(), f"strong_repo_default_backup_{ts}.txt")
                try:
                    os.replace(self._repo_path, bak)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            with open(self._repo_path, "w", encoding="utf-8") as f:
                f.write(self.txt.get("1.0", "end").rstrip())
            messagebox.showinfo(APP_TITLE, "更新しました。", parent=self)
            self.destroy()
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"保存に失敗しました:\n{e}", parent=self)

    def _reset(self):
        if not messagebox.askyesno(APP_TITLE, "当初デフォルトに戻します。続行しますか？", parent=self):
            return
        code = simpledialog.askstring(APP_TITLE, "確認のため RESET と入力してください", parent=self)
        if code != "RESET":
            messagebox.showinfo(APP_TITLE, "中止しました。", parent=self)
            return
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", INITIAL_DEFAULT_STRONG_REPO)


if __name__ == "__main__":
    main()