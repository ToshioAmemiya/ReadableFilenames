# -*- coding: utf-8 -*-
"""
Readable Filenames - 工房（作業） v2

狙い（検索用をまず固める）:
- 1行＝1式（カード）として編集できる（A寄り）
- ブラウザのAI回答をペーストして取り込める
- ONの式を上から順に適用して、即プレビューで確認できる
- 成果物は「準備で選んだジャンル」のリポジトリに保存する（genre別1ファイル）
- 一発正解は求めない。準備<->作業を往復して寄せていく。

注意:
- この環境ではGUIの自動起動テストはできないため、こちらでは構文チェックを通した状態で配布します。
"""

import os
import json
import re
import sys
import datetime
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

# =====================
# UI helpers: Text with both scrollbars + right-click copy/paste
# =====================

def attach_context_menu(widget: tk.Widget) -> None:
    """Right-click context menu: Cut/Copy/Paste/SelectAll for Text/Entry."""
    menu = tk.Menu(widget, tearoff=0)
    menu.add_command(label="切り取り", command=lambda: widget.event_generate("<<Cut>>"))
    menu.add_command(label="コピー", command=lambda: widget.event_generate("<<Copy>>"))
    menu.add_command(label="貼り付け", command=lambda: widget.event_generate("<<Paste>>"))
    menu.add_separator()
    menu.add_command(label="全選択", command=lambda: widget.event_generate("<<SelectAll>>"))

    def popup(e):
        try:
            widget.focus_set()
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    # Windows: right click / macOS: Control+click
    widget.bind("<Button-3>", popup, add=True)
    widget.bind("<Control-Button-1>", popup, add=True)


def make_text_with_scrollbars(parent: tk.Widget, *, height=8, width=None, wrap="none"):
    """
    Create a tk.Text with BOTH vertical + horizontal scrollbars.
    Returns (container_frame, text_widget).
    """
    frame = ttk.Frame(parent)
    ybar = ttk.Scrollbar(frame, orient="vertical")
    xbar = ttk.Scrollbar(frame, orient="horizontal")

    txt = tk.Text(
        frame,
        height=height,
        wrap=wrap,           # use "none" to make horizontal scrolling meaningful
        undo=True,
        yscrollcommand=ybar.set,
        xscrollcommand=xbar.set,
    )
    if width is not None:
        txt.config(width=width)

    ybar.config(command=txt.yview)
    xbar.config(command=txt.xview)

    txt.grid(row=0, column=0, sticky="nsew")
    ybar.grid(row=0, column=1, sticky="ns")
    xbar.grid(row=1, column=0, sticky="ew")

    frame.grid_rowconfigure(0, weight=1)
    frame.grid_columnconfigure(0, weight=1)

    attach_context_menu(txt)
    return frame, txt


APP_TITLE = "Readable Filenames – 作業工房"

# 検索モードのデフォルトジャンル（viewer と統一）
DEFAULT_GENRES = ["アニメ", "音楽", "ドラマ", "映画", "その他"]


# --- 内部固定語句（不可視）: エクスポート時にユーザー語句へ自動合成 ---
INTERNAL_FIXED_TERMS = [
    "括弧＋中身は1トークン（分解禁止）",
    "括弧全消しは禁止（例: \[[^\]]*\]）",
    "OR(|)で意味をまとめるのは禁止（1行=1式）",
]


# =====================
# Bracket pairs (Stage1: bracket token observation)
# =====================
DEFAULT_BRACKET_PAIRS = [
    ("(", ")"),
    ("[", "]"),
    ("{", "}"),
    ("<", ">"),
    ("（", "）"),
    ("［", "］"),
    ("｛", "｝"),
    ("＜", "＞"),
    ("【", "】"),
    ("〔", "〕"),
    ("〈", "〉"),
    ("《", "》"),
    ("「", "」"),
    ("『", "』"),
    ("〝", "〟"),
    ("｢", "｣"),
    ("‹", "›"),
    ("«", "»"),
]

def extract_bracket_tokens(lines, bracket_pairs=None):
    """Mechanical observation only.

    - Extract 'open + inside + close' as ONE token (no splitting).
    - No interpretation. No generalization.
    - Deduplicate by exact string match; also count occurrences.

    Returns: list of dicts: {"token": str, "count": int}
    """
    if bracket_pairs is None:
        bracket_pairs = DEFAULT_BRACKET_PAIRS
    pairs = [(str(a), str(b)) for a, b in bracket_pairs if str(a) and str(b)]
    counts = {}
    for raw in (lines or []):
        s = str(raw or "")
        if not s:
            continue
        for op, cl in pairs:
            start = 0
            while True:
                i = s.find(op, start)
                if i < 0:
                    break
                j = s.find(cl, i + len(op))
                if j < 0:
                    break
                tok = s[i:j + len(cl)]
                if tok:
                    counts[tok] = counts.get(tok, 0) + 1
                start = j + len(cl)
    items = [{"token": k, "count": v} for k, v in counts.items()]
    items.sort(key=lambda d: (-d["count"], d["token"]))
    return items
STATE_JSON = "ReadableFilenames_last_send.json"
SAMPLES_JSON = "ReadableFilenames_samples.json"
IPC_INBOX = "_ai_title_workshop_inbox.jsonl"  # viewer既存の送信先（互換用）
LOCK_FILE = "_ai_title_workshop_lock.json"
REPO_DIR = "repositories"
AI_REPO_JSON = "ReadableFilenames_ai_repo_default.json"
STRONG_SETS_JSON = "ReadableFilenames_strong_sets.json"

# --- default rules (shown on first launch per genre) ---
DEFAULT_RULES_WEAK = [
    {"enabled": False, "tier": "WEAK", "name": "例：区切り記号を空白へ", "pattern": r"[._-]+", "note": "区切りを整える例（必要ならON）"},
    {"enabled": False, "tier": "WEAK", "name": "例：連番っぽい末尾を消す", "pattern": r"(?:\s*[\[\(（【]?#?\d{1,4}[\]\)）】]?\s*)$", "note": "末尾の番号を消す例（必要ならON）"},
]
# --- Defaults used when viewer doesn't provide examples/note ---
DEFAULT_KEEP_EXAMPLES = [
    "Jujutsu Kaisen - 09",
    "One Piece - 1050",
    "第09話",
]
DEFAULT_NOISE_EXAMPLES = [
    "[720p]",
    "RAW",
    "WEB-DL",
    "MP3",
]
DEFAULT_REPO_NOTE = (
    "あなたはファイル名整理の補助役です。\n"
    "keep_examples と noise_examples は『例』であり、正解や確定ルールではありません。\n"
    "samples を観察し、共通して現れるノイズの候補を考えてください。\n"
    "ただし、タイトルの核を破壊しないことを最優先にし、迷う場合は消さずに残してください。\n"
    "提案は『弱：単独式』から始め、一度に多くを消そうとしないでください。\n"
    "出力は指定されたブロック形式で、pattern は1行で提示してください。"
)




def app_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def safe_load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def safe_save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

# ---------------- single-instance / IPC ----------------
CURSOR_JSON = "_ai_title_workshop_cursor.json"
_LAST_CURSOR_POS = None  # write cursor file only when changed

def _process_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False

def _lock_running(lock_path: str, max_age_sec: float = 3600.0) -> bool:
    d = safe_load_json(lock_path, None)
    if not isinstance(d, dict):
        return False
    pid = int(d.get("pid", 0) or 0)
    ts = float(d.get("ts", 0.0) or 0.0)
    if not _process_alive(pid):
        return False
    # If the process is alive, treat lock as valid regardless of timestamp.
    return True

def _write_lock(lock_path: str):
    """Create/update the lock file only when needed.
    We avoid refreshing timestamps periodically to prevent constant file writes.
    The viewer checks process liveness via PID.
    """
    my_pid = os.getpid()
    # If the lock already points to this PID, do nothing (no constant writes)
    try:
        cur = safe_load_json(lock_path, {})
        cur_pid = int(cur.get("pid", 0) or 0) if isinstance(cur, dict) else 0
        if cur_pid == my_pid and os.path.exists(lock_path):
            return
    except Exception:
        pass
    try:
        safe_save_json(lock_path, {"pid": my_pid, "ts": time.time()})
    except Exception:
        pass

def _remove_lock(lock_path: str):
    try:
        if os.path.exists(lock_path):
            os.remove(lock_path)
    except Exception:
        pass

def _append_inbox(msg: dict):
    try:
        path = os.path.join(app_dir(), IPC_INBOX)
        m = dict(msg or {})
        m.setdefault("id", f"ws_{int(time.time()*1000)}_{os.getpid()}")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    except Exception:
        pass

def _load_cursor(path: str) -> int:
    d = safe_load_json(path, {})
    if isinstance(d, dict):
        try:
            return int(d.get("pos", 0) or 0)
        except Exception:
            return 0
    return 0

def _save_cursor(path: str, pos: int):
    """Write cursor only when changed (avoid constant file writes)."""
    global _LAST_CURSOR_POS
    try:
        p = int(pos)
    except Exception:
        p = 0
    if _LAST_CURSOR_POS is None:
        # initialize from existing file once
        try:
            _LAST_CURSOR_POS = _load_cursor(path)
        except Exception:
            _LAST_CURSOR_POS = 0
    if p == _LAST_CURSOR_POS:
        return
    _LAST_CURSOR_POS = p
    try:
        safe_save_json(path, {"pos": int(p)})
    except Exception:
        pass

