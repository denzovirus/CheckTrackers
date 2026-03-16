from __future__ import annotations

import json
import os
import random
import re
import sys
import time
import threading
import traceback
import urllib.parse
import webbrowser
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, simpledialog, ttk

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from win10toast import ToastNotifier  # type: ignore[import]
except Exception:  # pragma: no cover - dépendance optionnelle
    ToastNotifier = None

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover - dépendances optionnelles
    pystray = None
    Image = None
    ImageDraw = None

from typing import TYPE_CHECKING, Optional, Tuple, cast, Any

if TYPE_CHECKING:
    # Import types only for type checking to avoid runtime import errors
    from win10toast import ToastNotifier as _ToastNotifier  # type: ignore
    import pystray as _pystray  # type: ignore
    from PIL import Image as _PILImage  # type: ignore


CLOSED_MARKER_PATTERN = r"inscriptions?\s+ferm"


class _Tooltip:
    """Infobulle légère qui apparaît après un délai quand la souris reste sur un widget."""
    _DELAY_MS = 600
    _BG = "#fffbe6"
    _FG = "#1f2933"

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self._widget = widget
        self._text = text
        self._tip: tk.Toplevel | None = None
        self._job: str | None = None
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")

    def _on_enter(self, _event: tk.Event) -> None:  # type: ignore[type-arg]
        self._job = self._widget.after(self._DELAY_MS, self._show)

    def _on_leave(self, _event: tk.Event) -> None:  # type: ignore[type-arg]
        if self._job:
            self._widget.after_cancel(self._job)
            self._job = None
        self._hide()

    def _show(self) -> None:
        if self._tip:
            return
        x = self._widget.winfo_rootx() + self._widget.winfo_width() // 2
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._tip = tk.Toplevel(self._widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(
            self._tip, text=self._text, bg=self._BG, fg=self._FG,
            font=("Segoe UI Variable", 9), relief="solid", borderwidth=1,
            padx=8, pady=4, wraplength=260,
        )
        lbl.pack()

    def _hide(self) -> None:
        if self._tip:
            self._tip.destroy()
            self._tip = None


APP_VERSION = "0.3"
GITHUB_REPO = "denzovirus/CheckTrackers"

# Déterminer le répertoire de base (pour l'exe PyInstaller ou le script Python)
def _resolve_base_dir() -> Path:
    if getattr(sys, 'frozen', False):
        # L'app est exécutée comme exe PyInstaller — sys.executable est fiable
        candidate = Path(sys.executable).parent.absolute()
    else:
        candidate = Path(__file__).parent.absolute()

    # Vérifier que le répertoire existe et est accessible en écriture
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        test_file = candidate / ".write_test"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        return candidate
    except Exception:
        pass

    # Fallback vers %APPDATA%\CheckTracker (toujours accessible en écriture)
    appdata = Path(os.environ.get("APPDATA", Path.home())) / "CheckTracker"
    appdata.mkdir(parents=True, exist_ok=True)
    return appdata

BASE_DIR = _resolve_base_dir()
CONFIG_PATH = BASE_DIR / "check_tracker.json"
HISTORY_PATH = BASE_DIR / "check_tracker_history.json"
LOG_PATH = BASE_DIR / "check_tracker.log"

BUILTIN_SITES = [
    {"key": "lacale", "name": "la-cale.space", "url": "https://la-cale.space/register"},
    {"key": "abn", "name": "abn.lol", "url": "https://abn.lol/Home/Register"},
    {"key": "tctg", "name": "tctg.pm", "url": "https://tctg.pm/signup.php"},
    {"key": "hdf", "name": "hdf.world", "url": "https://hdf.world/register.php"},
]

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
}


class LacaleWatcherApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Surveillance inscriptions")
        # Permettre le redimensionnement de la fenêtre principale
        self.root.resizable(True, True)
        self.root.minsize(520, 320)

        self.running = False
        self.interval_minutes = 5
        self.open_browser_on_open = True
        self.play_sound_on_open = True
        self.background_on_close = False
        self.autostart_with_windows = False
        self.log_every_check = False  # Enregistre l'historique à chaque vérification (même si le statut n'a pas changé)
        self.alert_on_error = False

        self.last_open_time: dict[str, float] = {}
        self.last_close_time: dict[str, float] = {}
        self.last_latency_ms: dict[str, float] = {}

        # Sites (natifs + personnalisés)
        self.sites: list[dict] = []
        # Par défaut, on affiche les sites natifs (la-cale, abn, tctg, hdf).
        self.removed_builtin_sites: list[str] = []
        self._builtin_widgets: dict[str, list[tk.Widget]] = {}
        self._custom_site_rows: dict[str, list[tk.Widget]] = {}
        self.site_control_buttons: list[tk.Widget] = []
        self._custom_site_counter = 0

        self.last_state: dict[str, str | None] = {}
        self.history: list[dict] = []  # [{"datetime", "site", "url", "status"}]
        self._load_history()
        self.thread: threading.Thread | None = None

        self.toaster: Optional["_ToastNotifier"] = (
            ToastNotifier() if ToastNotifier is not None else None
        )
        self.tray_icon: Optional[Any] = None

        # Session requests avec retry pour 429/503
        self._requests_session = requests.Session()
        retry = Retry(
            total=2,
            backoff_factor=1,
            status_forcelist=[429, 503],
            allowed_methods=frozenset(["GET", "POST"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self._requests_session.mount("https://", adapter)
        self._requests_session.mount("http://", adapter)

        self._load_config()
        # Si aucune config existante, on ajoute les sites natifs par défaut.
        if not self.sites:
            self.sites = [
                {
                    "key": s["key"],
                    "name": s["name"],
                    "url": s["url"],
                    "enabled": True,
                    "builtin": True,
                    "status": "Inconnu",
                }
                for s in BUILTIN_SITES
            ]
        self._rebuild_sites_list()
        self._sites_visible = False
        self._build_ui()
        self._build_custom_site_rows()
        self._apply_config_to_ui()

        # Crée le fichier de config par défaut (sites natifs) si nécessaire.
        if not CONFIG_PATH.exists():
            self._save_config()

        self._set_site_list_visible(self._sites_visible)
        self._check_for_update()

    def _build_ui(self) -> None:
        # Style global + moderne (inspiré de Windows 11)
        bg_color = "#f3f4f6"
        panel_color = "#ffffff"
        accent_color = "#0078D4"  # Windows 11 accent
        accent_hover = "#106ebe"
        self._accent_color = accent_color
        self._accent_hover = accent_hover
        text_color = "#1f2933"
        muted_text = "#4b5563"
        self.root.configure(background=bg_color)

        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except Exception:
            try:
                style.theme_use("clam")
            except Exception:
                pass

        style.configure("TFrame", background=bg_color)
        style.configure("Main.TFrame", background=bg_color)
        style.configure(
            "Panel.TFrame",
            background=panel_color,
            relief="flat",
            borderwidth=0,
            padding=16,
        )
        style.configure("Card.TFrame", background=panel_color, relief="flat", borderwidth=0, padding=12)

        style.configure(
            "TLabel",
            background=panel_color,
            foreground=text_color,
            font=("Segoe UI Variable", 9),
        )
        style.configure(
            "Header.TLabel",
            background=bg_color,
            foreground=accent_color,
            font=("Segoe UI Variable", 13, "bold"),
        )
        style.configure(
            "SubHeader.TLabel",
            background=panel_color,
            foreground=muted_text,
            font=("Segoe UI Variable", 10, "bold"),
        )
        style.configure(
            "Section.TLabelframe",
            background=panel_color,
            foreground=muted_text,
            font=("Segoe UI Variable", 10, "bold"),
            borderwidth=1,
            relief="solid",
            padding=(12, 10),
        )
        style.configure(
            "Section.TLabelframe.Label",
            background=panel_color,
            foreground=muted_text,
        )
        style.configure(
            "TButton",
            padding=(10, 7),
            font=("Segoe UI Variable", 9),
            borderwidth=0,
            relief="flat",
            foreground=text_color,
            background=panel_color,
            disabledforeground="#6b7280",
        )
        style.map(
            "TButton",
            background=[("active", "#f0f4ff"), ("disabled", "#f3f4f6")],
            foreground=[("disabled", "#9ca3af")],
        )
        style.configure(
            "Primary.TButton",
            foreground="#ffffff",
            background=accent_color,
            font=("Segoe UI Variable", 10, "bold"),
            padding=(12, 8),
            borderwidth=0,
            relief="flat",
            disabledforeground="#6b7280",
        )
        style.map(
            "Primary.TButton",
            background=[("!disabled", accent_color), ("active", accent_hover), ("disabled", "#d1d5db")],
            foreground=[("!disabled", "#ffffff"), ("disabled", "#9ca3af")],
        )
        style.configure(
            "TCheckbutton",
            background=panel_color,
            foreground=text_color,
            font=("Segoe UI Variable", 9),
        )
        style.configure(
            "TEntry",
            fieldbackground=panel_color,
            background=panel_color,
            foreground=text_color,
        )
        style.configure("TSpinbox", fieldbackground=panel_color, background=panel_color, foreground=text_color)
        style.configure(
            "BigStatus.TLabel",
            background=panel_color,
            foreground=text_color,
            font=("Segoe UI Variable", 18, "bold"),
        )

        main_frame = ttk.Frame(self.root, padding=15, style="Panel.TFrame")
        main_frame.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        # Mise en page responsive
        for col in range(5):
            main_frame.columnconfigure(col, weight=1 if col in (1, 2) else 0)

        self._status_frame = ttk.Frame(main_frame, style="Card.TFrame")
        status_frame = self._status_frame
        status_frame.grid(row=0, column=0, columnspan=5, padx=10, pady=(0, 10), sticky="nsew")
        status_frame.columnconfigure(1, weight=1)

        self.global_status_led = ttk.Label(
            status_frame, text="●", style="BigStatus.TLabel"
        )
        self.global_status_led.grid(row=0, column=0, padx=(10, 5), pady=(10, 10), sticky="w")

        self.global_status_label = ttk.Label(
            status_frame, text="État global : Inconnu", style="Header.TLabel"
        )
        self.global_status_label.grid(row=0, column=1, columnspan=3, padx=0, pady=(10, 0), sticky="w")

        beta_badge = tk.Label(
            status_frame, text="β 0.3",
            bg="#ede9fe", fg="#7c3aed",
            font=("Segoe UI Variable", 7, "bold"),
            padx=6, pady=2,
        )
        beta_badge.grid(row=1, column=4, padx=(0, 10), pady=(0, 0), sticky="e")

        counters_frame = ttk.Frame(status_frame, style="Card.TFrame")
        counters_frame.grid(row=1, column=1, columnspan=3, padx=0, pady=(0, 10), sticky="w")
        self.lbl_cnt_open = tk.Label(counters_frame, text="● OUVERT: 0", foreground="#9ca3af", bg=panel_color, font=("Segoe UI Variable", 9, "bold"))
        self.lbl_cnt_open.pack(side="left", padx=(0, 14))
        self.lbl_cnt_close = tk.Label(counters_frame, text="● FERMÉ: 0", foreground="#9ca3af", bg=panel_color, font=("Segoe UI Variable", 9, "bold"))
        self.lbl_cnt_close.pack(side="left", padx=(0, 14))
        self.lbl_cnt_err = tk.Label(counters_frame, text="● ERREUR: 0", foreground="#9ca3af", bg=panel_color, font=("Segoe UI Variable", 9, "bold"))
        self.lbl_cnt_err.pack(side="left")

        self.last_check_label = ttk.Label(status_frame, text="Dernière vérification : -")
        self.last_check_label.grid(row=2, column=0, columnspan=4, padx=10, pady=(0, 2), sticky="w")

        self.last_activity_label = ttk.Label(
            status_frame, text="Dernière ouverture/fermeture : -"
        )
        self.last_activity_label.grid(row=3, column=0, columnspan=4, padx=10, pady=(0, 10), sticky="w")

        self.toggle_sites_btn = ttk.Button(
            status_frame,
            text="Masquer sites" if self._sites_visible else "Gérer sites",
            command=self._toggle_site_list_visibility,
            style="TButton",
        )
        self.toggle_sites_btn.grid(row=0, column=4, padx=(4, 10), pady=(10, 4), sticky="e")

        next_frame = ttk.Frame(main_frame, style="Panel.TFrame")
        next_frame.grid(row=1, column=0, columnspan=5, padx=10, pady=(0, 6), sticky="ew")
        next_frame.columnconfigure(1, weight=1)
        self.next_check_label = ttk.Label(next_frame, text="Prochaine vérif : -")
        self.next_check_label.grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.next_check_progress = ttk.Progressbar(next_frame, orient="horizontal", mode="determinate", length=160)
        self.next_check_progress.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        self.next_check_progress["value"] = 0

        ttk.Separator(main_frame, orient="horizontal").grid(
            row=8, column=0, columnspan=5, padx=10, pady=(4, 0), sticky="ew"
        )

        interval_label = ttk.Label(main_frame, text="Intervalle :")
        interval_label.grid(row=9, column=0, padx=(10, 4), pady=8, sticky="e")

        self.interval_var = tk.IntVar(value=self.interval_minutes)
        self.interval_spin = ttk.Spinbox(
            main_frame,
            from_=5,
            to=60,
            textvariable=self.interval_var,
            width=5,
            command=self._on_interval_change,
        )
        self.interval_spin.grid(row=9, column=1, padx=(0, 8), pady=8, sticky="w")

        self.start_button = tk.Button(
            main_frame,
            text="Démarrer",
            command=self.start_watching,
            bg=accent_color,
            fg="#ffffff",
            font=("Segoe UI Variable", 10, "bold"),
            padx=12,
            pady=8,
            bd=0,
            relief="flat",
            activebackground=accent_hover,
            activeforeground="#ffffff",
            disabledforeground="#9ca3af",
            cursor="hand2",
        )
        self.start_button.grid(row=9, column=2, padx=3, pady=8)

        self.stop_button = tk.Button(
            main_frame,
            text="Arrêter",
            command=self.stop_watching,
            state=tk.DISABLED,
            bg="#d1d5db",
            fg="#9ca3af",
            font=("Segoe UI Variable", 10, "bold"),
            padx=12,
            pady=8,
            bd=0,
            relief="flat",
            activebackground=accent_hover,
            activeforeground="#ffffff",
            disabledforeground="#9ca3af",
            cursor="hand2",
        )
        self.stop_button.grid(row=9, column=3, padx=3, pady=8)

        self.check_now_button = ttk.Button(
            main_frame,
            text="↻ Vérifier maintenant",
            command=self._check_now,
        )
        self.check_now_button.grid(row=9, column=4, padx=(6, 10), pady=8, sticky="w")

        # Options notifications
        self.var_open_browser = tk.BooleanVar(value=self.open_browser_on_open)
        self.var_play_sound = tk.BooleanVar(value=self.play_sound_on_open)
        self.var_background_on_close = tk.BooleanVar(value=self.background_on_close)
        self.var_autostart = tk.BooleanVar(value=self.autostart_with_windows)
        self.var_log_every_check = tk.BooleanVar(value=self.log_every_check)
        self.var_alert_on_error = tk.BooleanVar(value=self.alert_on_error)

        options_lf = ttk.LabelFrame(main_frame, text="Options", style="Section.TLabelframe")
        options_lf.grid(row=10, column=0, columnspan=5, padx=10, pady=(4, 6), sticky="ew")
        options_lf.columnconfigure(0, weight=1)
        options_lf.columnconfigure(1, weight=1)
        ttk.Checkbutton(options_lf, text="Ouvrir le navigateur quand c'est ouvert", variable=self.var_open_browser, command=self._on_options_changed).grid(row=0, column=0, padx=10, pady=(8, 4), sticky="w")
        ttk.Checkbutton(options_lf, text="Jouer un son quand c'est ouvert", variable=self.var_play_sound, command=self._on_options_changed).grid(row=0, column=1, padx=10, pady=(8, 4), sticky="w")
        ttk.Checkbutton(options_lf, text="Lancer automatiquement avec Windows", variable=self.var_autostart, command=self._on_options_changed).grid(row=1, column=0, padx=10, pady=4, sticky="w")
        ttk.Checkbutton(options_lf, text="Garder la surveillance quand je ferme la fenêtre", variable=self.var_background_on_close, command=self._on_options_changed).grid(row=1, column=1, padx=10, pady=4, sticky="w")
        ttk.Checkbutton(options_lf, text="Enregistrer chaque vérification", variable=self.var_log_every_check, command=self._on_options_changed).grid(row=2, column=0, padx=10, pady=(4, 8), sticky="w")
        ttk.Checkbutton(options_lf, text="Alerter aussi sur erreur", variable=self.var_alert_on_error, command=self._on_options_changed).grid(row=2, column=1, padx=10, pady=(4, 8), sticky="w")

        # Sites surveillés (natifs + personnalisés)
        self._panel_color = panel_color
        self._bg_color = bg_color
        self.sites_frame = ttk.LabelFrame(main_frame, text="Sites surveillés", style="Section.TLabelframe")
        self.sites_frame.grid(row=13, column=0, columnspan=4, padx=10, pady=(5, 5), sticky="nsew")
        self.sites_frame.columnconfigure(0, weight=1)

        self.custom_sites_rows_container = tk.Frame(self.sites_frame, bg="#f3f4f6")
        self.custom_sites_rows_container.grid(row=0, column=0, padx=6, pady=6, sticky="nsew")
        self.custom_sites_rows_container.columnconfigure(0, weight=1)

        add_site_btn = ttk.Button(self.sites_frame, text="+ Ajouter un site", command=self._on_add_custom_site)
        add_site_btn.grid(row=1, column=0, padx=8, pady=(2, 8), sticky="w")

        # Zone logs + actions
        logs_frame = ttk.Frame(main_frame)
        logs_frame.grid(row=14, column=0, columnspan=4, padx=10, pady=(5, 5), sticky="ew")
        logs_frame.columnconfigure(0, weight=1)

        clear_logs_btn = ttk.Button(logs_frame, text="Vider les logs", command=self._clear_logs)
        clear_logs_btn.grid(row=0, column=1, padx=(5, 0), sticky="e")

        history_btn = ttk.Button(logs_frame, text="Voir l'historique", command=self._open_history_window)
        history_btn.grid(row=0, column=2, padx=(5, 0), sticky="e")



        self.log_box = scrolledtext.ScrolledText(
            main_frame,
            width=60,
            height=8,
            state=tk.DISABLED,
            bg="#1e1e1e",
            fg="#d4d4d4",
            font=("Consolas", 9),
            insertbackground="#d4d4d4",
            selectbackground="#264f78",
            selectforeground="#ffffff",
            bd=0,
            highlightthickness=1,
            highlightbackground="#374151",
        )
        self.log_box.tag_configure("open", foreground="#4ade80")
        self.log_box.tag_configure("close", foreground="#f87171")
        self.log_box.tag_configure("error", foreground="#fbbf24")
        self.log_box.grid(row=15, column=0, columnspan=4, padx=10, pady=(5, 10), sticky="nsew")
        main_frame.rowconfigure(15, weight=1)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _check_for_update(self) -> None:
        """Vérifie si une mise à jour est disponible sur GitHub (thread background)."""
        def _fetch() -> None:
            try:
                api_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
                resp = requests.get(
                    api_url, timeout=5,
                    headers={"User-Agent": f"CheckTracker/{APP_VERSION}"}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    tag = data.get("tag_name", "").lstrip("v")
                    html_url = data.get("html_url", "")
                    if tag and self._is_newer(tag, APP_VERSION):
                        self.root.after(0, lambda: self._show_update_badge(tag, html_url))
                        return
            except Exception:
                pass
            # À jour ou erreur réseau → badge vert
            self.root.after(0, self._show_up_to_date_badge)
        threading.Thread(target=_fetch, daemon=True).start()

    def _is_newer(self, remote: str, local: str) -> bool:
        def parse(v: str) -> tuple:
            try:
                return tuple(int(x) for x in v.split("."))
            except Exception:
                return (0,)
        return parse(remote) > parse(local)

    def _show_up_to_date_badge(self) -> None:
        """Affiche un badge vert 'à jour' dans la barre de statut."""
        if hasattr(self, "_update_badge"):
            return
        self._update_badge = tk.Label(
            self._status_frame,
            text="✓ à jour",
            bg="#dcfce7", fg="#15803d",
            font=("Segoe UI Variable", 7, "bold"),
            padx=6, pady=2,
        )
        self._update_badge.grid(row=2, column=4, padx=(0, 10), pady=(0, 4), sticky="e")

    def _show_update_badge(self, latest_version: str, url: str) -> None:
        """Affiche un badge cliquable 'mise à jour disponible' dans la barre de statut."""
        if hasattr(self, "_update_badge"):
            return
        self._update_badge = tk.Label(
            self._status_frame,
            text=f"⬆ v{latest_version} dispo",
            bg="#fef3c7", fg="#92400e",
            font=("Segoe UI Variable", 7, "bold"),
            padx=6, pady=2,
            cursor="hand2",
        )
        self._update_badge.grid(row=2, column=4, padx=(0, 10), pady=(0, 4), sticky="e")
        self._update_badge.bind("<Button-1>", lambda _: webbrowser.open(url))

    def _append_log(self, text: str) -> None:
        if not hasattr(self, "log_lines"):
            self.log_lines: list[str] = []
        self.log_lines.append(text)
        # on garde seulement les 500 dernières lignes en mémoire
        self.log_lines = self.log_lines[-500:]

        self.log_box.configure(state=tk.NORMAL)
        self.log_box.delete("1.0", tk.END)
        for i, line in enumerate(self.log_lines, start=1):
            upper = line.upper()
            tag = (
                "open" if "OUVERT" in upper
                else "close" if ("FERMÉ" in upper or "FERME" in upper)
                else "error" if "ERREUR" in upper
                else None
            )
            self.log_box.insert(tk.END, line + "\n")
            if tag:
                self.log_box.tag_add(tag, f"{i}.0", f"{i}.end")
        self.log_box.see(tk.END)
        self.log_box.configure(state=tk.DISABLED)

    def _log_exception(self, site: str, url: str | None, exc: BaseException) -> None:
        """Log exception details (traceback + info) into a persistent log file."""
        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            details = traceback.format_exc()
            url_str = url or ""
            line = f"[{ts}] {site} ({url_str}) - {exc}\n{details}\n"
            with LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            # Ne pas interrompre l'appli pour un problème de log
            pass

    def _clear_logs(self) -> None:
        self.log_lines = []
        self.log_box.configure(state=tk.NORMAL)
        self.log_box.delete("1.0", tk.END)
        self.log_box.configure(state=tk.DISABLED)

    def _open_history_window(self) -> None:
        if not self.history:
            messagebox.showinfo("Historique", "Aucun historique pour le moment.")
            return

        rows: list[dict] = list(self.history)

        def _refresh_table(tree: ttk.Treeview, data: list[dict]) -> None:
            tree.delete(*tree.get_children())
            for row in reversed(data):  # plus récent en haut
                tree.insert(
                    "", "end",
                    values=(row.get("datetime", ""), row.get("site", ""), row.get("status", "")),
                )

        def _sort_tree(tree: ttk.Treeview, col: str, reverse: bool) -> None:
            items = [(tree.set(k, col), k) for k in tree.get_children("")]
            items.sort(key=lambda t: t[0], reverse=reverse)
            for index, (_, k) in enumerate(items):
                tree.move(k, "", index)
            tree.heading(col, command=lambda: _sort_tree(tree, col, not reverse))

        def _get_sites() -> list[str]:
            return ["Tous"] + sorted({r.get("site", "") for r in rows if r.get("site")})

        def _filtered(data: list[dict]) -> list[dict]:
            sel = site_filter_var.get()
            return data if not sel or sel == "Tous" else [r for r in data if r.get("site") == sel]

        win = tk.Toplevel(self.root)
        win.title("Historique des états")
        win.geometry("560x420")

        ctrl_frame = ttk.Frame(win)
        ctrl_frame.pack(fill="x", padx=8, pady=6)
        site_filter_var = tk.StringVar(value="Tous")
        site_filter = ttk.Combobox(ctrl_frame, values=_get_sites(), textvariable=site_filter_var, state="readonly", width=22)
        site_filter.pack(side="left")

        def _refresh_all() -> None:
            nonlocal rows
            rows = list(self.history)
            site_filter.configure(values=_get_sites())
            _refresh_table(tree, _filtered(rows))

        ttk.Button(ctrl_frame, text="Rafraîchir", command=_refresh_all).pack(side="right")

        tree = ttk.Treeview(win, columns=("datetime", "site", "status"), show="headings")
        for col, w in (("datetime", 160), ("site", 150), ("status", 150)):
            tree.heading(col, text=col.capitalize(), command=lambda c=col: _sort_tree(tree, c, False))
            tree.column(col, anchor="w", width=w)
        scrollbar = ttk.Scrollbar(win, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(fill=tk.BOTH, expand=True, padx=(8, 0), pady=(0, 8))
        _refresh_table(tree, rows)
        site_filter.bind("<<ComboboxSelected>>", lambda _: _refresh_all())



    def _rebuild_sites_list(self) -> None:
        # Reconstruit la liste complète (builtin + custom) à partir de la config
        # et évite les doublons (même clé) qui peuvent survenir lors de l'ajout.
        builtins = [s for s in self.sites if s.get("builtin") is True]
        customs = [s for s in self.sites if s.get("builtin") is not True]

        seen_keys: set[str] = set()
        ordered: list[dict] = []
        for site in builtins + customs:
            key = site.get("key")
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            ordered.append(site)
        self.sites = ordered

    def _refresh_custom_sites_listbox(self) -> None:
        # Met à jour l'affichage des sites personnalisés dans la zone principale
        self._build_custom_site_rows()

    def _apply_site_visibility_to_ui(self) -> None:
        for widgets in self._custom_site_rows.values():
            try:
                # widgets[0] est le row_frame
                widgets[0].grid() if self._sites_visible else widgets[0].grid_remove()
            except Exception:
                pass

    def _hide_site_list_ui(self) -> None:
        # Cache la section de liste des sites (natifs + personnalisés)
        if hasattr(self, "sites_frame"):
            try:
                self.sites_frame.grid_remove()
            except Exception:
                pass



    def _set_site_list_visible(self, visible: bool) -> None:
        # Affiche ou masque la section de gestion des sites
        if visible:
            if hasattr(self, "sites_frame"):
                try:
                    self.sites_frame.grid()
                except Exception:
                    pass

            # Réaffiche les éléments
            self._apply_site_visibility_to_ui()
            self._refresh_custom_sites_listbox()
        else:
            self._hide_site_list_ui()

    def _auto_resize_window(self) -> None:
        """Ajuste la taille de la fenêtre pour afficher tout le contenu visible."""
        try:
            self.root.update_idletasks()
            req_w = self.root.winfo_reqwidth()
            req_h = self.root.winfo_reqheight()
            cur_w = self.root.winfo_width()
            cur_h = self.root.winfo_height()
            new_w = max(cur_w, req_w)
            new_h = max(cur_h, req_h)
            self.root.geometry(f"{new_w}x{new_h}")
        except Exception:
            pass

    def _toggle_site_list_visibility(self) -> None:
        self._sites_visible = not getattr(self, "_sites_visible", False)

        if self._sites_visible:
            # Sauvegarde uniquement la taille (WxH) avant d'étendre
            try:
                geo = self.root.geometry()          # "WxH+X+Y"
                self._saved_size = geo.split("+")[0]  # "WxH"
            except Exception:
                self._saved_size = None
        else:
            # Restaurer la taille d'avant en gardant la position actuelle
            saved = getattr(self, "_saved_size", None)
            if saved:
                try:
                    x = self.root.winfo_x()
                    y = self.root.winfo_y()
                    self.root.geometry(f"{saved}+{x}+{y}")
                except Exception:
                    pass

        self._set_site_list_visible(self._sites_visible)
        if hasattr(self, "toggle_sites_btn"):
            self.toggle_sites_btn.config(text="Masquer sites" if self._sites_visible else "Gérer sites")
        self._auto_resize_window()



    def _remove_builtin_site(self, key: str) -> None:
        # Retire un site (natif ou personnalisé)
        site = next((s for s in self.sites if s.get("key") == key), None)
        if not site:
            return
        
        name = site.get("name", key)
        if not messagebox.askyesno(
            "Supprimer", f"Supprimer le site '{name}' ?"
        ):
            return

        self.sites = [s for s in self.sites if s.get("key") != key]
        if key in self._custom_site_rows:
            try:
                self._custom_site_rows[key][0].destroy()  # row_frame
            except Exception:
                pass
            del self._custom_site_rows[key]
        
        self.last_state = {k: v for k, v in self.last_state.items() if k != name}
        self._update_global_status()
        self._refresh_custom_sites_listbox()
        self._save_config()

    def _status_badge_colors(self, status: str) -> tuple[str, str]:
        """Retourne (bg, fg) pour le badge de statut."""
        s = (status or "").split()[0]
        return {
            "OUVERT": ("#dcfce7", "#15803d"),
            "FERMÉ": ("#fee2e2", "#b91c1c"),
            "ERREUR": ("#fef3c7", "#b45309"),
        }.get(s, ("#f3f4f6", "#6b7280"))

    def _create_custom_site_row(self, site: dict) -> None:
        key = site.get("key")
        if not key:
            return

        panel = getattr(self, "_panel_color", "#ffffff")
        row_idx = len(self._custom_site_rows)

        row_frame = tk.Frame(
            self.custom_sites_rows_container,
            bg=panel,
            highlightbackground="#e5e7eb",
            highlightthickness=1,
        )
        row_frame.grid(row=row_idx, column=0, sticky="ew", padx=0, pady=3)
        row_frame.columnconfigure(1, weight=1)
        self.custom_sites_rows_container.columnconfigure(0, weight=1)

        enabled = site.get("enabled", True)
        name_color = "#1f2933" if enabled else "#9ca3af"

        # Indicateur ●
        led = tk.Label(row_frame, text="●", bg=panel, fg="#9ca3af",
                       font=("Segoe UI Variable", 13))
        led.grid(row=0, column=0, padx=(10, 6), pady=8)

        # Nom du site
        name_lbl = tk.Label(row_frame, text=site.get("name", ""), bg=panel,
                            fg=name_color, font=("Segoe UI Variable", 9, "bold"))
        name_lbl.grid(row=0, column=1, padx=(0, 8), pady=8, sticky="w")

        # Badge statut
        badge_bg, badge_fg = self._status_badge_colors(site.get("status", "Inconnu"))
        status_badge = tk.Label(row_frame, text=site.get("status", "Inconnu"),
                                bg=badge_bg, fg=badge_fg,
                                font=("Segoe UI Variable", 8, "bold"),
                                padx=8, pady=2)
        status_badge.grid(row=0, column=2, padx=(0, 10), pady=8)

        # Case activé
        var = tk.BooleanVar(value=enabled)
        def _make_toggle(k, v, nf, rf, p):
            def _toggle():
                is_on = v.get()
                nf.configure(fg="#1f2933" if is_on else "#9ca3af")
                self._on_custom_site_toggle(k, is_on)
            return _toggle
        check = ttk.Checkbutton(
            row_frame, text="", variable=var,
            command=_make_toggle(key, var, name_lbl, row_frame, panel),
        )
        check.grid(row=0, column=3, padx=(0, 4), pady=8)

        # Bouton tester ▶
        test_btn = tk.Button(
            row_frame, text="▶", bg=panel, fg="#0078D4",
            font=("Segoe UI Variable", 10), bd=0, relief="flat",
            activebackground="#e0f0ff", activeforeground="#005a9e",
            cursor="hand2",
            command=lambda k=key: self._test_site_threaded(k),
        )
        test_btn.grid(row=0, column=4, padx=(0, 2), pady=8)
        _Tooltip(test_btn, "Tester ce site maintenant")

        # Bouton éditer ✎
        edit_btn = tk.Button(
            row_frame, text="✎", bg=panel, fg="#6b7280",
            font=("Segoe UI Variable", 10), bd=0, relief="flat",
            activebackground="#f0f4ff", activeforeground="#374151",
            cursor="hand2",
            command=lambda k=key: self._edit_site(k),
        )
        edit_btn.grid(row=0, column=5, padx=(0, 2), pady=8)
        _Tooltip(edit_btn, "Modifier le nom / l'URL de ce site")

        # Bouton supprimer ×
        remove = tk.Button(
            row_frame, text="×", bg=panel, fg="#9ca3af",
            font=("Segoe UI Variable", 12), bd=0, relief="flat",
            activebackground="#fee2e2", activeforeground="#b91c1c",
            cursor="hand2",
            command=lambda k=key: self._remove_custom_site(k),
        )
        remove.grid(row=0, column=6, padx=(0, 8), pady=8)
        _Tooltip(remove, "Supprimer ce site de la liste")

        self._custom_site_rows[key] = [row_frame, led, name_lbl, status_badge, check, test_btn, edit_btn, remove]
        if not getattr(self, "_sites_visible", False):
            row_frame.grid_remove()

    def _remove_custom_site(self, key: str) -> None:
        # Supprime un site custom de la liste et de l'UI
        self.sites = [s for s in self.sites if s.get("key") != key]
        if key in self._custom_site_rows:
            try:
                self._custom_site_rows[key][0].destroy()  # row_frame
            except Exception:
                pass
            del self._custom_site_rows[key]
        self._refresh_custom_sites_listbox()
        self._save_config()

    def _test_site_threaded(self, key: str) -> None:
        """Déclenche un test immédiat pour un site spécifique depuis l'UI."""
        site = next((s for s in self.sites if s.get("key") == key), None)
        if site:
            self.root.after(0, lambda: self._test_site(site, show_popup_on_open=True))

    def _edit_site(self, key: str) -> None:
        """Ouvre une boîte de dialogue pour modifier le nom/URL d'un site."""
        site = next((s for s in self.sites if s.get("key") == key), None)
        if not site:
            return
        is_builtin = site.get("builtin", False)
        new_name = site.get("name", "")
        if not is_builtin:
            new_name = simpledialog.askstring(
                "Modifier le site", "Nom du site :",
                initialvalue=site.get("name", ""),
            )
            if new_name is None:
                return
            new_name = new_name.strip()
            if not new_name:
                messagebox.showerror("Erreur", "Le nom ne peut pas être vide.")
                return
            normalized = lambda v: (v or "").strip().lower()
            if any(
                s.get("key") != key and normalized(s.get("name")) == normalized(new_name)
                for s in self.sites
            ):
                messagebox.showinfo("Site existant", "Un site avec ce nom existe déjà.")
                return
        new_url = simpledialog.askstring(
            "Modifier le site", "URL :",
            initialvalue=site.get("url", ""),
        )
        if new_url is None:
            return
        new_url = new_url.strip()
        parsed = urllib.parse.urlparse(new_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            messagebox.showerror("URL invalide", "L'URL doit commencer par http:// ou https://.")
            return
        if not is_builtin:
            site["name"] = new_name
        site["url"] = new_url
        self._save_config()
        self._refresh_custom_sites_listbox()

    def _check_now(self) -> None:
        """Déclenche une vérification immédiate de tous les sites actifs."""
        enabled = [s for s in self.sites if s.get("enabled", True)]
        if not enabled:
            return
        self._append_log("---- Vérification manuelle ----")
        for i, site in enumerate(enabled):
            self.root.after(i * 800, lambda s=site: self._test_site(s, show_popup_on_open=True))

    def _on_add_custom_site(self) -> None:
        name = simpledialog.askstring("Ajouter un site", "Nom du site :")
        if not name:
            return
        url = simpledialog.askstring(
            "Ajouter un site", "URL (inclure http:// ou https://) :"
        )
        if not url:
            return

        name = name.strip()
        url = url.strip()

        # Évite les doublons dans le modèle
        normalized = lambda v: (v or "").strip().lower()
        if any(
            normalized(s.get("name")) == normalized(name) or
            normalized(s.get("url")) == normalized(url)
            for s in self.sites
        ):
            messagebox.showinfo(
                "Site existant",
                "Ce site est déjà présent dans la liste des sites surveillés.",
            )
            return

        # Validation de l'URL
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            messagebox.showerror(
                "URL invalide",
                "L'URL doit commencer par http:// ou https:// et être valide.",
            )
            return

        # Test de connectivité rapide (ne bloque que quelques secondes)
        try:
            resp = requests.get(url, timeout=5, headers=DEFAULT_HEADERS)
            if resp.status_code >= 400:
                if not messagebox.askyesno(
                    "Site non joignable",
                    f"Le site a renvoyé le code HTTP {resp.status_code}.\n\nVoulez-vous l'ajouter quand même ?",
                ):
                    return
        except Exception as exc:
            if not messagebox.askyesno(
                "Erreur de connexion",
                f"Impossible de joindre le site : {exc}\n\nVoulez-vous l'ajouter quand même ?",
            ):
                return

        self._custom_site_counter += 1
        key = f"custom_{self._custom_site_counter}"
        site = {
            "key": key,
            "name": name,
            "url": url,
            "enabled": True,
            "builtin": False,
            "status": "Inconnu",
        }
        self.sites.append(site)
        self._rebuild_sites_list()
        self._save_config()
        self._refresh_custom_sites_listbox()

    def _on_remove_custom_site(self) -> None:
        # Cette méthode est conservée pour rétrocompatibilité, mais n'est plus utilisée
        # car les sites personnalisés sont gérés via des boutons "Suppr" individuels.
        return

    def _test_site(self, site: dict, show_popup_on_open: bool = True) -> bool:
        now_str = time.strftime("%H:%M:%S")
        name = site.get("name", "site")
        url = site.get("url")
        
        if not url:
            self._append_log(f"[{now_str}] {name} : Erreur : URL manquante")
            return False

        try:
            start_time = time.perf_counter()
            response = self._requests_session.get(url, timeout=10, headers=DEFAULT_HEADERS)
            latency_ms = (time.perf_counter() - start_time) * 1000
            self.last_latency_ms[name] = latency_ms

            content = response.text
            if response.status_code >= 400:
                # Certains sites (tctg, etc.) renvoient 500 même quand la page existe
                # (ex: 'inscriptions fermées'). On détecte cela via le contenu.
                if re.search(CLOSED_MARKER_PATTERN, content, re.IGNORECASE):
                    status = "FERMÉ"
                    color = "red"
                    self._append_log(
                        f"[{now_str}] {name} : Fermé (HTTP {response.status_code}, motif 'Inscr. fermées') - {int(latency_ms)}ms"
                    )
                else:
                    status = f"ERREUR ({response.status_code})"
                    color = "orange red"
                    self._append_log(
                        f"[{now_str}] {name} : Erreur HTTP {response.status_code} - {int(latency_ms)}ms"
                    )
                    self._log_exception(name, url, Exception(f"HTTP {response.status_code}"))
            else:
                if re.search(CLOSED_MARKER_PATTERN, content, re.IGNORECASE):
                    status = "FERMÉ"
                    color = "red"
                    self._append_log(
                        f"[{now_str}] {name} : Fermé (motif 'Inscriptions ... fermé/fermées' détecté) - {int(latency_ms)}ms"
                    )
                else:
                    status = "OUVERT"
                    color = "green"
                    self._append_log(f"[{now_str}] {name} : OUVERT ! - {int(latency_ms)}ms")

            # Met à jour les horodatages d'ouverture/fermeture pour le statut global
            if status == "OUVERT":
                self.last_open_time[name] = time.time()
            elif status == "FERMÉ":
                self.last_close_time[name] = time.time()

            site["status"] = status
            self._set_last_check(f"Dernière vérification : {now_str} ({name} {status})")
            self._record_history(site, status)
            self._update_site_ui(site, status, color)

            # Alertes
            if status == "OUVERT" and show_popup_on_open:
                if self.play_sound_on_open:
                    try:
                        self.root.bell()
                    except Exception:
                        pass

                # Toujours envoyer le ballon systray / toast (visible même si fenêtre masquée)
                self._notify_windows(
                    f"{name} - INSCRIPTIONS OUVERTES",
                    f"Les inscriptions à {name} semblent OUVERTES !",
                )

                # Popup + navigateur seulement si la fenêtre est visible (pas en mode tray)
                window_visible = self.root.winfo_viewable()
                if window_visible:
                    messagebox.showinfo(
                        f"{name} - INSCRIPTIONS OUVERTES",
                        f"Les inscriptions à {name} semblent OUVERTES !\n"
                        f"Va vite sur le site : {url}",
                    )

                if self.open_browser_on_open:
                    if url:
                        webbrowser.open(url)
            elif status.startswith("ERREUR") and self.alert_on_error:
                if self.play_sound_on_open:
                    try:
                        self.root.bell()
                    except Exception:
                        pass
                self._notify_windows(
                    f"{name} - ERREUR",
                    f"Le site {name} renvoie une erreur ({status}).",
                )

            return status == "OUVERT"

        except requests.Timeout as exc:
            status = "ERREUR (timeout)"
            color = "orange red"
            self._set_last_check(f"Dernière vérification : {now_str} ({name} erreur)")
            self._record_history(site, status)
            self._append_log(f"[{now_str}] {name} : Erreur timeout")
            self._log_exception(name, url, exc)
            self._update_site_ui(site, status, color)
            return False

        except requests.RequestException as exc:
            status = "ERREUR (connexion)"
            color = "orange red"
            self._set_last_check(f"Dernière vérification : {now_str} ({name} erreur)")
            self._record_history(site, status)
            self._append_log(f"[{now_str}] {name} : Erreur de connexion")
            self._log_exception(name, url, exc)
            self._update_site_ui(site, status, color)
            return False

        except Exception as exc:  # noqa: BLE001
            status = "ERREUR"
            color = "orange red"
            self._set_last_check(f"Dernière vérification : {now_str} ({name} erreur)")
            self._record_history(site, status)
            self._append_log(f"[{now_str}] {name} : Erreur : {exc}")
            self._log_exception(name, url, exc)
            self._update_site_ui(site, status, color)
            return False

    def _update_site_ui(self, site: dict, status: str, color: str) -> None:
        site["status"] = status
        key = site.get("key")
        if key in self._custom_site_rows:
            row_widgets = self._custom_site_rows[key]
            # [row_frame, led, name_lbl, status_badge, check, remove]
            led_color = {"green": "#22c55e", "red": "#ef4444", "orange red": "#f59e0b", "gray": "#d1d5db"}.get(color, "#d1d5db")
            try:
                cast(tk.Label, row_widgets[1]).configure(fg=led_color)
            except Exception:
                pass
            badge_bg, badge_fg = self._status_badge_colors(status)
            try:
                cast(tk.Label, row_widgets[3]).configure(text=status, bg=badge_bg, fg=badge_fg)
            except Exception:
                pass
        self._refresh_custom_sites_listbox()

    def _get_site_url(self, name: str) -> str | None:
        for s in self.sites:
            if s.get("name") == name or s.get("key") == name:
                return s.get("url")
        return None

    def _record_history(self, site: dict, status: str) -> None:
        site_key = site.get("key") or site.get("name", "unknown")
        previous = self.last_state.get(site_key)
        if not self.log_every_check and previous == status:
            return
        self.last_state[site_key] = status

        entry = {
            "datetime": time.strftime("%Y-%m-%d %H:%M:%S"),
            "site": site.get("name", ""),
            "url": site.get("url", ""),
            "status": status,
        }
        self.history.append(entry)
        # Garde au max 2000 entrées
        if len(self.history) > 2000:
            self.history = self.history[-2000:]
        self._save_history()
        self._update_global_status()

    def _save_history(self) -> None:
        try:
            HISTORY_PATH.write_text(json.dumps(self.history, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load_history(self) -> None:
        if not HISTORY_PATH.exists():
            return
        try:
            data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self.history = data
        except Exception:
            pass

    def _update_global_status(self) -> None:
        values = [v for v in self.last_state.values() if v is not None]
        counts = {"OUVERT": 0, "FERMÉ": 0, "ERREUR": 0}
        for v in values:
            key = (v or "").split()[0]
            if key in counts:
                counts[key] += 1

        if not values:
            text = "État global : Inconnu"
            color = "gray"
        elif counts["OUVERT"] > 0:
            text = "État global : Au moins un site OUVERT"
            color = "green"
        elif counts["ERREUR"] > 0:
            text = "État global : Erreur sur au moins un site"
            color = "orange red"
        else:
            text = "État global : Tous fermés"
            color = "red"

        self.global_status_label.configure(text=text, foreground=color)
        if hasattr(self, "lbl_cnt_open"):
            self.lbl_cnt_open.configure(
                text=f"● OUVERT: {counts['OUVERT']}",
                foreground="#16a34a" if counts["OUVERT"] > 0 else "#9ca3af",
            )
            self.lbl_cnt_close.configure(
                text=f"● FERMÉ: {counts['FERMÉ']}",
                foreground="#dc2626" if counts["FERMÉ"] > 0 else "#9ca3af",
            )
            self.lbl_cnt_err.configure(
                text=f"● ERREUR: {counts['ERREUR']}",
                foreground="#d97706" if counts["ERREUR"] > 0 else "#9ca3af",
            )

        # Détail temps
        last_open = self._format_since_last(self.last_open_time)
        last_close = self._format_since_last(self.last_close_time)
        self.last_activity_label.configure(
            text=f"Dernière ouverture: {last_open}   Dernière fermeture: {last_close}"
        )

        if hasattr(self, "global_status_led"):
            self.global_status_led.configure(foreground=color)
        self._update_tray_icon_color(color)

    def _format_since_last(self, data: dict[str, float]) -> str:
        if not data:
            return "-"
        # prend la plus récente des valeurs stockées
        last = max(data.values())
        age = time.time() - last
        if age < 60:
            return f"il y a {int(age)}s"
        if age < 3600:
            return f"il y a {int(age/60)}m"
        return f"il y a {int(age/3600)}h"

    def _notify_windows(self, title: str, message: str) -> None:
        # Ballon sur l'icône systray (pystray) — visible près de l'horloge
        if self.tray_icon is not None:
            try:
                cast(Any, self.tray_icon).notify(message, title)
            except Exception:
                pass
        # Notification Windows toast (win10toast) en complément / fallback
        if self.toaster:
            try:
                self.toaster.show_toast(title, message, duration=5, threaded=True)
            except Exception:
                pass

    # --- Gestion icône de zone de notification (systray) ---

    def _create_tray_image(self, color: Tuple[int, int, int] = (0, 160, 0)) -> Optional["_PILImage.Image"]:
        if Image is None or ImageDraw is None:
            return None
        size = 64
        try:
            bg = cast(Tuple[int, int, int, int], (0, 0, 0, 0))
            image = Image.new("RGBA", (size, size), cast(Any, bg))
            draw = ImageDraw.Draw(image)
            draw.ellipse((8, 8, size - 8, size - 8), fill=(color[0], color[1], color[2], 255))
            return image
        except Exception:
            return None

    def _tray_show(self, icon, item) -> None:  # callbacks pystray
        # Repasser par le thread Tk
        self.root.after(0, self._restore_from_tray)

    def _tray_quit(self, icon, item) -> None:
        self.root.after(0, self._quit_from_tray)

    def _start_tray_icon(self) -> None:
        if pystray is None:
            return
        if self.tray_icon is not None:
            return

        image = self._create_tray_image()
        if image is None:
            return

        menu = pystray.Menu(
            pystray.MenuItem("Afficher la fenêtre", self._tray_show),
            pystray.MenuItem("Quitter complètement", self._tray_quit),
        )
        self.tray_icon = pystray.Icon(
            "lacale_watcher", image, "Surveillance inscriptions", menu
        )

        def _run_icon() -> None:
            try:
                # cast to satisfy type checker (we just created the icon)
                cast(Any, self.tray_icon).run()
            except Exception:
                self.tray_icon = None

        threading.Thread(target=_run_icon, daemon=True).start()

    def _update_tray_icon_color(self, status_color: str) -> None:
        if self.tray_icon is None or Image is None or ImageDraw is None:
            return
        color_map = {
            "green": (0, 160, 0),
            "red": (200, 0, 0),
            "orange red": (255, 100, 0),
            "gray": (120, 120, 120),
        }
        rgb = color_map.get(status_color, (0, 160, 0))
        img = self._create_tray_image(rgb)
        if img is None:
            return
        try:
            self.tray_icon.icon = img
        except Exception:
            pass

    def _restore_from_tray(self) -> None:
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None

        self.root.deiconify()
        self.root.after(100, self.root.lift)

    def _quit_from_tray(self) -> None:
        # On force la fermeture complète
        self.background_on_close = False
        self._on_close()

    def _set_last_check(self, text: str) -> None:
        self.last_check_label.config(text=text)

    def _set_next_check(self, seconds: int | None) -> None:
        """Affiche un compte à rebours avant la prochaine vérification."""
        if seconds is None:
            self.next_check_label.config(text="Prochaine vérif : -")
            if hasattr(self, "next_check_progress"):
                self.next_check_progress["value"] = 0
            return
        if seconds <= 0:
            self.next_check_label.config(text="Prochaine vérif : maintenant")
            if hasattr(self, "next_check_progress"):
                self.next_check_progress["value"] = 0
            return
        self.next_check_label.config(text=f"Prochaine vérif : {seconds}s")
        if hasattr(self, "next_check_progress"):
            total = getattr(self, "_total_countdown_seconds", 0)
            if total > 0:
                self.next_check_progress["value"] = 100 * seconds / total

    def _cancel_countdown(self) -> None:
        job = getattr(self, "_countdown_job", None)
        if job:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
            self._countdown_job = None

    def _start_countdown(self, seconds: int) -> None:
        self._cancel_countdown()
        self._remaining_seconds = seconds
        self._total_countdown_seconds = seconds

        def tick() -> None:
            if not getattr(self, "running", False):
                self._set_next_check(None)
                return
            if self._remaining_seconds <= 0:
                self._set_next_check(0)
                self._countdown_job = None
                return
            self._set_next_check(self._remaining_seconds)
            self._remaining_seconds -= 1
            self._countdown_job = self.root.after(1000, tick)

        tick()

    def _on_interval_change(self) -> None:
        try:
            val = int(self.interval_var.get())
        except (TypeError, ValueError):
            val = 5

        # Minimum absolu 5 minutes côté interface
        val = max(5, min(60, val))
        self.interval_minutes = val
        self.interval_var.set(val)
        self._save_config()

    def _watcher_loop(self) -> None:
        # Premier passage : tests immédiats, mais étalés dans le temps pour éviter les pics
        initial_delay_ms = 0
        step_ms = 800  # 0,8 s entre chaque site

        for site in self.sites:
            if not site.get("enabled", True):
                continue
            self.root.after(
                initial_delay_ms, lambda s=site: self._test_site(s, show_popup_on_open=True)
            )
            initial_delay_ms += step_ms

        while self.running:
            # On ajoute un léger aléa autour de l'intervalle pour éviter un motif trop "parfait"
            base_seconds = self.interval_minutes * 60
            jitter = random.randint(-15, 15)  # +/- 15 secondes
            target_seconds = max(30, base_seconds + jitter)

            # Afficher un compte à rebours avant la prochaine vérification
            self.root.after(0, lambda: self._start_countdown(target_seconds))

            for _ in range(target_seconds):
                if not self.running:
                    break
                time.sleep(1)
            if not self.running:
                break
            # Checks suivants (sans popup systématique pour éviter le spam)
            for site in self.sites:
                if not site.get("enabled", True):
                    continue
                self.root.after(0, lambda s=site: self._test_site(s, show_popup_on_open=False))

    def start_watching(self) -> None:
        if self.running:
            return

        self._on_interval_change()
        self.running = True
        self.start_button.config(state=tk.DISABLED, bg="#d1d5db", fg="#9ca3af")
        self.stop_button.config(state=tk.NORMAL, bg=self._accent_color, fg="#ffffff")
        self._append_log(
            f"---- Surveillance démarrée (toutes les {self.interval_minutes} min) ----"
        )

        self._append_log(f"[info] Données stockées dans : {BASE_DIR}")
        self.thread = threading.Thread(target=self._watcher_loop, daemon=True)
        self.thread.start()

    def stop_watching(self) -> None:
        if not self.running:
            return

        self.running = False
        self._cancel_countdown()
        self._set_next_check(None)

        self.start_button.config(state=tk.NORMAL, bg=self._accent_color, fg="#ffffff")
        self.stop_button.config(state=tk.DISABLED, bg="#d1d5db", fg="#9ca3af")
        self._append_log("---- Surveillance arrêtée ----")

    def _on_close(self) -> None:
        if self.background_on_close:
            # On garde la surveillance mais on cache la fenêtre et on passe en icône de zone de notification
            self.root.withdraw()
            self._start_tray_icon()
            self._save_config()
            return

        self.running = False
        self._cancel_countdown()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        self._save_config()
        self.root.destroy()

    def _on_options_changed(self) -> None:
        self.open_browser_on_open = self.var_open_browser.get()
        self.play_sound_on_open = self.var_play_sound.get()
        self.background_on_close = self.var_background_on_close.get()
        self.autostart_with_windows = self.var_autostart.get()
        self.log_every_check = self.var_log_every_check.get()
        self.alert_on_error = self.var_alert_on_error.get()
        self._update_autostart()
        self._save_config()

    def _update_autostart(self) -> None:
        reg_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "LacaleWatcher"
        try:
            import winreg
            if self.autostart_with_windows:
                # Chemin à lancer : l'exe PyInstaller ou python + script
                if getattr(sys, "frozen", False):
                    launch_cmd = f'"{sys.executable}"'
                else:
                    script = Path(__file__).resolve()
                    launch_cmd = f'"{sys.executable}" "{script}"'
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, reg_key, 0, winreg.KEY_SET_VALUE
                ) as key:
                    winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, launch_cmd)
            else:
                try:
                    with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER, reg_key, 0, winreg.KEY_SET_VALUE
                    ) as key:
                        winreg.DeleteValue(key, app_name)
                except FileNotFoundError:
                    pass
        except Exception:
            pass

    def _load_config(self) -> None:
        if not CONFIG_PATH.exists():
            return
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return

        self.interval_minutes = int(data.get("interval_minutes", self.interval_minutes))
        self.open_browser_on_open = bool(data.get("open_browser_on_open", self.open_browser_on_open))
        self.play_sound_on_open = bool(data.get("play_sound_on_open", self.play_sound_on_open))
        self.background_on_close = bool(data.get("background_on_close", self.background_on_close))
        self.autostart_with_windows = bool(
            data.get("autostart_with_windows", self.autostart_with_windows)
        )
        self.log_every_check = bool(data.get("log_every_check", self.log_every_check))
        self.alert_on_error = bool(data.get("alert_on_error", self.alert_on_error))

        # Si le config contient déjà une liste complète de sites, on l'utilise.
        sites_data = data.get("sites")
        if isinstance(sites_data, list):
            self.sites = []
            for site in sites_data:
                if not isinstance(site, dict):
                    continue
                name = site.get("name")
                url = site.get("url")
                if not name or not url:
                    continue
                self.sites.append(
                    {
                        "key": site.get("key"),
                        "name": str(name),
                        "url": str(url),
                        "enabled": bool(site.get("enabled", True)),
                        "builtin": bool(site.get("builtin", False)),
                        "status": "Inconnu",
                    }
                )

            # On s'assure que les 4 sites natifs sont toujours présents (pas d'effacement accidentel).
            existing_keys = {s.get("key") for s in self.sites if s.get("key")}
            for builtin in BUILTIN_SITES:
                if builtin["key"] not in existing_keys:
                    self.sites.append(
                        {
                            "key": builtin["key"],
                            "name": builtin["name"],
                            "url": builtin["url"],
                            "enabled": True,
                            "builtin": True,
                            "status": "Inconnu",
                        }
                    )
            self._rebuild_sites_list()
            return

        # Mode ancien (compatibilité) : custom_sites + removed_builtin_sites
        self.removed_builtin_sites = []
        for site in data.get("removed_builtin_sites", []):
            if not isinstance(site, str):
                continue
            if site in {"lacale", "abn", "tctg", "hdf"}:
                self.removed_builtin_sites.append(site)

        self.sites = []
        for builtin in BUILTIN_SITES:
            if builtin["key"] in self.removed_builtin_sites:
                continue
            self.sites.append(
                {
                    "key": builtin["key"],
                    "name": builtin["name"],
                    "url": builtin["url"],
                    "enabled": True,
                    "builtin": True,
                    "status": "Inconnu",
                }
            )

        for site in data.get("custom_sites", []):
            if not isinstance(site, dict):
                continue
            name = site.get("name")
            url = site.get("url")
            if not name or not url:
                continue
            key = site.get("key")
            if not key:
                self._custom_site_counter += 1
                key = f"custom_{self._custom_site_counter}"
            self.sites.append(
                {
                    "key": str(key),
                    "name": str(name),
                    "url": str(url),
                    "enabled": bool(site.get("enabled", True)),
                    "builtin": False,
                    "status": "Inconnu",
                }
            )

    def _save_config(self) -> None:
        data = {
            "interval_minutes": self.interval_minutes,
            "open_browser_on_open": self.open_browser_on_open,
            "play_sound_on_open": self.play_sound_on_open,
            "background_on_close": self.background_on_close,
            "autostart_with_windows": self.autostart_with_windows,
            "log_every_check": self.log_every_check,
            "alert_on_error": self.alert_on_error,
            "sites": [
                {
                    "key": site.get("key"),
                    "name": site.get("name"),
                    "url": site.get("url"),
                    "enabled": bool(site.get("enabled", True)),
                    "builtin": bool(site.get("builtin", False)),
                }
                for site in self.sites
            ],
        }
        try:
            CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _on_custom_site_toggle(self, key: str, enabled: bool) -> None:
        # Met à jour l'état "enabled" du site custom et sauvegarde la config
        for site in self.sites:
            if site.get("key") == key:
                site["enabled"] = bool(enabled)
                break
        self._save_config()
        self._refresh_custom_sites_listbox()

    def _build_custom_site_rows(self) -> None:
        # Reconstruit les lignes de tous les sites (natifs et personnalisés) affichées dans l'UI.
        # Supprime les anciennes lignes (widgets) puis les recrée.
        for widgets in self._custom_site_rows.values():
            for w in widgets:
                try:
                    w.destroy()
                except Exception:
                    pass
        self._custom_site_rows = {}

        for site in self.sites:
            key = site.get("key")
            if not key:
                self._custom_site_counter += 1
                key = f"custom_{self._custom_site_counter}"
                site["key"] = key
            self._create_custom_site_row(site)

        # Ajuste automatiquement la fenêtre pour que tout le contenu soit visible
        self._auto_resize_window()

    def _apply_config_to_ui(self) -> None:
        self.interval_var.set(self.interval_minutes)
        self.var_open_browser.set(self.open_browser_on_open)
        self.var_play_sound.set(self.play_sound_on_open)
        self.var_background_on_close.set(self.background_on_close)
        self.var_autostart.set(self.autostart_with_windows)
        self.var_log_every_check.set(self.log_every_check)
        self.var_alert_on_error.set(self.alert_on_error)
        self._refresh_custom_sites_listbox()
        self._set_site_list_visible(self._sites_visible)


def main() -> None:
    root = tk.Tk()
    LacaleWatcherApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
