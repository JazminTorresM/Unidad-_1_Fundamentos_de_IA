"""
Anime Crawler GUI
=================
Crawler con interfaz gráfica para extraer Título, Reseña y Géneros de animes
(por defecto de https://tioanime.com/) y exportarlos a CSV (utf-8-sig).

Dependencias:
    pip install requests beautifulsoup4 pandas customtkinter

Ejecución:
    python anime_crawler.py

Estructura:
    - AnimeParser   -> extrae datos del HTML (selectores + respaldos).
    - CrawlerEngine -> lógica de rastreo en un hilo (colas, set de visitados).
    - App           -> interfaz customtkinter (no bloquea; recibe eventos por Queue).
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from collections import deque
from tkinter import filedialog, messagebox, ttk
from urllib.parse import parse_qsl, urldefrag, urlencode, urljoin, urlparse

import customtkinter as ctk
import pandas as pd
import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------------- #
DEFAULT_SEED = "https://tioanime.com/"
REQUEST_DELAY = 0.8      # segundos entre peticiones
REQUEST_TIMEOUT = 15     # segundos
MAX_RETRIES = 2
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
}


# --------------------------------------------------------------------------- #
# Utilidades de URL
# --------------------------------------------------------------------------- #
def normalize_url(url: str) -> str:
    """Quita fragmentos y ordena parámetros para evitar duplicados."""
    url, _ = urldefrag(url)
    p = urlparse(url)
    query = urlencode(sorted(parse_qsl(p.query, keep_blank_values=True)))
    path = p.path.rstrip("/") or "/"
    return p._replace(path=path, query=query).geturl()


def is_detail_url(url: str) -> bool:
    return urlparse(url).path.startswith("/anime/")


def is_listing_url(url: str) -> bool:
    return urlparse(url).path.startswith("/directorio")


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
class AnimeParser:
    """Extrae datos de páginas de detalle y enlaces de páginas de listado."""

    @staticmethod
    def parse_detail(soup: BeautifulSoup) -> dict:
        # Título
        title_tag = soup.select_one("h1.title") or soup.find("h1")
        title = title_tag.get_text(" ", strip=True) if title_tag else ""
        if not title and soup.title:
            title = soup.title.get_text(strip=True).replace(" - TioAnime", "")

        # Reseña: <p class="sinopsis"> con respaldo en og:description
        syn_tag = soup.select_one("p.sinopsis")
        synopsis = syn_tag.get_text(" ", strip=True) if syn_tag else ""
        if not synopsis:
            meta = soup.find("meta", property="og:description")
            if meta and meta.get("content"):
                synopsis = meta["content"].replace(" - TioAnime animes de Calidad", "").strip()

        # Géneros: enlaces con ?genero=
        genres = []
        for a in soup.select("p.genres a, a[href*='genero=']"):
            g = a.get_text(strip=True)
            if g and g not in genres:
                genres.append(g)

        return {"Título": title, "Géneros": ", ".join(genres), "Reseña": synopsis}

    @staticmethod
    def extract_links(soup: BeautifulSoup, base_url: str, host: str) -> set[str]:
        """Todos los enlaces internos relevantes (detalle y listados)."""
        links = set()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith(("javascript:", "mailto:", "#")):
                continue
            full = normalize_url(urljoin(base_url, href))
            p = urlparse(full)
            if p.netloc != host:
                continue
            if is_detail_url(full) or is_listing_url(full):
                links.add(full)
        return links


# --------------------------------------------------------------------------- #
# Crawler
# --------------------------------------------------------------------------- #
class CrawlerEngine(threading.Thread):
    """
    Rastreo en dos colas:
      - listing_queue: directorio, géneros y paginación.
      - detail_queue : páginas /anime/<slug> a extraer.
    `seen` evita visitas duplicadas. Comunica con la GUI mediante `events`.
    """

    def __init__(self, seed: str, events: queue.Queue, stop_event: threading.Event,
                 max_animes: int = 0):
        super().__init__(daemon=True)
        self.seed = normalize_url(seed)
        self.host = urlparse(self.seed).netloc
        self.events = events
        self.stop_event = stop_event
        self.max_animes = max_animes
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

        self.seen: set[str] = set()
        self.listing_queue: deque[str] = deque()
        self.detail_queue: deque[str] = deque()
        self.discovered = 0
        self.processed = 0

    # -- helpers ----------------------------------------------------------- #
    def _emit(self, kind: str, **data):
        self.events.put((kind, data))

    def _fetch(self, url: str) -> BeautifulSoup | None:
        for attempt in range(MAX_RETRIES + 1):
            if self.stop_event.is_set():
                return None
            try:
                r = self.session.get(url, timeout=REQUEST_TIMEOUT)
                if r.status_code == 200:
                    return BeautifulSoup(r.text, "html.parser")
                self._emit("log", msg=f"HTTP {r.status_code} en {url}")
                if r.status_code in (403, 429, 503):
                    time.sleep(2 * (attempt + 1))
                else:
                    return None
            except requests.RequestException as exc:
                self._emit("log", msg=f"Error de red ({exc.__class__.__name__}) en {url}")
                time.sleep(1.5)
        return None

    def _enqueue(self, url: str):
        if url in self.seen:
            return
        self.seen.add(url)
        if is_detail_url(url):
            self.detail_queue.append(url)
            self.discovered += 1
        elif is_listing_url(url):
            self.listing_queue.append(url)

    def _progress(self):
        self._emit("progress", discovered=self.discovered, processed=self.processed)

    # -- ciclo principal --------------------------------------------------- #
    def run(self):
        self._emit("log", msg=f"Iniciando desde {self.seed}")
        self._enqueue(self.seed)
        # Se asegura de recorrer el directorio completo aunque la semilla sea la home
        self._enqueue(normalize_url(urljoin(self.seed, "/directorio")))

        while (self.listing_queue or self.detail_queue) and not self.stop_event.is_set():
            if self.max_animes and self.processed >= self.max_animes:
                self._emit("log", msg="Se alcanzó el límite de animes.")
                break

            # Prioridad: detalles primero (resultados en vivo); si no hay, listados
            if self.detail_queue:
                url = self.detail_queue.popleft()
                self._process_detail(url)
            else:
                url = self.listing_queue.popleft()
                self._process_listing(url)

            self._progress()
            time.sleep(REQUEST_DELAY)

        state = "detenido por el usuario" if self.stop_event.is_set() else "finalizado"
        self._emit("log", msg=f"Rastreo {state}. Animes extraídos: {self.processed}")
        self._emit("done")

    def _process_listing(self, url: str):
        self._emit("log", msg=f"[Listado] {url}")
        soup = self._fetch(url)
        if soup is None:
            return
        for link in AnimeParser.extract_links(soup, url, self.host):
            self._enqueue(link)

    def _process_detail(self, url: str):
        soup = self._fetch(url)
        if soup is None:
            self._emit("row", url=url, title="", genres="", synopsis="", status="Error")
            self.processed += 1
            return
        data = AnimeParser.parse_detail(soup)
        self.processed += 1
        status = "OK" if data["Título"] else "Sin título"
        self._emit("row", url=url, title=data["Título"], genres=data["Géneros"],
                   synopsis=data["Reseña"], status=status)
        # Los géneros del detalle alimentan nuevos listados (?genero=...)
        for link in AnimeParser.extract_links(soup, url, self.host):
            if is_listing_url(link):
                self._enqueue(link)


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #
class App(ctk.CTk):
    SHORT_LEN = 90
    POLL_MS = 150

    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.title("Anime Crawler – TioAnime")
        self.geometry("1100x700")
        self.minsize(900, 560)

        self.events: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.crawler: CrawlerEngine | None = None
        self.records: list[dict] = []

        self._build_ui()
        self.after(self.POLL_MS, self._poll_events)

    # -- construcción de la interfaz --------------------------------------- #
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=3)
        self.grid_rowconfigure(3, weight=1)

        # Barra superior: URL + límite + botones
        top = ctk.CTkFrame(self)
        top.grid(row=0, column=0, padx=12, pady=(12, 6), sticky="ew")
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(top, text="URL semilla:").grid(row=0, column=0, padx=(10, 6), pady=10)
        self.url_var = tk.StringVar(value=DEFAULT_SEED)
        ctk.CTkEntry(top, textvariable=self.url_var).grid(row=0, column=1, padx=6, sticky="ew")

        ctk.CTkLabel(top, text="Máx. animes (0 = todos):").grid(row=0, column=2, padx=(12, 6))
        self.limit_var = tk.StringVar(value="0")
        ctk.CTkEntry(top, textvariable=self.limit_var, width=60).grid(row=0, column=3, padx=6)

        self.btn_start = ctk.CTkButton(top, text="Iniciar Scraping", command=self.start)
        self.btn_start.grid(row=0, column=4, padx=6)
        self.btn_stop = ctk.CTkButton(top, text="Detener", fg_color="#b33a3a",
                                      hover_color="#8f2d2d", state="disabled",
                                      command=self.stop)
        self.btn_stop.grid(row=0, column=5, padx=6)
        self.btn_export = ctk.CTkButton(top, text="Exportar Dataset", fg_color="#2e8b57",
                                        hover_color="#236b43", command=self.export_csv)
        self.btn_export.grid(row=0, column=6, padx=(6, 10))

        # Tabla en vivo
        table_frame = ctk.CTkFrame(self)
        table_frame.grid(row=1, column=0, padx=12, pady=6, sticky="nsew")
        table_frame.grid_columnconfigure(0, weight=1)
        table_frame.grid_rowconfigure(0, weight=1)

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Treeview", background="#2b2b2b", fieldbackground="#2b2b2b",
                        foreground="white", rowheight=24, borderwidth=0)
        style.configure("Treeview.Heading", background="#3a3a3a", foreground="white",
                        relief="flat")
        style.map("Treeview", background=[("selected", "#1f6aa5")])

        cols = ("title", "genres", "synopsis", "status")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings")
        for col, text, width in (("title", "Título", 220), ("genres", "Género", 200),
                                 ("synopsis", "Reseña corta", 380),
                                 ("status", "Estado / URL", 260)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w")
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        # Progreso
        prog = ctk.CTkFrame(self)
        prog.grid(row=2, column=0, padx=12, pady=6, sticky="ew")
        prog.grid_columnconfigure(0, weight=1)
        self.progress = ctk.CTkProgressBar(prog)
        self.progress.set(0)
        self.progress.grid(row=0, column=0, padx=10, pady=(10, 4), sticky="ew")
        self.progress_lbl = ctk.CTkLabel(prog, text="Descubiertas: 0 | Procesadas: 0")
        self.progress_lbl.grid(row=1, column=0, pady=(0, 8))

        # Consola
        self.console = ctk.CTkTextbox(self, height=130, state="disabled")
        self.console.grid(row=3, column=0, padx=12, pady=(6, 12), sticky="nsew")

    # -- acciones ----------------------------------------------------------- #
    def start(self):
        seed = self.url_var.get().strip()
        if not seed.startswith(("http://", "https://")):
            messagebox.showwarning("URL inválida", "La URL debe iniciar con http:// o https://")
            return
        try:
            limit = max(0, int(self.limit_var.get() or 0))
        except ValueError:
            messagebox.showwarning("Límite inválido", "Escribe un número entero (0 = sin límite).")
            return

        if self.records and not messagebox.askyesno(
                "Nueva corrida", "Ya hay datos cargados. ¿Descartarlos y empezar de nuevo?"):
            return

        self.records.clear()
        self.tree.delete(*self.tree.get_children())
        self.progress.set(0)
        self.progress_lbl.configure(text="Descubiertas: 0 | Procesadas: 0")
        self.stop_event.clear()

        self.crawler = CrawlerEngine(seed, self.events, self.stop_event, limit)
        self.crawler.start()
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")

    def stop(self):
        self.stop_event.set()
        self.btn_stop.configure(state="disabled")
        self._log("Deteniendo… (termina la petición en curso)")

    def export_csv(self):
        if not self.records:
            messagebox.showinfo("Sin datos", "Aún no hay animes para exportar.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile="animes_dataset.csv")
        if not path:
            return
        df = pd.DataFrame(self.records, columns=["Título", "Géneros", "Reseña", "URL"])
        df.to_csv(path, index=False, encoding="utf-8-sig")
        self._log(f"Dataset exportado: {path} ({len(df)} filas)")
        messagebox.showinfo("Exportado", f"Se guardaron {len(df)} animes en:\n{path}")

    # -- eventos del crawler ------------------------------------------------ #
    def _poll_events(self):
        try:
            for _ in range(200):  # procesa por lotes para no congelar la GUI
                kind, data = self.events.get_nowait()
                if kind == "log":
                    self._log(data["msg"])
                elif kind == "row":
                    self._add_row(data)
                elif kind == "progress":
                    self._update_progress(data["discovered"], data["processed"])
                elif kind == "done":
                    self.btn_start.configure(state="normal")
                    self.btn_stop.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(self.POLL_MS, self._poll_events)

    def _add_row(self, d: dict):
        if d["status"] == "OK":
            self.records.append({"Título": d["title"], "Géneros": d["genres"],
                                 "Reseña": d["synopsis"], "URL": d["url"]})
        short = d["synopsis"]
        if len(short) > self.SHORT_LEN:
            short = short[: self.SHORT_LEN].rstrip() + "…"
        self.tree.insert("", "end", values=(d["title"], d["genres"], short,
                                            f'{d["status"]} – {d["url"]}'))
        self.tree.yview_moveto(1)

    def _update_progress(self, discovered: int, processed: int):
        self.progress.set(min(1, processed / discovered) if discovered else 0)
        self.progress_lbl.configure(
            text=f"Descubiertas: {discovered} | Procesadas: {processed}")

    def _log(self, msg: str):
        self.console.configure(state="normal")
        self.console.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        self.console.see("end")
        self.console.configure(state="disabled")


if __name__ == "__main__":
    App().mainloop()