def load_latest_prep_state() -> dict:
    """優先順位: last_send.json -> inbox(jsonl)最後の行 -> 空"""
    p_state = os.path.join(app_dir(), STATE_JSON)
    if os.path.exists(p_state):
        d = safe_load_json(p_state, {})
        if isinstance(d, dict) and d:
            return d

    p_inbox = os.path.join(app_dir(), IPC_INBOX)
    if os.path.exists(p_inbox):
        try:
            last = ""
            with open(p_inbox, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        last = line
            if last:
                d = json.loads(last)
                if isinstance(d, dict):
                    return d
        except Exception:
            pass

    return {
        "purpose": "SEARCH_DISPLAY",
        "strength": "WEAK",
        "genre": "その他",
        "keep_items": [],
        "ignore_items": [],
    }


def normalize_genre(genre: str) -> str:
    g = (genre or "").strip()
    return g if g else "その他"


def normalize_mode(purpose: str) -> str:
    p = (purpose or "").strip().upper()
    if p in ("SEARCH", "SEARCH_DISPLAY", "DISPLAY", "VIEW"):
        return "検索用"
    return "保存用"


def normalize_strength(strength: str) -> str:
    s = (strength or "").strip().upper()
    if s in ("WEAK", "弱"):
        return "弱"
    if s in ("MEDIUM", "MID", "中"):
        return "中"
    if s in ("STRONG", "強"):
        return "強"
    return strength or ""


def repo_path_for_genre(genre: str) -> str:
    g = normalize_genre(genre)
    fn = f"rules_{g}.json"
    return os.path.join(app_dir(), REPO_DIR, fn)


def parse_ai_blocks(text: str):
    """ブラウザのAI回答を取り込み。

    Stage1（括弧トークン）では、AIは **pattern: 行のみ** を返す運用がある。
    そのため、次の2系統を受け入れる。

    A) 従来ブロック:
       name: ...
       pattern: ...
       注意: ...

    B) 最小ブロック（推奨）:
       pattern: ...

    取り込み時のルール:
    - 1行=1式（pattern）
    - name が無い場合は空文字のまま保存する（命名しない方針）
    - why は無視（保存しない）
    """
    lines = text.splitlines()
    blocks = []
    cur = {"name": "", "pattern": "", "note": ""}
    state = None

    def flush():
        nonlocal cur
        pat = (cur.get("pattern") or "").strip()
        if pat:
            blocks.append({
                "enabled": True,
                "tier": "WEAK",
                "name": (cur.get("name") or "").strip(),
                "pattern": pat,
                "note": (cur.get("note") or "").strip(),
            })
        cur = {"name": "", "pattern": "", "note": ""}

    for raw in lines:
        line = raw.rstrip("\n")
        m = re.match(r"^\s*(name|pattern|why|注意)\s*:\s*(.*)$", line)
        if m:
            key = m.group(1)
            val = m.group(2)
            if key == "name":
                if (cur.get("pattern") or "").strip():
                    flush()
                state = "name"
                cur["name"] = val
            elif key == "pattern":
                if (cur.get("pattern") or "").strip():
                    flush()
                state = "pattern"
                cur["pattern"] = val
            elif key == "注意":
                state = "note"
                cur["note"] = val
            else:
                state = "why"
            continue

        if state == "pattern":
            if line.strip():
                cur["pattern"] = (cur["pattern"] + " " + line.strip()).strip()
        elif state == "note":
            if line.strip():
                cur["note"] = (cur["note"] + "\n" + line.strip()).strip()

    flush()
    return blocks

def compile_rule(pattern: str):
    p = (pattern or "").strip()
    # r"..." / r'...' 形式を剥ぐ
    if (p.startswith('r"') and p.endswith('"')) or (p.startswith("r'") and p.endswith("'")):
        p = p[2:-1]
    # "..." / '...' を剥ぐ
    if (p.startswith('"') and p.endswith('"')) or (p.startswith("'") and p.endswith("'")):
        p = p[1:-1]
    return re.compile(p)


def apply_rules_once(s: str, rules):
    out = s
    for r in rules:
        if not r.get("enabled", True):
            continue
        pat = (r.get("pattern") or "").strip()
        if not pat:
            continue
        try:
            rx = compile_rule(pat)
            out = rx.sub(" ", out)
        except Exception:
            continue
    out = re.sub(r"\s+", " ", out).strip()
    return out


def apply_rules_trace(s: str, rules):
    """Apply rules sequentially and return (result, hits).
    hits is a list of rule labels that actually changed the text.
    """
    out = s
    hits = []
    for i, r in enumerate(rules):
        if not r.get("enabled", True):
            continue
        pat = (r.get("pattern") or "").strip()
        if not pat:
            continue
        try:
            rx = compile_rule(pat)
            before = out
            out = rx.sub(" ", out)
            if out != before:
                name = str(r.get("name") or "").strip()
                label = f"#{i+1} {name}".strip()
                hits.append(label)
        except Exception:
            continue
    out = re.sub(r"\s+", " ", out).strip()
    return out, hits


class WorkshopPanel(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        # (embedded) window settings are handled by the toplevel
        # (embedded) window settings are handled by the toplevel
        # (embedded) window settings are handled by the toplevel

        self.state = load_latest_prep_state()
        self.genre = normalize_genre(self.state.get("genre", "その他"))
        self.mode_label = normalize_mode(self.state.get("purpose", "SEARCH_DISPLAY"))
        self.strength_label = normalize_strength(self.state.get("strength", "WEAK"))
        self.repo_path = repo_path_for_genre(self.genre)

        # user tokens (KEEP/IGNORE) - Stage1確定仕様
        self.user_keep_tokens = []
        self.user_ignore_tokens = []
        self._user_token_last = "－"

        self.keep_examples = list(self.state.get("keep_examples") or DEFAULT_KEEP_EXAMPLES)
        self.noise_examples = list(self.state.get("noise_examples") or DEFAULT_NOISE_EXAMPLES)
        self.repo_note = str(self.state.get("repo_note") or self.state.get("note") or DEFAULT_REPO_NOTE)

        self.rules = []
        self.weakmid_state = None  # saved into repo as JSON
        self.samples = self._load_samples()
        self._samples_mtime = self._get_samples_mtime()
        self._samples_mtime = self._get_samples_mtime()

        self._preview_win = None
        self._build_ui()

        # viewerでフォルダ切替→samples.json更新に追従
        self.bind('<FocusIn>', lambda e: self._maybe_reload_samples())

        self._load_repo()
        self._refresh_user_token_ui()
        self._refresh_tree()
        # 起動時は「AIに渡すリポジトリ」をペースト欄に表示
        try:
            self.ensure_ai_repo_file()
            self.show_repo_text()
        except Exception:
            pass
        self._update_status("準備→作業：往復して調整していく前提です。")

    
    def _get_samples_mtime(self):
        try:
            p = os.path.join(app_dir(), SAMPLES_JSON)
            return os.path.getmtime(p)
        except Exception:
            return None

    def _maybe_reload_samples(self, force=False):
        """samples.json が更新されたら再読込して表示を置き換える。"""
        try:
            m = self._get_samples_mtime()
        except Exception:
            m = None
        if not force and m is not None and getattr(self, "_samples_mtime", None) == m:
            return False
        self.samples = self._load_samples()
        self._samples_mtime = m
        try:
            self._render_samples()
        except Exception:
            pass
        # リポジトリ表示中なら生成物も更新
        try:
            self.ensure_ai_repo_file()
            if getattr(self, "_paste_mode", "") == "repo":
                self.show_repo_text()
        except Exception:
            pass
        return True

    def _load_samples(self):
        p = os.path.join(app_dir(), SAMPLES_JSON)
        d = safe_load_json(p, {"samples": []})
        ss = d.get("samples") if isinstance(d, dict) else []
        if not isinstance(ss, list):
            ss = []
        out = []
        for x in ss[:5000]:
            t = str(x or "").strip()
            if t:
                out.append(t)
        return out

    def _calc_hit_count_for_pattern(self, pattern: str):
        """Count how many current samples would change by applying this ONE pattern.
        Returns int or 'ERR' when the pattern can't compile.
        """
        pat = (pattern or "").strip()
        if not pat:
            return 0
        try:
            rx = compile_rule(pat)
        except Exception:
            return "ERR"

        hit = 0
        for s in (self.samples or []):
            before = re.sub(r"\\s+", " ", str(s)).strip()
            after = re.sub(r"\\s+", " ", rx.sub(" ", str(s))).strip()
            if after != before:
                hit += 1
        return hit

    def _build_ui(self):
        top = ttk.Frame(self, padding=(10, 8, 10, 6))
        top.pack(fill="x")

        ttk.Label(top, text="工房（作業）", font=("Segoe UI", 13, "bold")).pack(side="left")
        header = ttk.Frame(top)
        header.pack(side="top", fill="x")

        meta = ttk.Frame(header)
        meta.pack(side="left", padx=12)

        self.var_meta = tk.StringVar(
            value=f"モード: {self.mode_label}　強さ: {self.strength_label}　ジャンル: {self.genre}"
        )
        ttk.Label(meta, textvariable=self.var_meta).pack(side="left")

        # --- actions (buttons, top row) ---
        self.action_bar = ttk.Frame(header)
        self.action_bar.pack(side="right", anchor="e")

        # Use tk.Button for reliable text color across themes
        def _mk(text, cmd, fg=None):
            b = tk.Button(self.action_bar, text=text, command=cmd)
            if fg:
                b.configure(fg=fg, activeforeground=fg)
            return b

        self._action_buttons = [
            _mk("渡す文の表示", self.show_repo_text),
            _mk("渡す文を消す", self.clear_repo_paste),
            _mk("①AIに渡す文をコピーする", self.copy_repo, fg="#0b5ed7"),
            _mk("②AIの回答を貼る", self.paste_repo_only, fg="#0b5ed7"),
            _mk("③適用", self.apply_paste_now),
            _mk("④プレビュー", self.open_preview),
            _mk("⑤保存工房", self.open_save_screen),
        ]

        for i, b in enumerate(self._action_buttons):
            b.grid(row=0, column=i, padx=(0 if i == 0 else 6), pady=0, sticky="e")

        # --- 強度スイッチ（弱／中／強）：1段下 ---
        self.var_strength = tk.StringVar(value=self.strength_label)
        if self.var_strength.get() == "強":
            self.var_strength.set("中")
        strength_bar = ttk.Frame(top)
        strength_bar.pack(side="top", fill="x", pady=(0, 6))
        sw = ttk.Frame(strength_bar)
        sw.pack(side="right")
        ttk.Label(sw, text="強度").pack(side="left", padx=(0, 6))
        for lab in ("弱", "中"):
            ttk.Radiobutton(
                sw,
                text=lab,
                value=lab,
                variable=self.var_strength,
                command=self._on_strength_changed,
            ).pack(side="left", padx=2)



        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        left = ttk.Frame(body, padding=6)
        body.add(left, weight=3)

        ttk.Label(left, text="適用する式を選択", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 6))

        # --- 式リストのスクロールバー（縦・横） ---
        tree_wrap = ttk.Frame(left)
        tree_wrap.pack(fill="both", expand=True)
        ybar = ttk.Scrollbar(tree_wrap, orient="vertical")
        xbar = ttk.Scrollbar(tree_wrap, orient="horizontal")
        ybar.pack(side="right", fill="y")
        xbar.pack(side="bottom", fill="x")


        self.tree = ttk.Treeview(
            tree_wrap,
            columns=("no","on","name","hit","pattern","note"),
            show="headings",
            selectmode="browse",
            height=16,
        )
        self.tree.heading("no", text="#")
        self.tree.heading("on", text="ON")
        self.tree.heading("name", text="名前")
        self.tree.heading("hit", text="HIT")
        self.tree.heading("pattern", text="pattern（式）")
        self.tree.heading("note", text="注意")
        self.tree.column("no", width=44, anchor="e", stretch=False)
        self.tree.column("on", width=48, anchor="center", stretch=False)
        self.tree.column("name", width=140, anchor="w", stretch=False)
        self.tree.column("hit", width=70, anchor="e", stretch=False)
        self.tree.column("pattern", width=520, anchor="w", stretch=True)
        self.tree.column("note", width=220, anchor="w", stretch=True)
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        ybar.configure(command=self.tree.yview)
        xbar.configure(command=self.tree.xview)


        self.tree.bind("<Double-1>", self.on_double_click_toggle)
        self.tree.bind("<Button-1>", self._on_tree_click, add=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._load_selected_into_editor())

        edit = ttk.LabelFrame(left, text="選択行の編集", padding=8)
        edit.pack(fill="x", pady=(8, 0))

        row1 = ttk.Frame(edit)
        row1.pack(fill="x", pady=(0, 6))
        self.var_enabled = tk.BooleanVar(value=True)
        ttk.Checkbutton(row1, text="ON", variable=self.var_enabled, command=self._commit_editor_to_selected).pack(side="left")

        ttk.Label(row1, text="名前:").pack(side="left", padx=(10, 4))
        self.var_name = tk.StringVar()
        ent_name = ttk.Entry(row1, textvariable=self.var_name, width=24)
        ent_name.pack(side="left")
        attach_context_menu(ent_name)
        ent_name.bind("<FocusOut>", lambda e: self._commit_editor_to_selected())

        ttk.Label(row1, text="注意:").pack(side="left", padx=(10, 4))
        self.var_note = tk.StringVar()
        ent_note = ttk.Entry(row1, textvariable=self.var_note)
        ent_note.pack(side="left", fill="x", expand=True)
        attach_context_menu(ent_note)
        ent_note.bind("<FocusOut>", lambda e: self._commit_editor_to_selected())

        row2 = ttk.Frame(edit)
        row2.pack(fill="x")
        ttk.Label(row2, text="pattern:").pack(side="left", padx=(0, 4))
        self.var_pattern = tk.StringVar()
        ent_pat = ttk.Entry(row2, textvariable=self.var_pattern)
        ent_pat.pack(side="left", fill="x", expand=True)
        attach_context_menu(ent_pat)
        ent_pat.bind("<Return>", lambda e: self._commit_editor_to_selected())
        ent_pat.bind("<FocusOut>", lambda e: self._commit_editor_to_selected())

        tool = ttk.Frame(left)
        tool.pack(fill="x", pady=(8, 0))
        ttk.Button(tool, text="追加", command=self.add_rule).pack(side="left")
        ttk.Button(tool, text="削除", command=self.delete_rule).pack(side="left", padx=6)
        ttk.Button(tool, text="↑", width=3, command=lambda: self.move_rule(-1)).pack(side="left")
        ttk.Button(tool, text="↓", width=3, command=lambda: self.move_rule(1)).pack(side="left", padx=(2, 0))
        ttk.Button(tool, text="適用→プレビュー更新", command=self.refresh_preview).pack(side="right")

        right = ttk.Frame(body, padding=6)
        body.add(right, weight=2)

        # --- Right side: tabbed ---
        nb_right = ttk.Notebook(right)
        nb_right.pack(fill="both", expand=True)

        tab_ai = ttk.Frame(nb_right, padding=0)
        tab_token = ttk.Frame(nb_right, padding=0)
        tab_sample = ttk.Frame(nb_right, padding=0)

        nb_right.add(tab_ai, text="AI回答")
        nb_right.add(tab_token, text="トークン")
        nb_right.add(tab_sample, text="サンプル")

        # ===== Tab: AI回答 =====
        ttk.Label(tab_ai, text="AI回答貼り付け", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 6))

        frm_repo_paste, self.txt_repo_paste = make_text_with_scrollbars(tab_ai, height=14, wrap="none")
        frm_repo_paste.pack(fill="both", expand=True)
        # ペースト内容が変わったら（少し待ってから）自動で取り込み
        self.txt_repo_paste.bind("<KeyRelease>", lambda e: self._schedule_apply_paste())
        self.txt_repo_paste.bind("<FocusOut>", lambda e: self._schedule_apply_paste())

        # ===== Tab: トークン =====
        ttk.Label(tab_token, text="トークン追加（手動）", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 6))

        frm_token = ttk.Frame(tab_token)
        frm_token.pack(anchor="w", pady=(0, 0), fill="x")

        row_token = ttk.Frame(frm_token)
        row_token.pack(fill="x")

        self.entry_user_token = ttk.Entry(row_token, width=34)
        self.entry_user_token.pack(side="left")
        self.entry_user_token.bind("<Return>", lambda e: self.start_user_token_tutorial())

        ttk.Label(row_token, text="1行に1トークン", foreground="#666").pack(side="left", padx=(8, 0))

        row_actions = ttk.Frame(frm_token)
        row_actions.pack(fill="x", pady=(6, 0))

        ttk.Button(row_actions, text="追加（KEEP/IGNORE確認）", command=self.start_user_token_tutorial).pack(side="left")

        self.lbl_user_token_counts = ttk.Label(row_actions, text="KEEP: 0  /  IGNORE: 0", foreground="#666")
        self.lbl_user_token_counts.pack(side="left", padx=(10, 0))

        self.lbl_user_token_last = ttk.Label(
            row_actions, text="最後に追加: －", foreground="#666", wraplength=420, justify="left"
        )
        self.lbl_user_token_last.pack(side="left", padx=(10, 0), fill="x", expand=True)

        # ===== Tab: サンプル =====
        ttk.Label(tab_sample, text="プレビュー用サンプル（viewerから自動）", font=("Segoe UI", 10, "bold")).pack(anchor="w")

        sample_block = ttk.Frame(tab_sample)
        sample_block.pack(fill="both", expand=True, pady=(6, 0))

        frm_samples, self.txt_samples = make_text_with_scrollbars(sample_block, height=10, wrap="none")
        frm_samples.pack(fill="both", expand=True)
        self._render_samples()

        # Bottom controls: stick to bottom
        bottom_block = ttk.Frame(sample_block)
        bottom_block.pack(side="bottom", fill="x", pady=(8, 0))

        ttk.Label(bottom_block, text="追加テキスト記入欄（プレビュー用に1件追加）").pack(anchor="w")
        self.ent_sample_add = ttk.Entry(bottom_block)
        self.ent_sample_add.pack(fill="x", pady=(4, 0))
        self.ent_sample_add.bind("<Return>", lambda e: self.add_samples_from_extra())

        row_add_samples = ttk.Frame(bottom_block)
        row_add_samples.pack(fill="x", pady=(4, 0))
        ttk.Button(row_add_samples, text="サンプルへ追加", command=self.add_samples_from_extra).pack(side="left")
        ttk.Button(row_add_samples, text="クリア", command=lambda: self.ent_sample_add.delete(0, "end")).pack(side="left", padx=(6, 0))

        # default tab
        nb_right.select(tab_ai)


        self.status = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.status, padding=(10, 0, 10, 8)).pack(fill="x")


    def _on_strength_changed(self):
        """強度スイッチ変更：表示とフィルタを更新し、last_send.json（STATE_JSON）へ保存する。"""
        v = (self.var_strength.get() or "").strip()
        if v not in ("弱", "中", "強"):
            return
        self.strength_label = v

        # stateは内部表現（WEAK/MEDIUM/STRONG）で保持
        if v == "弱":
            self.state["strength"] = "WEAK"
        elif v == "中":
            self.state["strength"] = "MEDIUM"
        else:
            self.state["strength"] = "STRONG"

        try:
            safe_save_json(os.path.join(app_dir(), STATE_JSON), self.state)
        except Exception:
            pass

        # 上部メタ更新
        try:
            self.var_meta.set(f"モード: {self.mode_label}　強さ: {self.strength_label}　ジャンル: {self.genre}")
        except Exception:
            pass

        # 表示フィルタとプレビュー更新（強度で見える式が変わる）
        try:
            self._refresh_tree()
            self.refresh_preview()
        except Exception:
            pass


    def _load_repo(self):
        """ジャンル別リポジトリを読み込む。無ければ“送信＝作成”として空で新規作成する。"""
        # 送信されて工房が開いた時点で、保存先（リポジトリ）が存在する状態にする
        if not os.path.exists(self.repo_path):
            # create empty repo file immediately
            payload = {
                "app": "Readable Filenames",
                "genre": self.genre,
                "rules": [dict(r) for r in DEFAULT_RULES_WEAK],
                "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
            }
            try:
                safe_save_json(self.repo_path, payload)
            except Exception:
                # 保存に失敗しても起動は続ける（後でユーザーが保存できる）
                pass
            self.rules = []
            return

        data = safe_load_json(self.repo_path, {"rules": []})
        # load user tokens (KEEP/IGNORE)
        if isinstance(data, dict):
            self.user_keep_tokens = list(data.get('user_keep_tokens') or [])
            self.user_ignore_tokens = list(data.get('user_ignore_tokens') or [])
        else:
            self.user_keep_tokens = []
            self.user_ignore_tokens = []

        rules = data.get("rules") if isinstance(data, dict) else []
        if not isinstance(rules, list):
            rules = []
        out = []
        for r in rules:
            if not isinstance(r, dict):
                continue
            out.append({
                "enabled": bool(r.get("enabled", True)),
                "name": str(r.get("name", "") or ""),
                "pattern": str(r.get("pattern", "") or ""),
                "note": str(r.get("note", "") or ""),
                "tier": str(r.get("tier", "WEAK") or "WEAK"),
            })
        self.rules = out

    
    # ---------- user token (KEEP/IGNORE) ----------
    def _refresh_user_token_ui(self):
        # widgets may not exist if UI build changes
        try:
            keep_n = len(self.user_keep_tokens)
            ign_n = len(self.user_ignore_tokens)
            if hasattr(self, "lbl_user_token_counts"):
                self.lbl_user_token_counts.configure(text=f"KEEP: {keep_n}  /  IGNORE: {ign_n}")
            if hasattr(self, "lbl_user_token_last"):
                self.lbl_user_token_last.configure(text=f"最後に追加: {self._user_token_last}")
        except Exception:
            pass

    
    def _update_user_token_last_wrap(self):
        # Make the "最後に追加" label wrap only when horizontal space is tight (1〜1.5行)
        try:
            w = self.winfo_width()
        except Exception:
            return
        wrap = max(220, min(720, w - 520))
        try:
            self.lbl_user_token_last.configure(wraplength=wrap)
        except Exception:
            pass

    def start_user_token_tutorial(self):
        try:
            token = (self.entry_user_token.get() if hasattr(self, "entry_user_token") else "").strip()
        except Exception:
            token = ""
        if not token:
            messagebox.showinfo(APP_TITLE, "トークンを1つ入力してください。")
            return

        r1 = messagebox.askyesnocancel(
            APP_TITLE,
            f"入力されたトークン:\n\n{token}\n\nKEEP に追加しますか？\n\n（いいえ＝次に IGNORE を確認 / キャンセル＝中止）"
        )
        if r1 is None:
            return
        if r1 is True:
            self._add_user_token(token, keep=True)
            return

        r2 = messagebox.askyesnocancel(APP_TITLE, "IGNORE に追加しますか？\n\n（キャンセル＝中止）")
        if r2 is None:
            return
        if r2 is True:
            self._add_user_token(token, keep=False)

    def _add_user_token(self, token: str, keep: bool):
        token = str(token).strip()
        if not token:
            return
        arr = self.user_keep_tokens if keep else self.user_ignore_tokens
        if token in arr:
            messagebox.showinfo(APP_TITLE, "既に追加されています。")
            return
        arr.append(token)
        self._user_token_last = f"{'KEEP' if keep else 'IGNORE'}: {token}"
        try:
            if hasattr(self, "entry_user_token"):
                self.entry_user_token.delete(0, "end")
                self.entry_user_token.focus_set()
        except Exception:
            pass
        self._refresh_user_token_ui()



    def _apply_weakmid_to_rules(self, payload):
        """
        WEAK/MID画面の結果で、工房の式リスト順を更新する。
        - 左ペイン order の順で self.rules を並べ替える（pattern一致ベース）
        - enabled/tier 等は既存のまま（この段階では編集しない）
        """
        if not isinstance(payload, dict):
            return
        order = payload.get("order")
        if not isinstance(order, list) or not self.rules:
            return

        # index map (pattern -> position). duplicates are ignored (first wins)
        idx = {}
        for i, p in enumerate(order):
            p = str(p or "").strip()
            if p and p not in idx:
                idx[p] = i

        # stable: items with known order first, then the rest
        def keyfunc(r):
            p = str(r.get("pattern") or "").strip()
            return (0, idx[p]) if p in idx else (1, 10**9)

        self.rules = sorted(list(self.rules), key=keyfunc)
    def open_save_screen(self):
        """保存工房ボタン：強度に応じて画面を切り替える。"""
        s = (self.var_strength.get() if hasattr(self, "var_strength") else "").strip()
        if s == "強":
            try:
                StrongSaveWindow(self, workshop_panel=self)
            except Exception as e:
                try:
                    messagebox.showerror(APP_TITLE, f"保存工房（強）の起動に失敗しました:\n{e}")
                except Exception:
                    pass
        else:
            self.open_weakmid_screen()
    def open_save_screen(self):
        """保存工房ボタン：強度に応じて画面を切り替える。"""
        s = (self.var_strength.get() if hasattr(self, "var_strength") else "").strip()
        if s == "強":
            try:
                StrongSaveWindow(self, workshop_panel=self)
            except Exception as e:
                try:
                    messagebox.showerror(APP_TITLE, f"保存工房（強）の起動に失敗しました:\n{e}")
                except Exception:
                    pass
        else:
            self.open_weakmid_screen()





    def open_weakmid_screen(self):
        """保存工房ボタン：WEAK/MID 作業画面を開く（並べ替え・タグ付け→適用で工房側も即更新）。"""
        # 左ペイン（材料）：現在の rules から pattern を順番どおりに作る（編集不可）
        patterns = []
        mid_flags = set()

        for r in (self.rules or []):
            p = str(r.get("pattern") or "").strip()
            if not p:
                continue
            patterns.append(p)
            tier = str(r.get("tier", "WEAK") or "WEAK").upper()
            if tier in ("MEDIUM", "MID", "MIDDLE", "中"):
                mid_flags.add(p)

        # 既存の保存データから weakmid を復元（あれば）
        existing = safe_load_json(self.repo_path, {})
        prev = existing.get("weakmid") if isinstance(existing, dict) else None

        # ジャンルは検索モードのデフォルトと統一（＋ユーザー追加分があれば末尾に足す）
        genres = list(DEFAULT_GENRES)
        if isinstance(prev, dict):
            gs = prev.get("genres")
            if isinstance(gs, dict) and gs:
                for g in list(gs.keys()):
                    if g not in genres:
                        genres.append(g)

        def _on_apply(payload):
            # 1) 保存用に保持
            self.weakmid_state = payload

            # 2) 工房（この画面）も即更新：順序だけ反映 → ツリー/プレビュー更新
            try:
                self._apply_weakmid_to_rules(payload)
                self._refresh_tree()
                self.refresh_preview()
            except Exception:
                pass

            # 3) 検索モードへ渡す（適用スナップショット：current / prev）
            try:
                st_path = os.path.join(app_dir(), STATE_JSON)
                st = safe_load_json(st_path, {})
                if not isinstance(st, dict):
                    st = {}
                cur = st.get("applied_current")
                st["applied_prev"] = cur if isinstance(cur, dict) else None
                st["applied_current"] = payload
                safe_save_json(st_path, st)
            except Exception:
                pass

            # 4) JSONへ保存（従来の保存にweakmidを同梱）
            self.save_repo()

            # 4) 画面が「何も起きない」感を避ける
            try:
                self._update_status("WEAK/MID を適用しました（工房の式リスト順を更新し、保存しました）。")
            except Exception:
                pass

            # close workscreen window
            try:
                win.destroy()
            except Exception:
                pass

        win = tk.Toplevel(self)
        win.title("保存工房（弱/中）")
        try:
            win.geometry("1100x650")
            win.minsize(900, 500)
        except Exception:
            pass

        # 目印（これすら見えないなら、UI配置以前の問題）
        try:
            ttk.Label(win, text="(読み込み中...)").pack(anchor="w", padx=8, pady=6)
        except Exception:
            pass

        try:
            screen = WeakMidScreen(
                win,
                strength=self.strength_label,
                patterns=patterns,
                mid_flags=mid_flags,
                genres=genres,
                on_apply=_on_apply,
            )
            # 重要：Frame を表示ツリーに参加させる（真っ白対策）
            screen.pack(fill="both", expand=True)
            try:
                win.update_idletasks()
            except Exception:
                pass
        except Exception as e:
            try:
                messagebox.showerror(APP_TITLE, "保存工房（弱/中）のUI生成に失敗しました。\n\n" + str(e))
            except Exception:
                pass
            try:
                print("WeakMidScreen build failed:")
                traceback.print_exc()
            except Exception:
                pass
            return

        # 既存の weakmid データがあれば、ジャンル割当を復元する（UIは自動反映）
        if isinstance(prev, dict):
            try:
                gmap = prev.get("genres")
                if isinstance(gmap, dict):
                    for g in list(gmap.keys()):
                        if g not in screen.genre_map:
                            screen.genre_map[g] = []
                    for g, lst in gmap.items():
                        if not isinstance(lst, list):
                            continue
                        screen.genre_map[g] = [str(x) for x in lst if str(x).strip() in patterns]
                    screen.refresh_right()
            except Exception:
                pass


    def save_repo(self):
        # user_keep_tokens / user_ignore_tokens（確定仕様）
        user_keep = list(self.user_keep_tokens)
        user_ignore = list(self.user_ignore_tokens)

        # 互換フィールド（未使用前提。将来捨ててもOK）
        terms = []
        keep_words = []
        ignore_words = []


        payload = {
            "app": "Readable Filenames",
            "type": "AI_REPOSITORY",
            "genre": self.genre,
            "purpose": self.mode_label,   # 検索用 / 保存用
            "strength": self.strength_label,
            "rules": self.rules,            "terms": terms,
            "keep_words": keep_words,
            "ignore_words": ignore_words,
            "user_keep_tokens": user_keep,
            "user_ignore_tokens": user_ignore,
            "weakmid": (self.weakmid_state or None),
        }
        safe_save_json(self.repo_path, payload)
        self._update_status(f"保存しました: {os.path.relpath(self.repo_path, app_dir())}")

    def copy_repo(self):
        # Copy exactly what is currently visible in the repository text box.
        # (User may have edited it; that text is the source of truth.)
        try:
            s = self.txt_repo_paste.get("1.0", "end-1c")
        except Exception:
            s = ""
        if not str(s).strip():
            payload = self._build_ai_repo_payload()
            s = json.dumps(payload, ensure_ascii=False, indent=2)
        try:
            self.clipboard_clear()
            self.clipboard_append(s)
            # コピーしたらペースト欄は空にする（作業導線）
            try:
                self.txt_repo_paste.delete("1.0", "end")
            except Exception:
                pass
            self._update_status("リポジトリ（JSON）をコピーしました。")
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"コピーに失敗しました:\n{e}")

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())

        visible_no = 0

        # show only rules allowed by current strength (弱/中/強)
        if self.strength_label == "弱":
            allowed = {"WEAK"}
        elif self.strength_label == "中":
            allowed = {"WEAK", "MEDIUM"}
        else:
            allowed = {"WEAK", "MEDIUM", "STRONG"}

        for i, r in enumerate(self.rules):
            tier = str(r.get("tier", "WEAK") or "WEAK").upper()
            if tier not in allowed:
                continue
            on = "☑" if r.get("enabled", True) else "☐"
            visible_no += 1
            hit = self._calc_hit_count_for_pattern(r.get("pattern", ""))
            self.tree.insert("", "end", iid=f"r{i}",
                             values=(visible_no, on, r.get("name", ""), hit, r.get("pattern", ""), r.get("note", "")))

    def _selected_index(self):
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        m = re.match(r"^r(\d+)$", iid)
        if not m:
            return None
        idx = int(m.group(1))
        if 0 <= idx < len(self.rules):
            return idx
        return None

    def _load_selected_into_editor(self):
        idx = self._selected_index()
        if idx is None:
            return
        r = self.rules[idx]
        self.var_enabled.set(bool(r.get("enabled", True)))
        self.var_name.set(r.get("name", ""))
        self.var_pattern.set(r.get("pattern", ""))
        self.var_note.set(r.get("note", ""))

    def _commit_editor_to_selected(self):
        idx = self._selected_index()
        if idx is None:
            return
        self.rules[idx]["enabled"] = bool(self.var_enabled.get())
        self.rules[idx]["name"] = (self.var_name.get() or "").strip()
        self.rules[idx]["pattern"] = (self.var_pattern.get() or "").strip()
        self.rules[idx]["note"] = (self.var_note.get() or "").strip()
        self._refresh_tree()
        self.tree.selection_set(f"r{idx}")
        self.tree.see(f"r{idx}")

    def on_double_click_toggle(self, _evt):
        idx = self._selected_index()
        if idx is None:
            return
        self.rules[idx]["enabled"] = not bool(self.rules[idx].get("enabled", True))
        self._refresh_tree()
        self.tree.selection_set(f"r{idx}")
        self.refresh_preview()
        # プレビュー用サンプル欄も最新に置き換え
        try:
            self.samples = self._load_samples()
            self._render_samples()
        except Exception:
            pass

    def add_rule(self):
        self.rules.append({"enabled": True, "tier": "WEAK", "name": "New", "pattern": r"\bWORD\b", "note": ""})
        self._refresh_tree()
        idx = len(self.rules) - 1
        self.tree.selection_set(f"r{idx}")
        self.tree.see(f"r{idx}")
        self._load_selected_into_editor()

    def delete_rule(self):
        idx = self._selected_index()
        if idx is None:
            return
        self.rules.pop(idx)
        self._refresh_tree()
        self._update_status("削除しました。")
        self.refresh_preview()

    def move_rule(self, delta: int):
        idx = self._selected_index()
        if idx is None:
            return
        j = idx + delta
        if not (0 <= j < len(self.rules)):
            return
        self.rules[idx], self.rules[j] = self.rules[j], self.rules[idx]
        self._refresh_tree()
        self.tree.selection_set(f"r{j}")
        self.tree.see(f"r{j}")
        self.refresh_preview()

    def _build_ai_repo_payload(self):
        # サンプル取得（viewer → samples.json / 手動プレビュー）
        try:
            manual = self.txt_samples.get("1.0", "end-1c").splitlines()
            sample_lines = [s.strip() for s in manual if s.strip()]
        except Exception:
            sample_lines = []

        if not sample_lines:
            sample_lines = list(self.samples or [])

        # 括弧トークン抽出（機械的）
        token_items = extract_bracket_tokens(sample_lines, DEFAULT_BRACKET_PAIRS)

        tokens = [d["token"] for d in token_items]
        token_counts = {d["token"]: d["count"] for d in token_items}

        payload = {
            "app": "ReadableFilenames",
            "type": "AI_REPOSITORY",
            "stage": "1",

            # 確定仕様：専用配列のみ使用
            "user_keep_tokens": list(self.user_keep_tokens),
            "user_ignore_tokens": list(self.user_ignore_tokens),

            "purpose": "括弧トークン（括弧＋中身＋括弧）＝1塊を列挙し、各トークン専用の単純式を得る",

            "instructions_to_AI": [
                "tokens は『括弧＋中身＋括弧』の塊そのもの。括弧も中身も分解・加工・省略禁止。",
            "あなたは書記。判断しない。",
                "tokens 配列のみを処理する。",
                "tokens は分解しない。意味を考えない。一般化しない。",
                "各 token について re.escape 相当の式を1行で返す。",
                "出力は pattern: 行のみ。",
                "tokens が0件なら pattern: a^ の1行だけ出力。",
            ],

            "tokens": tokens,
            "token_counts": token_counts,

            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }

        return payload


    def ensure_ai_repo_file(self):
        """AIへ渡すためのデフォルトリポジトリ（JSON）を作成/更新して保存する。"""
        try:
            p = os.path.join(app_dir(), AI_REPO_JSON)
            payload = self._build_ai_repo_payload()
            safe_save_json(p, payload)
        except Exception:
            pass

    def show_repo_text(self):
        self._paste_mode = 'repo'
        """現在のリポジトリ（AIへ渡す用の平文）をペースト欄へ表示する。"""
        try:
            payload = self._build_ai_repo_payload()
        except Exception as e:
            # ここで落ちると「何も起きない」ように見えるので、明示する
            try:
                messagebox.showerror(APP_TITLE, f"リポジトリ生成に失敗しました:\n{e}")
            except Exception:
                pass
            payload = {}
        tokens = payload.get("tokens") if isinstance(payload, dict) else None
        if not isinstance(tokens, list):
            tokens = []

        repo_lines = [
            "ReadableFilenames",
            "AI_REPOSITORY",
            "stage 1",
            "",
            "目的",
            "tokens（括弧＋中身＋括弧＝1塊）を、それぞれ単独の正規表現に変換する。",
            "ファイル名本文は渡さない。",
            "",
            "入力",
            "下に並ぶ tokens のみを処理すること。",
            "tokens は分解しない。意味を考えない。一般化しない。",
            "",
            "禁止",
            "判断・説明・要約・提案。",
            "OR(|)や複数意味の表現。",
            "",
            "出力",
            "答えはワンクリックでコピーできる形で提供してください。",
            "各 token について、次の形の行だけを書いてください。",
            "",
            "pattern: 正規表現",
            "",
            "この形以外の文字は一切書かないでください。",
            "前置き、説明文、番号、空行、装飾は禁止。",
            "",
            "tokens が0件の場合は、次の1行だけを書いて終了してください。",
            "",
            "pattern: a^",
            "",
            "tokens",
        ]

        # tokens をそのまま列挙（1行=1トークン）
        if tokens:
            repo_lines.extend([str(t) for t in tokens])
        else:
            repo_lines.append("（tokens がありません）")

        s = "\n".join(repo_lines) + "\n"

        try:
            self._suppress_paste_apply = True
            self.txt_repo_paste.delete("1.0", "end")
            self.txt_repo_paste.insert("1.0", s)
            self._update_status("リポジトリ（平文）を表示しました。全文コピーしてAIに渡してください。")
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"表示に失敗しました:\n{e}")
        finally:
            self._suppress_paste_apply = False

    def _schedule_apply_paste(self):
        """KeyRelease連打を吸収して、少し待ってから適用する。"""
        if getattr(self, "_suppress_paste_apply", False):
            return
        try:
            if self._paste_job is not None:
                try:
                    self.after_cancel(self._paste_job)
                except Exception:
                    pass
            self._paste_job = self.after(450, self.apply_paste_now)
        except Exception:
            pass

    def _normalize_rules_list(self, rules):
        out = []
        if not isinstance(rules, list):
            return out
        for r in rules:
            if not isinstance(r, dict):
                continue
            out.append({
                "enabled": bool(r.get("enabled", True)),
                "name": str(r.get("name", "") or ""),
                "pattern": str(r.get("pattern", "") or ""),
                "note": str(r.get("note", "") or ""),
                "tier": str(r.get("tier", "WEAK") or "WEAK"),
            })
        return out

    def apply_paste_now(self):
        """
        ペースト欄の内容を適用する。

        - JSON（{"rules":[...] }）なら「置き換え」
        - AIブロック（name/pattern/注意）なら「追加」
        """
        try:
            txt = self.txt_repo_paste.get("1.0", "end-1c")
        except Exception:
            return
        if not (txt or "").strip():
            # 空なら「全クリア」扱い
            self.rules = []
            self._refresh_tree()
            try:
                self.refresh_preview()
            except Exception:
                pass
            self._update_status("ペースト欄が空のため、式リストを空にしました。")
            return

        # 1) JSONとして読めるなら置き換え（編集リポジトリの反映）
        try:
            d = json.loads(txt)
            if isinstance(d, dict) and isinstance(d.get("rules"), list):
                self.rules = self._normalize_rules_list(d.get("rules"))
                self._refresh_tree()
                self._update_status("ペースト（JSON）を適用しました：式リストを置き換えました。")
                self.refresh_preview()
                # 表示追従（リポジトリ表示中なら更新）
                try:
                    self.ensure_ai_repo_file()
                    if getattr(self, '_paste_mode', '') == 'repo':
                        self.show_repo_text()
                except Exception:
                    pass
                return
        except Exception:
            pass

        # 2) AIブロックとして取り込み（追加）
        blocks = parse_ai_blocks(txt)
        if not blocks:
            # 何も取れない場合は何もしない（誤爆防止）
            return
        # 置き換え（積み上げ防止）
        self.rules = list(blocks)
        self._refresh_tree()
        self._update_status(f"ペースト（AI回答）を適用しました：{len(blocks)} 件（置き換え）")
        self.refresh_preview()
        self._write_applied_snapshot_for_viewer()
        try:
            self.ensure_ai_repo_file()
            if getattr(self, '_paste_mode', '') == 'repo':
                self.show_repo_text()
        except Exception:
            pass


    def _write_applied_snapshot_for_viewer(self):
        """検索モード（viewer）が参照する applied_current を更新する。
        保存工房を開かなくても、③適用の直後に検索候補へ反映されるようにする。

        重要: ここで書く genres は「現在ジャンルだけ」ではなく、
        REPO_DIR 内の rules_*.json を走査して **全ジャンル分** をまとめる。
        （空ジャンルも空リストで残す）
        """
        try:
            # 1) Collect all genre rules from repo files (rules_<genre>.json)
            genres = {}
            repo_dir = os.path.join(app_dir(), REPO_DIR)
            if os.path.isdir(repo_dir):
                for fn in sorted(os.listdir(repo_dir)):
                    if not (fn.startswith("rules_") and fn.endswith(".json")):
                        continue
                    genre = fn[len("rules_"):-len(".json")]
                    genre = normalize_genre(genre)
                    path = os.path.join(repo_dir, fn)
                    data = safe_load_json(path, {})
                    rules = data.get("rules") if isinstance(data, dict) else None
                    patterns = []
                    if isinstance(rules, list):
                        for r in rules:
                            if not isinstance(r, dict):
                                continue
                            if not bool(r.get("on", True)):
                                continue
                            p = str(r.get("pattern") or "").strip()
                            if p:
                                patterns.append(p)
                    genres[genre] = patterns

            # 2) Ensure default genres exist (so empty boxes still appear)
            for g in DEFAULT_GENRES:
                g2 = normalize_genre(g)
                genres.setdefault(g2, [])

            # 3) Build a stable global order (unique, first-seen)
            seen = set()
            order = []
            for g, pats in genres.items():
                for p in pats:
                    if p in seen:
                        continue
                    seen.add(p)
                    order.append(p)

            payload = {
                "app": "ReadableFilenames",
                "source": "workshop_apply",
                "genre": normalize_genre(self.genre),
                "genres": genres,
                "order": order,
            }

            st_path = os.path.join(app_dir(), STATE_JSON)
            st = safe_load_json(st_path, {})
            if not isinstance(st, dict):
                st = {}
            cur = st.get("applied_current")
            st["applied_prev"] = cur if isinstance(cur, dict) else None
            st["applied_current"] = payload
            safe_save_json(st_path, st)
        except Exception:
            pass

    def import_from_paste(self):
        self.apply_paste_now()

    def add_samples_from_extra(self):
        """追加テキスト記入欄の内容を、プレビュー用サンプルに追加する。"""
        raw = ""
        # 新UI: 1行 Entry
        if hasattr(self, "ent_sample_add"):
            try:
                raw = self.ent_sample_add.get()
            except Exception:
                raw = ""
        # 互換: 旧UI（複数行 Text）
        if not raw and hasattr(self, "txt_samples_add"):
            try:
                raw = self.txt_samples_add.get("1.0", "end")
            except Exception:
                raw = ""

        items = [s.strip() for s in raw.splitlines() if s.strip()]
        if not items:
            messagebox.showinfo('追加', '追加するサンプルがありません。', parent=self)
            return
        existing = set([s.strip() for s in (self.samples or []) if str(s).strip()])
        add = [s for s in items if s not in existing]
        if not add:
            messagebox.showinfo('追加', 'すべて既にサンプル欄にあります。', parent=self)
            return
        # samples.json に追記（重複は追加しない）→ 画面は置き換え表示
        try:
            p = os.path.join(app_dir(), SAMPLES_JSON)
            d = safe_load_json(p, {"samples": []})
            ss = d.get("samples") if isinstance(d, dict) else []
            if not isinstance(ss, list):
                ss = []
            existing_file = set([str(x or "").strip() for x in ss if str(x or "").strip()])
            for s in add:
                if s not in existing_file:
                    ss.append(s)
                    existing_file.add(s)
            if isinstance(d, dict):
                d["samples"] = ss
            else:
                d = {"samples": ss}
            safe_save_json(p, d)
        except Exception:
            # 失敗してもメモリ上には反映
            self.samples = list(self.samples) + list(add)
        # 画面反映（置き換え）
        try:
            self.samples = self._load_samples()
        except Exception:
            pass
        self._render_samples()
        # 追加欄クリア
        if hasattr(self, 'ent_sample_add'):
            self.ent_sample_add.delete(0, 'end')
            self.ent_sample_add.focus_set()
        if hasattr(self, 'txt_samples_add'):
            self.txt_samples_add.delete('1.0', 'end')
        self._update_status(f'サンプルを {len(add)} 件追加しました。')

    def _render_samples(self):
        self.txt_samples.delete("1.0", "end")
        if not self.samples:
            self.txt_samples.insert("end", "（サンプルがありません。viewerでフォルダを読み込んでから工房を開いてください）")
            return
        self.txt_samples.insert("end", "\n".join(self.samples))

    def _poll_external_state(self):
        """viewer が samples.json を更新したら、サンプル欄を追従更新（置き換え表示）。"""
        try:
            m = self._get_samples_mtime()
            if m is not None and getattr(self, "_samples_mtime", None) != m:
                self.samples = self._load_samples()
                self._samples_mtime = m
                try:
                    self._render_samples()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self.after(700, self._poll_external_state)
        except Exception:
            pass

    def open_preview(self):
        if self._preview_win and self._preview_win.winfo_exists():
            self._preview_win.lift()
            self.refresh_preview()
            return

        win = tk.Toplevel(self)
        win.title("プレビュー（即時確認）")
        win.geometry("980x640")
        win.minsize(820, 520)
        self._preview_win = win

        frm = ttk.Frame(win, padding=10)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="前 → 後（ONの式を上から順に適用）", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        frm_preview, self.txt_preview = make_text_with_scrollbars(frm, height=20, wrap="none")
        frm_preview.pack(fill="both", expand=True, pady=(6, 0))
        self.refresh_preview()

    def refresh_preview(self):
        if not (self._preview_win and self._preview_win.winfo_exists()):
            return

        manual = self.txt_samples.get("1.0", "end-1c").splitlines()
        samples = [s.strip() for s in manual if s.strip()]
        if not samples:
            samples = self.samples[:]

        allowed = set()
        if self.strength_label == "弱":
            allowed = {"WEAK"}
        elif self.strength_label == "中":
            allowed = {"WEAK", "MEDIUM"}
        else:
            allowed = {"WEAK", "MEDIUM", "STRONG"}

        enabled_rules = [r for r in self.rules if r.get("enabled", True) and str(r.get("tier","WEAK")).upper() in allowed]
        out_lines = []
        out_lines.append(f"ジャンル: {self.genre} / モード: {self.mode_label} / 強さ: {self.strength_label}")
        out_lines.append(f"ONの式: {len(enabled_rules)} 件（上から順に適用）")
        out_lines.append("")

        bad = []
        for i, r in enumerate(enabled_rules):
            pat = (r.get("pattern") or "").strip()
            if not pat:
                continue
            try:
                compile_rule(pat)
            except Exception as e:
                bad.append((i, r.get("name", ""), str(e)))

        if bad:
            out_lines.append("⚠ コンパイルに失敗する式（プレビューでは無視されます）:")
            for i, name, err in bad[:20]:
                out_lines.append(f"  - #{i+1} {name}: {err}")
            out_lines.append("")

        for s in samples[:40]:
            after, hits = apply_rules_trace(s, enabled_rules)
            out_lines.append(f"前: {s}")
            out_lines.append(f"後: {after}")
            if after == "":
                out_lines.append("※ 全て消えて空になりました（誤爆の可能性あり）")
            if hits:
                out_lines.append("効いた式: " + ", ".join(hits))
            out_lines.append("")

        self.txt_preview.delete("1.0", "end")
        self.txt_preview.insert("end", "\n".join(out_lines))

    def _update_status(self, msg: str):
        self.status.set(msg)



    def paste_repo_only(self):
        self._paste_mode = 'free'
        """クリップボードの内容をAI回答貼り付け欄に貼るだけ。適用はしない。"""
        try:
            s = self.clipboard_get()
        except Exception:
            return
        try:
            self._suppress_paste_apply = True
            self.txt_repo_paste.delete("1.0", "end")
            self.txt_repo_paste.insert("1.0", s)
        finally:
            self._suppress_paste_apply = False
        self._update_status("回答をペーストしました。必要なら『適用』を押してください。")

    def clear_repo_paste(self):
        self._paste_mode = 'free'
        """AI回答貼り付け欄を空にする。"""
        try:
            self._suppress_paste_apply = True
            self.txt_repo_paste.delete("1.0", "end")
        finally:
            self._suppress_paste_apply = False
        self._update_status("リポジトリ欄を消去しました。")

    def _on_tree_click(self, event):
        """TreeviewのON列をクリックしたらその行をトグル（1クリックで反応）。"""
        try:
            col = self.tree.identify_column(event.x)
            row = self.tree.identify_row(event.y)
            if not row:
                return
            self.tree.selection_set(row)
            self.tree.focus(row)
            if col == "#1":
                self._toggle_rule_by_iid(row)
        except Exception:
            pass

    def _toggle_rule_by_iid(self, iid):
        idx = self._index_from_iid(iid)
        if idx is None:
            return
        self.rules[idx]["enabled"] = not bool(self.rules[idx].get("enabled", False))
        self._refresh_tree()
        self.tree.selection_set(f"r{idx}")
        self.tree.see(f"r{idx}")
        self._load_selected_into_editor()
        self.refresh_preview()

    def _index_from_iid(self, iid):
        if isinstance(iid, str) and iid.startswith("r"):
            try:
                return int(iid[1:])
            except Exception:
                return None
        return None



class WorkshopApp(tk.Tk):
    """Standalone window wrapper (keeps old entrypoint)"""
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        try:
            self.geometry("1180x760")
            self.minsize(1040, 700)
        except Exception:
            pass
        self.panel = WorkshopPanel(self)
        self.panel.pack(fill="both", expand=True)

        # --- メニュー：強の入口はここだけ ---
        try:
            menubar = tk.Menu(self)
            m = tk.Menu(menubar, tearoff=0)
            m.add_command(label="保存工房（強）", command=lambda: StrongSaveWindow(self, workshop_panel=self.panel))
            m.add_separator()
            m.add_command(label="終了", command=self.destroy)
            menubar.add_cascade(label="メニュー", menu=m)
            self.config(menu=menubar)
        except Exception:
            pass


# ==========================================================
# 保存工房（弱/中）画面：左右2ペイン（式リスト / ジャンル箱）
# - 式は編集不可
# - 中央ボタンで追加/削除（タグ付け）
# - ジャンル側でのみ ↑↓ を許可
# - [適用] で payload(dict) を on_apply に渡す
# ==========================================================
class WeakMidScreen(ttk.Frame):
    def __init__(self, master, *, strength: str, patterns, mid_flags=None, genres=None, on_apply=None):
        super().__init__(master)
        self.master = master
        self.strength = strength or "弱"
        self.patterns = [str(x) for x in (patterns or []) if str(x).strip()]
        self.mid_flags = set([str(x) for x in (mid_flags or set())])
        self.on_apply = on_apply

        self.genre_values = list(genres) if genres else ["アニメ", "音楽", "ドラマ", "映画", "その他"]
        if "その他" not in self.genre_values:
            self.genre_values.append("その他")

        # genre_map: genre -> list[pattern]
        self.genre_map = {g: [] for g in self.genre_values}
        self.cur_genre = tk.StringVar(value=self.genre_values[0])
        self.var_status = tk.StringVar(value="")

        self._build_ui()
        self.pack(fill="both", expand=True)

    def _build_ui(self):
        # top
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=6)

        ttk.Label(top, text="ジャンル").pack(side="left")
        self.cb_genre = ttk.Combobox(top, values=self.genre_values, textvariable=self.cur_genre, state="readonly", width=18)
        self.cb_genre.pack(side="left", padx=6)
        self.cb_genre.bind("<<ComboboxSelected>>", lambda e: self.refresh_right())

        ttk.Button(top, text="編集", command=self.edit_genres).pack(side="left")

        ttk.Label(top, text=f"  強度: {self.strength}").pack(side="right")

        # main panes
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=8, pady=(0, 6))

        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0)
        body.columnconfigure(2, weight=1)
        body.rowconfigure(0, weight=1)

        # left list
        lf = ttk.Labelframe(body, text="式リスト（WEAK + MID）")
        lf.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        lf.rowconfigure(0, weight=1)
        lf.columnconfigure(0, weight=1)

        self.lb_left = tk.Listbox(lf, selectmode="extended", activestyle="none")
        self.lb_left.grid(row=0, column=0, sticky="nsew")
        sb1 = ttk.Scrollbar(lf, orient="vertical", command=self.lb_left.yview)
        sb1.grid(row=0, column=1, sticky="ns")
        self.lb_left.configure(yscrollcommand=sb1.set)

        # fill left
        for p in self.patterns:
            prefix = "⚠ " if p in self.mid_flags else ""
            self.lb_left.insert("end", prefix + p)

        # center controls
        cf = ttk.Frame(body)
        cf.grid(row=0, column=1, sticky="ns", padx=4)
        cf.rowconfigure(0, weight=1)
        cf.rowconfigure(3, weight=1)

        ttk.Label(cf, text=" ").grid(row=0, column=0, sticky="n")
        ttk.Button(cf, text="追加 ▶", command=self.add_to_genre).grid(row=1, column=0, pady=4)
        ttk.Button(cf, text="◀ 削除", command=self.remove_from_genre).grid(row=2, column=0, pady=4)
        ttk.Label(cf, text=" ").grid(row=3, column=0, sticky="s")

        # right list
        rf = ttk.Labelframe(body, text="ジャンル箱（式が見える）")
        rf.grid(row=0, column=2, sticky="nsew", padx=(6, 0))
        rf.rowconfigure(0, weight=1)
        rf.columnconfigure(0, weight=1)

        self.lb_right = tk.Listbox(rf, selectmode="extended", activestyle="none")
        self.lb_right.grid(row=0, column=0, sticky="nsew")
        sb2 = ttk.Scrollbar(rf, orient="vertical", command=self.lb_right.yview)
        sb2.grid(row=0, column=1, sticky="ns")
        self.lb_right.configure(yscrollcommand=sb2.set)

        # right buttons (order)
        btns = ttk.Frame(rf)
        btns.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(btns, text="↑", width=6, command=self.move_up).pack(side="left", padx=2)
        ttk.Button(btns, text="↓", width=6, command=self.move_down).pack(side="left", padx=2)

        self.refresh_right()

        # bottom
        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=8, pady=(0, 8))

        ttk.Button(bottom, text="適用", command=self.apply).pack(side="right")
        self.var_status = tk.StringVar(value=f"ジャンル: {self.cur_genre.get()} / 強度: {self.strength}")
        ttk.Label(bottom, textvariable=self.var_status).pack(side="left")

    def _right_patterns(self):
        g = self.cur_genre.get()
        return self.genre_map.get(g, [])

    def refresh_right(self):
        g = self.cur_genre.get()
        self.lb_right.delete(0, "end")
        for p in self.genre_map.get(g, []):
            prefix = "⚠ " if p in self.mid_flags else ""
            self.lb_right.insert("end", prefix + p)
        if hasattr(self, "var_status") and self.var_status is not None:
            self.var_status.set(f"ジャンル: {g} / 強度: {self.strength}")

    def _strip_prefix(self, s: str) -> str:
        s = str(s)
        if s.startswith("⚠ "):
            return s[2:]
        return s

    def add_to_genre(self):
        g = self.cur_genre.get()
        cur = self.genre_map.setdefault(g, [])
        sel = list(self.lb_left.curselection())
        if not sel:
            return
        for i in sel:
            p = self._strip_prefix(self.lb_left.get(i))
            if p in self.patterns and p not in cur:
                cur.append(p)
        self.refresh_right()

    def remove_from_genre(self):
        g = self.cur_genre.get()
        cur = self.genre_map.setdefault(g, [])
        sel = list(self.lb_right.curselection())
        if not sel:
            return
        # remove from back to front
        for i in sorted(sel, reverse=True):
            try:
                p = self._strip_prefix(self.lb_right.get(i))
                if p in cur:
                    cur.remove(p)
            except Exception:
                pass
        self.refresh_right()

    def move_up(self):
        g = self.cur_genre.get()
        cur = self.genre_map.setdefault(g, [])
        sel = list(self.lb_right.curselection())
        if not sel:
            return
        # move each selected up preserving relative order
        for i in sel:
            if i <= 0:
                continue
            cur[i-1], cur[i] = cur[i], cur[i-1]
        self.refresh_right()
        for i in [max(0, x-1) for x in sel]:
            self.lb_right.selection_set(i)

    def move_down(self):
        g = self.cur_genre.get()
        cur = self.genre_map.setdefault(g, [])
        sel = list(self.lb_right.curselection())
        if not sel:
            return
        for i in sorted(sel, reverse=True):
            if i >= len(cur)-1:
                continue
            cur[i+1], cur[i] = cur[i], cur[i+1]
        self.refresh_right()
        for i in [min(len(cur)-1, x+1) for x in sel]:
            self.lb_right.selection_set(i)

    def edit_genres(self):
        # minimal editor: add / rename / delete via dialogs
        g = self.cur_genre.get()

        action = simpledialog.askstring("ジャンル編集", "操作: add / rename / delete\n例: add:新ジャンル\nrename:旧→新\ndelete:ジャンル名")
        if not action:
            return
        action = action.strip()
        if action.startswith("add:"):
            name = action[4:].strip()
            if name and name not in self.genre_values:
                self.genre_values.append(name)
                self.genre_map[name] = []
                self.cb_genre["values"] = self.genre_values
                self.cur_genre.set(name)
                self.refresh_right()
        elif action.startswith("rename:"):
            body = action[7:].strip()
            if "→" in body:
                old, new = [x.strip() for x in body.split("→", 1)]
            elif "->" in body:
                old, new = [x.strip() for x in body.split("->", 1)]
            else:
                return
            if old in self.genre_values and new and new not in self.genre_values:
                self.genre_values = [new if x == old else x for x in self.genre_values]
                self.genre_map[new] = self.genre_map.pop(old, [])
                self.cb_genre["values"] = self.genre_values
                if self.cur_genre.get() == old:
                    self.cur_genre.set(new)
                self.refresh_right()
        elif action.startswith("delete:"):
            name = action[7:].strip()
            if name in self.genre_values and len(self.genre_values) > 1:
                # drop mapping (patterns become unassigned; that's fine)
                self.genre_values.remove(name)
                self.genre_map.pop(name, None)
                self.cb_genre["values"] = self.genre_values
                if self.cur_genre.get() == name:
                    self.cur_genre.set(self.genre_values[0])
                self.refresh_right()

    def apply(self):
        payload = {
            "strength": self.strength,
            "order": list(self.patterns),
            "genres": {g: list(lst) for g, lst in self.genre_map.items()},
        }
        if callable(self.on_apply):
            self.on_apply(payload)




class StrongSaveWindow(tk.Toplevel):
    """
    保存工房（強）
    - ユーザーが「強セット」を複数保存（セット名＋適用先フォルダ名）
    - セットごとにジャンル箱を選び、そこから材料（pattern 行）を拾う（基本全選択）
    - 固定の強リポ＋材料を結合した「AIに渡すテキスト」をワンクリックコピー
    """
    def __init__(self, master, *, workshop_panel):
        super().__init__(master)
        self.title(f"{APP_TITLE} - 保存工房（強）")
        self.geometry("980x640")
        self.minsize(900, 560)

        self.workshop_panel = workshop_panel
        self._sets_path = os.path.join(app_dir(), STRONG_SETS_JSON)
        self._sets = self._load_sets()

        # runtime only: per-set exclude list for strong materials (not persisted)
        self._exclude_runtime = {}

        # fixed strong repo (plain text)
        self._repo_text = self._load_default_repo_text()

        self._build_ui()
        self._refresh_sets_tree(select_first=True)

        # Close behavior:
        # - When launched in "strong-only" mode (from viewer menu), closing this window should exit the app.
        # - Otherwise, just close this window.
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        try:
            self.destroy()
        finally:
            try:
                if getattr(self.master, "_strong_only_mode", False):
                    self.master.destroy()
            except Exception:
                pass

    # ---------------- persistence ----------------
    def _load_sets(self):
        data = safe_load_json(self._sets_path, default={"sets": []})
        sets = data.get("sets") if isinstance(data, dict) else None
        if not isinstance(sets, list):
            sets = []
        # normalize
        out = []
        for s in sets:
            if not isinstance(s, dict):
                continue
            name = str(s.get("name") or "").strip()
            folder_label = str(s.get("folder_label") or "").strip()
            genres = s.get("genres")
            if not isinstance(genres, list):
                genres = []
            genres = [str(g).strip() for g in genres if str(g).strip()]
            if name:
                folder_path = str(s.get("folder_path") or "").strip()
                out.append({"name": name, "folder_label": folder_label, "folder_path": folder_path, "genres": genres})
        return out

    def _save_sets(self):
        safe_save_json(self._sets_path, {"sets": self._sets})

    # ---------------- fixed repo text ----------------
    def _build_fixed_strong_repo_text(self):
        # 強は「材料をまとめた成果物を作る」だけ。汎用性不要。
        lines = [
            "ReadableFilenames",
            "AI_REPOSITORY",
            "stage: STRONG_COMPOSE",
            "",
            "目的",
            "材料として渡す pattern 行を、できるだけ少ない本数の pattern にまとめる。",
            "汎用性は不要。今回のフォルダのファイル名がきれいになれば良い。",
            "",
            "ルール",
            "- OR(|) と量指定子（+,*,{m,n}）の使用は許可。",
            "- ただし、装飾Unicodeや改行混入でコピーが壊れる出力は禁止。",
            "",
            "出力（最重要・厳守）",
            "",
            "答えは必ず「ワンクリックでコピーできる形」で提供してください。",
            "これは実運用上の必須条件です。",
            "",
            "以下を厳守してください。",
            "",
            "- 出力してよいのは「pattern: 正規表現」の行のみです。",
            "- 1行につき1つの pattern だけを書いてください。",
            "- pattern: 行は必ず1行で完結させてください（改行禁止）。",
            "- 空行、前置き、説明文、番号、コメント、装飾は一切出力しないでください。",
            "- コードブロック、引用、箇条書き、見出しは禁止です。",
            "- コピー時に余計な文字が混ざらない形で出力してください。",
            "",
            "追加（違反時の扱い）",
            "上の条件を守れない場合は、出力を行わず次の1行だけを書いて終了してください。",
            "pattern: a^",
            "",
            "材料",
            "以下に並ぶ pattern 行のみを材料として扱うこと。",
            "材料の並び順は維持すること。",
            "",
        ]
        return "\n".join(lines)

    # ---------------- UI ----------------
    def _build_ui(self):
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        pw = ttk.Panedwindow(root, orient="horizontal")
        pw.pack(fill="both", expand=True)

        # left: sets
        left = ttk.Frame(pw, padding=6)
        pw.add(left, weight=2)

        ttk.Label(left, text="強セット", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 6))

        self.tree_sets = ttk.Treeview(left, columns=("name","folder"), show="headings", selectmode="browse", height=18)
        self.tree_sets.heading("name", text="セット名")
        self.tree_sets.heading("folder", text="適用先（フォルダ名）")
        self.tree_sets.column("name", width=180, anchor="w", stretch=False)
        self.tree_sets.column("folder", width=240, anchor="w", stretch=True)
        y = ttk.Scrollbar(left, orient="vertical", command=self.tree_sets.yview)
        self.tree_sets.configure(yscrollcommand=y.set)
        self.tree_sets.pack(side="left", fill="both", expand=True)
        y.pack(side="left", fill="y", padx=(6, 0))

        self.tree_sets.bind("<<TreeviewSelect>>", self._on_set_selected)

        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="＋ 新規", command=self._new_set).pack(side="left")
        ttk.Button(btns, text="名前変更", command=self._rename_set).pack(side="left", padx=6)
        ttk.Button(btns, text="適用先変更", command=self._edit_folder_label).pack(side="left")
        ttk.Button(btns, text="削除", command=self._delete_set).pack(side="left", padx=6)

        # --- 生成 / コピー（AIに渡す）---
        gen_wrap = ttk.Frame(left)
        gen_wrap.pack(fill="x", pady=(10, 0))

        ttk.Button(
            gen_wrap,
            text="生成",
            command=self._generate_ai_payload
        ).pack(anchor="center")

        ttk.Button(
            gen_wrap,
            text="コピー",
            command=self._copy_ai_payload
        ).pack(anchor="center", pady=(6, 0))


        # right: editor + pack
        right = ttk.Frame(pw, padding=6)
        pw.add(right, weight=5)

        # Ensure the right pane is visible (avoid starting with sash collapsed)
        try:
            pw.paneconfigure(left, minsize=260)
            pw.paneconfigure(right, minsize=520)
            # place sash around 35% from left after window is realized
            self.after(50, lambda: pw.sashpos(0, int(self.winfo_width()*0.35)))
        except Exception:
            pass

        nb = ttk.Notebook(right)
        nb.pack(fill="both", expand=True)

        # tab: setup
        tab_setup = ttk.Frame(nb, padding=10)
        nb.add(tab_setup, text="セット編集")

        top = ttk.Frame(tab_setup)
        top.pack(fill="x")

        ttk.Label(top, text="選択ジャンル", font=("Segoe UI", 10, "bold")).pack(anchor="w")

        self.list_genres = tk.Listbox(tab_setup, selectmode="browse", height=10)
        g_y = ttk.Scrollbar(tab_setup, orient="vertical", command=self.list_genres.yview)
        self.list_genres.configure(yscrollcommand=g_y.set)
        self.list_genres.pack(side="left", fill="y")
        g_y.pack(side="left", fill="y", padx=(6, 0))
        self.list_genres.bind("<<ListboxSelect>>", self._on_genre_selected)

        right_col = ttk.Frame(tab_setup)
        right_col.pack(side="left", fill="both", expand=True, padx=(10, 0))

        ttk.Label(right_col, text="材料（自動生成 / 基本全選択）", font=("Segoe UI", 10, "bold")).pack(anchor="w")


        # NOTE: materials area only change:


        # use checkbox list inside the materials field (☑=AIに渡す / ☐=渡さない)


        mat_wrap = ttk.Frame(right_col)


        mat_wrap.pack(fill="both", expand=True)



        self.tree_materials = ttk.Treeview(


            mat_wrap,


            columns=("use", "pattern"),


            show="headings",


            selectmode="browse",


            height=12,


        )


        self.tree_materials.heading("use", text="渡す")


        self.tree_materials.heading("pattern", text="pattern")


        self.tree_materials.column("use", width=56, anchor="center", stretch=False)


        self.tree_materials.column("pattern", width=560, anchor="w", stretch=True)



        m_y = ttk.Scrollbar(mat_wrap, orient="vertical", command=self.tree_materials.yview)


        self.tree_materials.configure(yscrollcommand=m_y.set)



        self.tree_materials.pack(side="left", fill="both", expand=True)


        m_y.pack(side="left", fill="y", padx=(6, 0))



        self.tree_materials.bind("<Button-1>", self._on_materials_click)

        btn_row = ttk.Frame(right_col)
        btn_row.pack(fill="x", pady=(8, 0))
        # tab: ai pack

        # tab: repo edit (not saved here)
        tab_repo = ttk.Frame(nb, padding=10)
        nb.add(tab_repo, text="リポ（編集）")
        ttk.Label(tab_repo, text="強リポ（編集可・この画面では保存しません）", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(
            tab_repo,
            text="※ デフォルトの差し替えは viewer の「設定」から行います（ここは一時編集）。",
        ).pack(anchor="w", pady=(2, 6))

        btn_repo_row = ttk.Frame(tab_repo)
        btn_repo_row.pack(fill="x", pady=(0, 6))
        ttk.Button(btn_repo_row, text="デフォルトに戻す", command=self._reset_repo_to_default).pack(side="left")

        self.txt_repo = tk.Text(tab_repo, wrap="none")
        r_y = ttk.Scrollbar(tab_repo, orient="vertical", command=self.txt_repo.yview)
        self.txt_repo.configure(yscrollcommand=r_y.set)
        self.txt_repo.pack(side="left", fill="both", expand=True)
        r_y.pack(side="left", fill="y", padx=(6, 0))
        self.txt_repo.insert("1.0", self._repo_text)
        # リポ（編集）は一時編集。AIに渡す合成は「生成」ボタンで行う（自動合成しない）
        tab_pack = ttk.Frame(nb, padding=10)
        nb.add(tab_pack, text="AIに渡す")

        ttk.Label(tab_pack, text="AIに渡すテキスト（固定リポ＋材料）", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.txt_pack = tk.Text(tab_pack, wrap="none")
        p_y = ttk.Scrollbar(tab_pack, orient="vertical", command=self.txt_pack.yview)
        self.txt_pack.configure(yscrollcommand=p_y.set)
        self.txt_pack.pack(side="left", fill="both", expand=True)
        p_y.pack(side="left", fill="y", padx=(6, 0))

        row = ttk.Frame(tab_pack)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="AIに渡すテキストをコピー", command=self._copy_pack).pack(side="left")

        self._update_available_genres()
        # 初期表示では合成しない（生成ボタンで更新）
        self.txt_pack.delete('1.0', 'end')
        try:
            repo_txt = self.txt_repo.get('1.0', 'end-1c')
        except Exception:
            repo_txt = self._repo_text
        self.txt_pack.insert('1.0', repo_txt)
        self.txt_pack.insert('end', '\n')

    # ---------------- helpers ----------------
    def _load_default_repo_text(self):
        repo_path = os.path.join(app_dir(), 'strong_repo_default.txt')
        if os.path.exists(repo_path):
            try:
                with open(repo_path, 'r', encoding='utf-8') as f:
                    return f.read().rstrip('\n')
            except Exception:
                pass
        # fallback: built-in template
        txt = self._build_fixed_strong_repo_text()
        try:
            with open(repo_path, 'w', encoding='utf-8') as f:
                f.write(txt)
        except Exception:
            pass
        return txt


    def _reset_repo_to_default(self):
        """リポ（編集）をデフォルト文に戻す（合成はしない）。"""
        try:
            # 最新のデフォルト（ファイル）を読み直す
            self._repo_text = self._load_default_repo_text()
        except Exception:
            pass
        try:
            if hasattr(self, "txt_repo") and self.txt_repo.winfo_exists():
                self.txt_repo.delete("1.0", "end")
                self.txt_repo.insert("1.0", self._repo_text)
        except Exception:
            pass
        try:
            # ステータス表示があれば更新（無ければ無視）
            if hasattr(self, "var_status"):
                self.var_status.set("リポをデフォルトに戻しました（生成は未実行）")
        except Exception:
            pass

    def _get_available_genres(self):
        """材料ソースの優先順位:
        1) last_send.json の applied_current（検索モードと同じ成果物）
        2) workshop_panel.weakmid_state（画面内状態）
        3) DEFAULT_GENRES
        """
        genres = []
        try:
            st = safe_load_json(os.path.join(app_dir(), STATE_JSON), {})
            cur = st.get("applied_current") if isinstance(st, dict) else None
            gs = cur.get("genres") if isinstance(cur, dict) else None
            if isinstance(gs, dict):
                for g in gs.keys():
                    if str(g).strip():
                        genres.append(str(g).strip())
        except Exception:
            pass

        if not genres:
            st = getattr(self.workshop_panel, "weakmid_state", None)
            if isinstance(st, dict):
                gs = st.get("genres")
                if isinstance(gs, dict):
                    for g in gs.keys():
                        if str(g).strip():
                            genres.append(str(g).strip())

        if not genres:
            genres = list(DEFAULT_GENRES)
        return genres


    def _materials_from_selected_genres(self, selected_genres):
        """選択ジャンルから材料（pattern 行）を集める。
        材料ソースは applied_current.genres（優先）→ workshop_panel.weakmid_state.genres。
        """
        selected = []
        selected_set = set([normalize_genre(g) for g in (selected_genres or []) if str(g).strip()])

        def _collect_from_genres_dict(gs: dict):
            out = []
            # keep stable order: keys insertion order
            for g, lst in gs.items():
                g2 = normalize_genre(g)
                if g2 not in selected_set:
                    continue
                if isinstance(lst, list):
                    for p in lst:
                        p2 = str(p).strip()
                        if p2:
                            out.append(p2)
            return out

        # 1) from applied_current on disk
        try:
            st = safe_load_json(os.path.join(app_dir(), STATE_JSON), {})
            cur = st.get("applied_current") if isinstance(st, dict) else None
            gs = cur.get("genres") if isinstance(cur, dict) else None
            if isinstance(gs, dict):
                selected = _collect_from_genres_dict(gs)
        except Exception:
            selected = []

        # 2) fallback: weakmid_state in memory
        if not selected:
            st2 = getattr(self.workshop_panel, "weakmid_state", None)
            if isinstance(st2, dict):
                gs2 = st2.get("genres")
                if isinstance(gs2, dict):
                    selected = _collect_from_genres_dict(gs2)

        # unique, preserve order
        seen=set()
        out=[]
        for p in selected:
            if p in seen:
                continue
            seen.add(p)
            out.append(p)
        return out

    def _update_available_genres(self):
        self.list_genres.delete(0, "end")
        for g in self._get_available_genres():
            self.list_genres.insert("end", g)

    def _refresh_sets_tree(self, select_first=False):
        for iid in self.tree_sets.get_children():
            self.tree_sets.delete(iid)
        for i, s in enumerate(self._sets):
            iid = f"s{i}"
            self.tree_sets.insert("", "end", iid=iid, values=(s.get("name",""), s.get("folder_label",""),))
        if select_first and self.tree_sets.get_children():
            self.tree_sets.selection_set(self.tree_sets.get_children()[0])
            self.tree_sets.see(self.tree_sets.get_children()[0])

    def _current_set_index(self):
        sel = self.tree_sets.selection()
        if not sel:
            return None
        iid = sel[0]
        if iid.startswith("s"):
            try:
                return int(iid[1:])
            except Exception:
                return None
        return None

    def _on_set_selected(self, _evt=None):
        idx = self._current_set_index()
        if idx is None or idx < 0 or idx >= len(self._sets):
            self._genres_clear_all()
            self._set_materials_text([])
            return
        s = self._sets[idx]
        # genres selection
        all_genres = self._get_available_genres()
        chosen_list = list(s.get("genres") or [])
        self.list_genres.selection_clear(0, "end")
        target = chosen_list[0] if chosen_list else ""
        for i, g in enumerate(all_genres):
            if g == target:
                self.list_genres.selection_set(i)
                self.list_genres.see(i)
                break
        mats = self._materials_from_selected_genres([target] if target else [])
        self._set_materials_text(mats)
    def _get_excluded_set(self):
        idx = self._current_set_index()
        if idx is None:
            return set()
        s = self._exclude_runtime.get(idx)
        if isinstance(s, set):
            return s
        if isinstance(s, list):
            return set(s)
        return set()

    def _set_excluded_set(self, ex_set):
        idx = self._current_set_index()
        if idx is None:
            return
        self._exclude_runtime[idx] = set(ex_set or [])

    def _included_patterns(self, patterns):
        ex = self._get_excluded_set()
        return [p for p in (patterns or []) if p not in ex]

    def _set_materials_text(self, patterns):
        # materials field: checkbox list (runtime only)
        try:
            self.tree_materials.delete(*self.tree_materials.get_children())
        except Exception:
            return
        ex = self._get_excluded_set()
        if not patterns:
            self.tree_materials.insert("", "end", values=("", "（空です）"))
            return
        for p in patterns:
            mark = "☐" if p in ex else "☑"
            self.tree_materials.insert("", "end", values=(mark, p))

    def _on_materials_click(self, event):
        # toggle checkbox only when clicking first column
        try:
            region = self.tree_materials.identify("region", event.x, event.y)
            if region != "cell":
                return
            col = self.tree_materials.identify_column(event.x)
            if col != "#1":
                return
            row = self.tree_materials.identify_row(event.y)
            if not row:
                return
            cur = self.tree_materials.set(row, "use")
            pat = self.tree_materials.set(row, "pattern")
            if pat == "（空です）":
                return
            newv = "☐" if cur == "☑" else "☑"
            self.tree_materials.set(row, "use", newv)
            ex = self._get_excluded_set()
            if newv == "☐":
                ex.add(pat)
            else:
                ex.discard(pat)
            self._set_excluded_set(ex)
        except Exception:
            return



    def _update_pack_text(self, patterns=None):
        if patterns is None:
            # current selection
            idx = self._current_set_index()
            if idx is None:
                patterns = []
            else:
                s = self._sets[idx]
                patterns = self._materials_from_selected_genres(s.get("genres") or [])
        patterns = self._included_patterns(patterns)
        self.txt_pack.delete("1.0", "end")
        repo_txt = self._repo_text
        if hasattr(self, 'txt_repo'):
            try:
                repo_txt = self.txt_repo.get('1.0', 'end-1c')
            except Exception:
                repo_txt = self._repo_text
        self.txt_pack.insert('1.0', repo_txt)
        self.txt_pack.insert("end", "\n")
        for p in patterns:
            self.txt_pack.insert("end", f"pattern: {p}\n")
    def _generate_ai_payload(self):
        """材料（チェック状態含む）からAIに渡すテキストを再生成する（明示トリガ）"""
        try:
            chosen = self._commit_genre_selection_to_set()
            mats = self._materials_from_selected_genres(chosen)
            self._set_materials_text(mats)
            self._update_pack_text(mats)
        except Exception:
            pass

    def _copy_ai_payload(self):
        """AIに渡すタブに表示されている内容をクリップボードにコピー"""
        try:
            text = self.txt_pack.get("1.0", "end").rstrip("\n")
            if not text:
                return
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update_idletasks()
        except Exception:
            pass


    def _copy_pack(self):
        try:
            txt = self.txt_pack.get("1.0", "end-1c")
            self.clipboard_clear()
            self.clipboard_append(txt)
            self.update_idletasks()
        except Exception:
            pass

    # ---------------- set operations ----------------
    def _prompt_text(self, title, prompt, initial=""):
        try:
            return simpledialog.askstring(title, prompt, initialvalue=initial, parent=self)
        except Exception:
            return None

    def _new_set(self):
        # STEP 1: choose target folder (path)
        try:
            from tkinter import filedialog
            folder_path = filedialog.askdirectory(parent=self, title="適用するフォルダーを選択してください")
        except Exception:
            folder_path = ""
        if not folder_path:
            return
        folder_label = os.path.basename(folder_path.rstrip("/\\"))
        # STEP 2: set name (default: folder name)
        name = self._prompt_text(APP_TITLE, "セット名を入力してください（フォルダー名を含めてください）。", folder_label)
        if not name:
            return
        name = name.strip()
        # default genre: first available (single selection)
        genres_all = self._get_available_genres()
        genres = [genres_all[0]] if genres_all else []
        self._sets.append({
            "name": name,
            "folder_label": folder_label,
            "folder_path": folder_path,
            "genres": list(genres),
        })
        self._save_sets()
        self._refresh_sets_tree()
        # select newly added
        idx = len(self._sets) - 1
        iid = f"s{idx}"
        self.tree_sets.selection_set(iid)
        self.tree_sets.see(iid)
        self._sync_title()
        self._on_set_selected()

    def _rename_set(self):
        idx = self._current_set_index()
        if idx is None:
            return
        cur = self._sets[idx].get("name","")
        name = self._prompt_text(APP_TITLE, "セット名を変更してください。", cur)
        if not name:
            return
        self._sets[idx]["name"] = name.strip()
        self._save_sets()
        # update tree label? Tree only shows folder; keep name in iid order; show name in tooltip not supported.
        # For visibility, update window title to include selected name.
        self._sync_title()

    def _edit_folder_label(self):
        idx = self._current_set_index()
        if idx is None:
            return
        try:
            from tkinter import filedialog
            folder_path = filedialog.askdirectory(parent=self, title='適用するフォルダーを選択してください')
        except Exception:
            folder_path = ''
        if not folder_path:
            return
        folder_label = os.path.basename(folder_path.rstrip('/\\'))
        # allow user to tweak label (name only)
        cur = folder_label
        v = self._prompt_text(APP_TITLE, '適用先フォルダ名（名前だけ）を確認/変更してください。', cur)
        if v is None:
            return
        folder_label = str(v).strip()
        self._sets[idx]['folder_label'] = folder_label
        self._sets[idx]['folder_path'] = folder_path
        self._save_sets()
        self._refresh_sets_tree()
        iid = f's{idx}'
        self.tree_sets.selection_set(iid)
        self.tree_sets.see(iid)
        self._sync_title()

    def _delete_set(self):
        idx = self._current_set_index()
        if idx is None:
            return
        try:
            s = self._sets[idx]
            name = s.get("name","")
        except Exception:
            name = ""
        if not messagebox.askyesno(APP_TITLE, f"セット「{name}」を削除しますか？"):
            return
        del self._sets[idx]
        self._save_sets()
        self._refresh_sets_tree(select_first=True)
        self._sync_title()

    def _on_genre_selected(self, _evt=None):
        # 選択が変わったら「材料」表示だけ更新（保存や合成はしない）
        self._refresh_materials_from_genre_selection()

    def _genres_select_all(self):
        # legacy no-op in single selection mode: select first item if any
        if self.list_genres.size() > 0:
            self.list_genres.selection_clear(0, "end")
            self.list_genres.selection_set(0)
            self.list_genres.see(0)
            self._refresh_materials_from_genre_selection()

    def _genres_clear_all(self):
        self.list_genres.selection_clear(0, "end")
        self._refresh_materials_from_genre_selection()

    def _refresh_materials_from_genre_selection(self):
        """ジャンル選択に合わせて材料表示だけ更新（保存・合成はしない）"""
        try:
            sels = list(self.list_genres.curselection())
            all_g = self._get_available_genres()
            chosen = [all_g[sels[0]]] if (sels and 0 <= sels[0] < len(all_g)) else []
            mats = self._materials_from_selected_genres(chosen)
            self._set_materials_text(mats)
        except Exception:
            pass

    def _commit_genre_selection_to_set(self):
        """現在のジャンル選択をセットへ保存して返す（生成ボタン用）"""
        idx = self._current_set_index()
        if idx is None:
            return []
        sels = list(self.list_genres.curselection())
        all_g = self._get_available_genres()
        chosen = [all_g[sels[0]]] if (sels and 0 <= sels[0] < len(all_g)) else []
        self._sets[idx]["genres"] = chosen
        self._save_sets()
        self._sync_title()
        return chosen

    def _sync_title(self):
        idx = self._current_set_index()
        if idx is None:
            self.title(f"{APP_TITLE} - 保存工房（強）")
            return
        s = self._sets[idx]
        name = s.get("name","")
        folder_label = s.get("folder_label","")
        self.title(f"{APP_TITLE} - 保存工房（強）  [{name}]  → {folder_label}")



def main():
    import sys

    # args
    args = list(sys.argv[1:])
    single = ("--single" in args)
    hidden = ("--hidden" in args)
    open_strong = any(a in ("--open-strong-save", "--strong-save", "--strong") for a in args)

    lock_path = os.path.join(app_dir(), LOCK_FILE)
    cursor_path = os.path.join(app_dir(), CURSOR_JSON)
    inbox_path = os.path.join(app_dir(), IPC_INBOX)

    # single-instance: if already running, ask it to do what we need and exit
    if single and _lock_running(lock_path):
        if open_strong:
            _append_inbox({"cmd": "OPEN_STRONG_SAVE"})
            _append_inbox({"cmd": "SHOW"})
        else:
            _append_inbox({"cmd": "SHOW"})
        return
    # become the instance (best-effort)
    _write_lock(lock_path)

    app = WorkshopApp()

    # hidden start (viewer uses this for background boot)
    if hidden:
        try:
            app.withdraw()
        except Exception:
            pass

    # Strong-only mode: hide main window and open only Strong Save window
    if open_strong:
        app._strong_only_mode = True
        try:
            app.withdraw()
        except Exception:
            pass

        def _open_strong():
            try:
                w = StrongSaveWindow(app, workshop_panel=app.panel)
                try:
                    w.lift()
                    w.focus_force()
                except Exception:
                    pass
            except Exception:
                # If Strong window fails to open, restore the main window so the user isn't left with nothing.
                try:
                    app.deiconify()
                except Exception:
                    pass

        try:
            app.after(50, _open_strong)
        except Exception:
            pass

    # IPC: watch inbox for SHOW (bring to front)
    def _poll_inbox():
        try:
            if os.path.exists(inbox_path):
                pos = _load_cursor(cursor_path)
                # If inbox was rotated/truncated, cursor may point past EOF; clamp it.
                try:
                    sz = os.path.getsize(inbox_path)
                    if pos > sz:
                        pos = 0
                except Exception:
                    pass
                with open(inbox_path, "r", encoding="utf-8") as f:
                    try:
                        f.seek(pos)
                    except Exception:
                        f.seek(0)
                    while True:
                        line = f.readline()
                        if not line:
                            break
                        s = line.strip()
                        if not s:
                            continue
                        try:
                            msg = json.loads(s)
                        except Exception:
                            continue
                        if isinstance(msg, dict):
                            cmd = msg.get("cmd")
                            if cmd == "SHOW":
                                try:
                                    app.deiconify()
                                except Exception:
                                    pass
                                try:
                                    app.lift()
                                    app.focus_force()
                                except Exception:
                                    pass

                            elif cmd == "OPEN_STRONG_SAVE":
                                try:
                                    # 既存の強ウインドウがあれば前面へ
                                    win = getattr(app, "_strong_save_win", None)
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
                                        # ない場合は新規で開く
                                        try:
                                            app.deiconify()
                                            app.iconify()
                                        except Exception:
                                            pass
                                        win = StrongSaveWindow(app, workshop_panel=app.panel)
                                        app._strong_save_win = win
                                        try:
                                            win.deiconify()
                                            win.lift()
                                            win.focus_force()
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                    try:
                        pos2 = f.tell()
                    except Exception:
                        pos2 = 0
                try:
                    global _LAST_CURSOR_POS
                    _LAST_CURSOR_POS = int(pos2)
                except Exception:
                    pass
        except Exception:
            pass

        # lock file is not refreshed periodically (write-once)

        try:
            app.after(600, _poll_inbox)
        except Exception:
            pass

    try:
        app.after(600, _poll_inbox)
    except Exception:
        pass
# ensure lock cleaned up on exit
def _on_close():
    try:
        # save cursor once on exit (avoid constant writes)
        try:
            _save_cursor(cursor_path, _LAST_CURSOR_POS if _LAST_CURSOR_POS is not None else 0)
        except Exception:
            pass
    except Exception:
        pass

    _remove_lock(lock_path)
    try:
        app.destroy()
    except Exception:
        pass

    try:
        app.protocol("WM_DELETE_WINDOW", _on_close)
    except Exception:
        pass

    try:
        app.mainloop()
    finally:
        _remove_lock(lock_path)

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import os, traceback, datetime
        try:
            p = os.path.join(app_dir(), "_workshop_crash.log")
        except Exception:
            p = "_workshop_crash.log"
        try:
            with open(p, "a", encoding="utf-8") as f:
                f.write("\n" + "="*70 + "\n")
                f.write("CRASH: " + datetime.datetime.now().isoformat() + "\n")
                f.write(traceback.format_exc() + "\n")
        except Exception:
            pass
        # pythonw 対応: tkinter を使わずに Windows のメッセージボックスを出す
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                "ReadableFilenames_workshop.py が起動時に落ちました。\n\n"
                "詳細ログ: _workshop_crash.log\n"
                "（作業フォルダに出ます）",
                "ReadableFilenames - workshop crash",
                0x10
            )
        except Exception:
            pass

