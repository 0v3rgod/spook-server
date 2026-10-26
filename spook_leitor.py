#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SPOOK // READER - Cross-Device Reading Module
Integrated into SPOOK Overlay (GTK4 / Wayland)
SPOOK Palette: #0B0A12, #16141F, #5CE1E6, #FF4D9A, #00C853, JetBrains Mono

Okular-inspired features:
- High-fidelity rendering (PyMuPDF / fitz)
- Terminal Dark Inverted mode (inverts colors for black bg, readable text)
- Table of Contents (TOC)
- Dynamic zoom and page fit
- Jump to page number
- Full keyboard navigation (PageUp/Down, arrows, j/k, space)
- Auto-sync with Oracle Relay (http://100.70.88.101:5000)
- Cross-device position sync (SHA-256 book ID)
"""

import os
import sys
import json
import time
import hashlib
import threading
import subprocess
import shutil
import urllib.parse
from pathlib import Path
from typing import Optional, Dict, Any, List

import fitz  # PyMuPDF
import socketio

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, Gdk, GLib, Pango

# ============================ CONFIG ============================

ORACLE_RELAY_URL = "http://100.70.88.101:5000"
TOKEN_FILE = Path.home() / ".pentestlink" / "token_sessao.txt"
CACHE_DIR = Path.home() / ".cache" / "spook-reader"
POSICOES_FILE = CACHE_DIR / "positions.json"
LIVROS_FILE = CACHE_DIR / "books.json"  # Persisted book list on Arch

# Local books folder (mirrors Android's SpookReader)
SPOOK_READER_DIR = Path.home() / "SpookReader"

# Only user-opened files are tracked (no Drive listing)
# Files are added via "OPEN FILE" button or from Google Drive
DEVICE_ID = "arch-desktop"


def carregar_token() -> str:
    try:
        if TOKEN_FILE.exists():
            return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def calcular_book_id(caminho: Path) -> str:
    """SHA-256 of file for canonical cross-device identity."""
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def formatar_tamanho(bytes_: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_ < 1024:
            return f"{bytes_:.1f} {unit}"
        bytes_ /= 1024
    return f"{bytes_:.1f} TB"


# ============================ GERENCIADOR DO LEITOR ============================

class SpookLeitorManager:
    def __init__(self, overlay_app):
        self.app = overlay_app
        self.token = carregar_token()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        SPOOK_READER_DIR.mkdir(parents=True, exist_ok=True)

        self.posicoes: Dict[str, Dict[str, Any]] = self._carregar_posicoes()
        self.livros_lista: List[Dict[str, Any]] = self._carregar_livros()
        self.sio: Optional[socketio.Client] = None
        self.sio_conectado = False
        self.pendentes_pull: Dict[str, Any] = {}

        # Estado do livro atualmente aberto
        self.livro_atual: Optional[Dict[str, Any]] = None
        self.doc: Optional[fitz.Document] = None
        self.pagina_atual: int = 0
        self.total_paginas: int = 0
        self.zoom: float = 1.25
        self.modo_escuro: bool = True  # Default: Terminal Inverted
        self.debounce_timer: Optional[int] = None
        self.ultimo_dispositivo: str = ""

        # Sincronismo v2 (uma ficha por livro)
        self._supress_sync = 0            # >0: render de restauracao, nao emitir
        self._ultimo_em_local = 0         # em da ultima posicao que EU escrevi
        self._em_abertura = 0             # em da posicao com que abri o livro
        self._baixando: set = set()       # bookIds com download em andamento
        self._remoto_pendente: Optional[Dict[str, Any]] = None
        self.btn_chip_remoto: Optional[Gtk.Button] = None
        self._chip_alvo: Optional[int] = None
        self._pagina_abertura: int = 0

        # Widgets
        self.categoria_ativa = "ALL"

        self._iniciar_socketio()

    # ── SOCKET.IO SYNC WITH ORACLE RELAY ──

    def _iniciar_socketio(self):
        def _conectar():
            try:
                self.sio = socketio.Client(reconnection=True, reconnection_attempts=0, reconnection_delay=3)

                @self.sio.event
                def connect():
                    self.sio_conectado = True
                    GLib.idle_add(self._atualizar_badge_sync)
                    self._pedir_prateleira()
                    self._reenviar_posicoes_pendentes()
                    if self.livro_atual and self.livro_atual.get("bookId"):
                        try:
                            self.sio.emit("livro_puxar", {"bookId": self.livro_atual["bookId"]})
                        except Exception:
                            pass

                @self.sio.event
                def connect_error(data):
                    pass

                @self.sio.event
                def connect_timeout(data):
                    pass

                @self.sio.event
                def error(data):
                    pass

                @self.sio.event
                def disconnect():
                    self.sio_conectado = False
                    GLib.idle_add(self._atualizar_badge_sync)

                @self.sio.on("prateleira_lista")
                def on_prateleira(dados):
                    livros = (dados or {}).get("livros") or []
                    GLib.idle_add(lambda: self._receber_prateleira(livros))

                @self.sio.on("livro_resposta")
                def on_livro_resposta(dados):
                    livro = (dados or {}).get("livro")
                    if livro:
                        GLib.idle_add(lambda: self._aplicar_ficha(livro, abertura=True))

                @self.sio.on("livro_estado")
                def on_livro_estado(dados):
                    livro = (dados or {}).get("livro")
                    if livro:
                        GLib.idle_add(lambda: self._aplicar_ficha(livro, abertura=False))

                self.sio.connect(ORACLE_RELAY_URL, auth={"token": self.token}, wait_timeout=10)
            except Exception:
                self.sio_conectado = False

        threading.Thread(target=_conectar, daemon=True).start()

    # ── PRATELEIRA / FICHAS ──

    def _pedir_prateleira(self):
        if self.sio and self.sio_conectado:
            try:
                self.sio.emit("prateleira_listar", {"deviceId": DEVICE_ID})
            except Exception:
                pass

    def _ficha(self, book_id: str) -> Optional[Dict[str, Any]]:
        for livro in self.livros_lista:
            if livro.get("bookId") == book_id:
                return livro
        return None

    def _upsert_ficha(self, livro: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        book_id = livro.get("bookId")
        if not book_id:
            return None
        for i, local in enumerate(self.livros_lista):
            if local.get("bookId") == book_id:
                self.livros_lista[i] = livro
                return livro
        self.livros_lista.append(livro)
        return livro

    def _caminho_local(self, book_id: str, ext: Optional[str] = None) -> Optional[Path]:
        """Arquivo do livro nesta maquina: ~/SpookReader/<bookId>.<ext>."""
        try:
            if book_id and ext:
                p = SPOOK_READER_DIR / f"{book_id}.{ext.lower()}"
                if p.exists() and p.stat().st_size > 0:
                    return p
            if book_id:
                for f in SPOOK_READER_DIR.glob(f"{book_id}.*"):
                    if f.is_file() and f.stat().st_size > 0:
                        return f
        except Exception:
            pass
        return None

    def _receber_prateleira(self, livros: List[Dict[str, Any]]):
        for livro in livros or []:
            if isinstance(livro, dict) and livro.get("bookId"):
                self._upsert_ficha(livro)
        self._salvar_livros()
        self._carregar_biblioteca()
        self._agendar_downloads()

    def _aplicar_ficha(self, livro: Dict[str, Any], abertura: bool = False):
        book_id = livro.get("bookId")
        if not book_id:
            return
        self._upsert_ficha(livro)
        self._salvar_livros()

        ret = livro.get("retomar") or {}
        em_ret = int(ret.get("em") or 0)
        em_local = int((self.posicoes.get(book_id) or {}).get("updatedAt") or 0)
        if em_ret and em_ret >= em_local:
            self.posicoes[book_id] = {
                "page": ret.get("page", 0),
                "offsetY": ret.get("offsetY", 0.0),
                "percent": ret.get("percent", 0.0),
                "lastDevice": ret.get("device", ""),
                "updatedAt": em_ret,
            }
            self._salvar_posicoes()

        if self.livro_atual and self.livro_atual.get("bookId") == book_id:
            em = int(ret.get("em") or 0)
            dev = str(ret.get("device") or "")
            if abertura:
                if em > self._em_abertura and int(ret.get("page") or 0) != self.pagina_atual:
                    self._aplicar_posicao_remota(int(ret.get("page") or 0), dev)
                    self._pagina_abertura = self.pagina_atual
                self._em_abertura = max(self._em_abertura, em)
            elif (dev and dev != DEVICE_ID and em > max(self._em_abertura, self._ultimo_em_local)):
                # Auto-follow (pedido do Taylor): o outro aparelho virou a pagina,
                # este pula junto. _aplicar_posicao_remota NAO reemite, entao nao
                # ha ping-pong entre os dois.
                alvo = int(ret.get("page") or 0)
                if alvo != self.pagina_atual:
                    self._aplicar_posicao_remota(alvo, dev)
                self._em_abertura = max(self._em_abertura, em)
            elif dev == DEVICE_ID:
                self._esconder_chip_remoto()

        self._carregar_biblioteca()
        self._agendar_downloads()

    def _reenviar_posicoes_pendentes(self):
        """Ao reconectar: sobe posicoes locais que o relay ainda nao tem."""
        if not (self.sio and self.sio_conectado):
            return
        for book_id, pos in list(self.posicoes.items()):
            ficha = self._ficha(book_id) or {}
            ret = ficha.get("retomar") or {}
            if int(pos.get("updatedAt") or 0) > int(ret.get("em") or 0):
                try:
                    self.sio.emit("livro_atualizar", {
                        "bookId": book_id,
                        "deviceId": DEVICE_ID,
                        "page": pos.get("page", 0),
                        "offsetY": pos.get("offsetY", 0.0),
                        "percent": pos.get("percent", 0.0),
                        "em": int(pos.get("updatedAt") or 0),
                        "origem": "leitura",
                    })
                except Exception:
                    pass

    # ── DOWNLOAD / UPLOAD ──

    def _agendar_downloads(self):
        """Auto-download: toda ficha no Drive que nao existe aqui, baixa sozinha."""
        if not self.sio_conectado:
            return
        for ficha in list(self.livros_lista):
            book_id = ficha.get("bookId")
            if not book_id or ficha.get("removido"):
                continue
            if self._caminho_local(book_id, ficha.get("ext")):
                continue
            if not (ficha.get("arquivo") or {}).get("noDrive"):
                continue
            if book_id in self._baixando:
                continue
            self._baixando.add(book_id)
            threading.Thread(target=self._baixar_livro, args=(ficha,), daemon=True).start()

    def _http_atualizar(self, book_id: str, payload: Dict[str, Any]) -> bool:
        """Mesma semantica LWW do socket, mas por HTTP (socket da VM pode cair)."""
        try:
            import requests
            r = requests.post(
                f"{ORACLE_RELAY_URL}/livros/{book_id}",
                headers={"Authorization": f"Bearer {self.token}"},
                json=payload, timeout=(5, 20),
            )
            return bool(r.ok)
        except Exception:
            return False

    def _http_apagar(self, book_id: str) -> bool:
        try:
            import requests
            r = requests.delete(
                f"{ORACLE_RELAY_URL}/livros/{book_id}",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=(5, 20),
            )
            return bool(r.ok)
        except Exception:
            return False

    def _baixar_livro(self, ficha: Dict[str, Any], abrir: bool = False):
        book_id = ficha.get("bookId") or ""
        ext = (ficha.get("ext") or "pdf").lower()
        titulo = (ficha.get("titulo") or "")[:40]
        try:
            GLib.idle_add(lambda: self._set_status_sync(f"⇣ Baixando {titulo}..."))
            import requests
            url = f"{ORACLE_RELAY_URL}/livros/{book_id}/arquivo"
            destino = SPOOK_READER_DIR / f"{book_id}.{ext}"
            parcial = destino.with_suffix(destino.suffix + ".part")
            with requests.get(url, headers={"Authorization": f"Bearer {self.token}"},
                              stream=True, timeout=(10, 900)) as r:
                r.raise_for_status()
                with open(parcial, "wb") as f:
                    for pedaco in r.iter_content(256 * 1024):
                        if pedaco:
                            f.write(pedaco)
            if calcular_book_id(parcial) != book_id:
                parcial.unlink(missing_ok=True)
                raise RuntimeError("sha256 nao confere")
            os.replace(parcial, destino)
            ficha["arquivo"] = {**(ficha.get("arquivo") or {}), "noDrive": True}
            GLib.idle_add(lambda: self._set_status_sync(f"✓ {titulo} baixado"))
            GLib.idle_add(self._carregar_biblioteca)
            if abrir:
                GLib.idle_add(lambda: self._abrir_livro({**ficha, "caminho": str(destino)}))
        except Exception as e:
            if "parcial" in dir():
                try:
                    parcial.unlink(missing_ok=True)
                except Exception:
                    pass
            GLib.idle_add(lambda: self._set_status_sync(f"✕ download falhou: {e}"))
        finally:
            self._baixando.discard(book_id)

    def _publicar_livro(self, caminho: Path, titulo: str = ""):
        """Sobe o arquivo pro relay/Drive (auto-upload ao importar).

        Vai por HTTP de proposito: nao depende do socket da VM.
        """
        if not self.token:
            return

        def _up():
            try:
                import requests
                mime = "application/pdf" if caminho.suffix.lower() == ".pdf" else "application/epub+zip"
                with open(caminho, "rb") as f:
                    r = requests.post(
                        f"{ORACLE_RELAY_URL}/livros",
                        headers={"Authorization": f"Bearer {self.token}"},
                        files={"file": (caminho.name, f, mime)},
                        data={"deviceId": DEVICE_ID, "titulo": titulo or caminho.stem},
                        timeout=(10, 900),
                    )
                r.raise_for_status()
                GLib.idle_add(lambda: self._set_status_sync(f"✓ {titulo or caminho.stem} publicado"))
            except Exception as e:
                GLib.idle_add(lambda: self._set_status_sync(f"✕ publicar falhou: {e}"))

        threading.Thread(target=_up, daemon=True).start()

    def _atualizar_badge_sync(self):
        if self.status_biblioteca_label:
            if self.sio_conectado:
                self.status_biblioteca_label.set_text("● ORACLE RELAY ONLINE")
                self.status_biblioteca_label.remove_css_class("spook-err")
                self.status_biblioteca_label.add_css_class("spook-ok")
            else:
                self.status_biblioteca_label.set_text("○ ORACLE OFFLINE (MODO LOCAL)")
                self.status_biblioteca_label.remove_css_class("spook-ok")
                self.status_biblioteca_label.add_css_class("spook-err")

    def _aplicar_posicao_remota(self, pagina: int, dispositivo: str):
        """Pula para a pagina do outro aparelho SEM reemitir (evita o eco que
        apagava a posicao de quem estava lendo)."""
        if 0 <= pagina < self.total_paginas and pagina != self.pagina_atual:
            self._cancelar_sync()
            self.pagina_atual = pagina
            dev_str = "Poco X7" if "poco" in dispositivo.lower() else (dispositivo or "remoto")
            self._set_status_sync(f"Posição restaurada do {dev_str}: pág. {pagina + 1}")
            self._supress_sync += 1
            try:
                self._renderizar_pagina_atual()
            finally:
                self._supress_sync -= 1
            self._esconder_chip_remoto()

    def _notificar_remoto(self, msg: str):
        self._set_status_sync(f"● {msg}")

    def _set_status_sync(self, texto: str):
        if self.label_status_sync:
            self.label_status_sync.set_text(texto)

    def _carregar_posicoes(self) -> Dict[str, Dict[str, Any]]:
        try:
            if POSICOES_FILE.exists():
                return json.loads(POSICOES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _salvar_posicoes(self):
        try:
            POSICOES_FILE.write_text(json.dumps(self.posicoes, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _carregar_livros(self) -> List[Dict[str, Any]]:
        """Load persisted book list from local cache."""
        try:
            if LIVROS_FILE.exists():
                return json.loads(LIVROS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        return []

    def _salvar_livros(self):
        """Cache local da prateleira (modo offline). O relay e a fonte da verdade."""
        try:
            LIVROS_FILE.write_text(json.dumps(self.livros_lista, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _emitir_atualizacao(self):
        if not self.livro_atual:
            return
        book_id = self.livro_atual.get("bookId")
        if not book_id:
            return
        
        pct = (self.pagina_atual + 1) / self.total_paginas if self.total_paginas > 0 else 0.0
        agora = int(time.time() * 1000)
        self._ultimo_em_local = agora

        # Salva localmente (fallback offline)
        self.posicoes[book_id] = {
            "page": self.pagina_atual,
            "offsetY": 0.0,
            "percent": pct,
            "lastDevice": DEVICE_ID,
            "updatedAt": agora,
        }
        self._salvar_posicoes()

        # Bônus de interoperabilidade com o Okular
        self._atualizar_okular_docdata(self.livro_atual.get("caminho"), self.pagina_atual)

        # Ficha local: retomar vira a posicao global
        ficha = self._ficha(book_id)
        if ficha is not None:
            ficha["retomar"] = {"page": self.pagina_atual, "offsetY": 0.0, "percent": pct,
                                "device": DEVICE_ID, "em": agora}
        if self.sio and self.sio_conectado:
            payload = {
                "bookId": book_id,
                "deviceId": DEVICE_ID,
                "page": self.pagina_atual,
                "offsetY": 0.0,
                "percent": pct,
                "em": agora,
                "origem": "leitura",
                "titulo": self.livro_atual.get("titulo", ""),
                "ext": self.livro_atual.get("ext", ""),
                "tamanho": self.livro_atual.get("tamanho", 0),
            }
            try:
                self.sio.emit("livro_atualizar", payload)
                self._set_status_sync(f"✓ Relay: pág. {self.pagina_atual + 1}")
            except Exception:
                corpo = {k: v for k, v in payload.items() if k != "bookId"}
                if self._http_atualizar(book_id, corpo):
                    self._set_status_sync(f"✓ HTTP: pág. {self.pagina_atual + 1}")
                else:
                    self._set_status_sync(f"Offline: salvo localmente (pág. {self.pagina_atual + 1})")
        else:
            corpo = {k: v for k, v in payload.items() if k != "bookId"}
            if self._http_atualizar(book_id, corpo):
                self._set_status_sync(f"✓ HTTP: pág. {self.pagina_atual + 1}")
            else:
                self._set_status_sync(f"Offline: salvo localmente (pág. {self.pagina_atual + 1})")

    def _atualizar_okular_docdata(self, caminho_str: Optional[str], pagina: int):
        if not caminho_str:
            return
        try:
            p = Path(caminho_str)
            if not p.exists():
                return
            tamanho = p.stat().st_size
            docdata_dir = Path.home() / ".local" / "share" / "okular" / "docdata"
            docdata_dir.mkdir(parents=True, exist_ok=True)
            xml_path = docdata_dir / f"{tamanho}.{p.name}.xml"
            xml_content = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE documentInfo>
<documentInfo url="{p.absolute()}">
 <generalInfo>
  <history>
   <current viewport="{pagina};C2:0.5:0.0:1"/>
  </history>
  <views>
   <view name="PageView">
    <zoom mode="0" value="1.0"/>
   </view>
  </views>
 </generalInfo>
</documentInfo>
"""
            xml_path.write_text(xml_content, encoding="utf-8")
        except Exception:
            pass

    # ── BUILD READER PAGE ──

    def criar_pagina(self) -> Gtk.Widget:
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_transition_duration(180)

        self.box_biblioteca = self._construir_biblioteca()
        self.box_visualizador = self._construir_visualizador()

        self.stack.add_named(self.box_biblioteca, "biblioteca")
        self.stack.add_named(self.box_visualizador, "visualizador")
        self.stack.set_visible_child_name("biblioteca")

        return self.stack

    def _construir_biblioteca(self) -> Gtk.Widget:
        raiz = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        raiz.add_css_class("spook-root")

        # Header
        cabeca = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        tit_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        tit = Gtk.Label(label="SPOOK // READER", xalign=0)
        tit.add_css_class("spook-title")
        sub = Gtk.Label(label="CROSS-DEVICE READING POSITION SYNC", xalign=0)
        sub.add_css_class("spook-sub")
        tit_box.append(tit)
        tit_box.append(sub)
        cabeca.append(tit_box)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        cabeca.append(spacer)

        self.status_biblioteca_label = Gtk.Label(label="● ORACLE RELAY", xalign=1)
        self.status_biblioteca_label.add_css_class("spook-status")
        self.status_biblioteca_label.add_css_class("spook-ok")
        cabeca.append(self.status_biblioteca_label)

        btn_abrir = Gtk.Button(label="+ OPEN FILE")
        btn_abrir.add_css_class("spook-btn")
        btn_abrir.add_css_class("spook-accent")
        btn_abrir.connect("clicked", lambda _: self._escolher_arquivo_local())
        cabeca.append(btn_abrir)

        btn_gdrive = Gtk.Button(label="GOOGLE DRIVE")
        btn_gdrive.add_css_class("spook-btn")
        btn_gdrive.connect("clicked", lambda _: self._abrir_google_drive())
        cabeca.append(btn_gdrive)

        raiz.append(cabeca)

        # Library List (ScrolledWindow)
        scroll_livros = Gtk.ScrolledWindow()
        scroll_livros.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll_livros.set_vexpand(True)

        self.lista_livros_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroll_livros.set_child(self.lista_livros_box)
        raiz.append(scroll_livros)

        # Load saved positions and rebuild list
        GLib.idle_add(self._carregar_biblioteca)
        return raiz

    def _carregar_biblioteca(self):
        if not getattr(self, "lista_livros_box", None):
            return
        while (c := self.lista_livros_box.get_first_child()):
            self.lista_livros_box.remove(c)

        visiveis = [l for l in self.livros_lista if not l.get("removido")]
        if not visiveis:
            caixa_vazia = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            caixa_vazia.add_css_class("spook-card")
            l1 = Gtk.Label(label="No books in your reading list.", xalign=0)
            l1.add_css_class("spook-loc")
            l2 = Gtk.Label(label="Click '+ OPEN FILE' to add a PDF or EPUB from disk.", xalign=0)
            l2.add_css_class("spook-note")
            caixa_vazia.append(l1)
            caixa_vazia.append(l2)
            self.lista_livros_box.append(caixa_vazia)
            return

        for livro in visiveis:
            self._adicionar_livro_na_lista(livro)

    def _adicionar_livro_na_lista(self, livro: Dict[str, Any]):
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        card.add_css_class("spook-card")

        ext = (livro.get("ext") or "").lower()
        icone_tipo = Gtk.Label(label="[PDF]" if ext == "pdf" else "[EPUB]")
        icone_tipo.add_css_class("spook-status")
        icone_tipo.add_css_class("spook-warn" if ext == "pdf" else "spook-ok")
        card.append(icone_tipo)

        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        info_box.set_hexpand(True)

        tit = Gtk.Label(label=livro.get("titulo") or "(sem titulo)", xalign=0)
        tit.add_css_class("spook-loc")
        tit.set_ellipsize(Pango.EllipsizeMode.END)
        info_box.append(tit)

        sub_linha = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        book_id = livro.get("bookId", "")

        # Onde parei: ponteiro global (qualquer aparelho) + de quem foi
        ret = livro.get("retomar") or {}
        pag = int(ret.get("page") or 0)
        pct = float(ret.get("percent") or 0.0)
        dev = str(ret.get("device") or "")
        if ret.get("em"):
            dev_tag = "POCO" if "poco" in dev.lower() else ("ARCH" if "arch" in dev.lower() else dev[:8])
            txt = f"Page {pag + 1}"
            if pct > 0:
                txt += f" ({int(pct * 100)}%)"
            if dev_tag:
                txt += f" • {dev_tag}"
            prog_label = Gtk.Label(label=txt, xalign=0)
            prog_label.add_css_class("spook-status")
            prog_label.add_css_class("spook-ok")
            sub_linha.append(prog_label)

        # Disponibilidade local / na nuvem
        local = self._caminho_local(book_id, ext)
        if local is None:
            tag = Gtk.Label(label="⇣ BAIXANDO" if book_id in self._baixando else "☁ DRIVE", xalign=0)
            tag.add_css_class("spook-status")
            tag.add_css_class("spook-note")
            sub_linha.append(tag)

        chk_done = Gtk.CheckButton()
        chk_done.set_active(bool(livro.get("done")))
        chk_done.set_tooltip_text("Mark as done")
        chk_done.connect("toggled", lambda b, bid=book_id: self._toggle_done(bid, b.get_active()))
        sub_linha.append(chk_done)

        info_box.append(sub_linha)
        card.append(info_box)

        # O cartao inteiro abre o livro (o botao READ era redundante).
        # Checkbox e botao de remover continuam com o clique proprio deles.
        clique_card = Gtk.GestureClick()
        clique_card.set_button(1)
        clique_card.connect("released", lambda _g, _n, _x, _y: self._abrir_livro(livro))
        info_box.add_controller(clique_card)
        info_box.set_cursor_from_name("pointer")
        info_box.set_tooltip_text("Abrir este livro")

        btn_del = Gtk.Button(label="\ue92e")
        btn_del.add_css_class("spook-btn")
        btn_del.add_css_class("spook-msi-btn")
        btn_del.add_css_class("spook-msi-del")
        btn_del.set_tooltip_text("Remove from list")
        self._tint_button(btn_del, "spook-msi-del")
        btn_del.connect("clicked", lambda _, bid=book_id: self._remover_da_lista(bid))
        card.append(btn_del)

        self.lista_livros_box.append(card)

    def _tint_button(self, btn, classe):
        """GTK4: CSS color on Button node doesn't tint internal label.
        Apply class to child (Gtk.Label) so colors show properly."""
        child = btn.get_child()
        if child is not None:
            child.add_css_class(classe)

    def _toggle_done(self, book_id: str, done: bool):
        livro = self._ficha(book_id)
        if livro is not None:
            livro["done"] = done
            livro["doneEm"] = int(time.time() * 1000)
            livro["donePor"] = DEVICE_ID
        payload = {
            "deviceId": DEVICE_ID,
            "done": done, "doneEm": int(time.time() * 1000), "donePor": DEVICE_ID,
        }
        enviado = False
        if self.sio and self.sio_conectado:
            try:
                self.sio.emit("livro_atualizar", {"bookId": book_id, **payload})
                enviado = True
            except Exception:
                pass
        if not enviado:
            self._http_atualizar(book_id, payload)
        self._salvar_livros()

    def _remover_da_lista(self, book_id: str):
        """Remove da LISTA nos dois aparelhos. O arquivo continua no Drive."""
        self.livros_lista = [l for l in self.livros_lista if l.get("bookId") != book_id]
        enviado = False
        if self.sio and self.sio_conectado:
            try:
                self.sio.emit("livro_apagar", {"bookId": book_id, "deviceId": DEVICE_ID})
                enviado = True
            except Exception:
                pass
        if not enviado:
            self._http_apagar(book_id)
        self._salvar_livros()
        self._carregar_biblioteca()

    def _abrir_google_drive(self):
        """Prateleira do Drive: escolhe -> baixa -> abre aqui."""
        self._abrir_books_do_drive()

    def _abrir_drive_no_navegador(self):
        url = "https://drive.google.com/drive/folders/1aY8nMTCHQsB1-lS-o6UcmCZk4YDdWXSL"
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._set_status_sync("Opened Google Drive SPOOK folder")

    def _abrir_books_do_drive(self):
        """Mostra a prateleira (Drive) e abre/baixa o livro escolhido."""
        if not self.sio or not self.sio_conectado:
            self._set_status_sync("Oracle Relay offline")
            return
        self._pedir_prateleira()
        GLib.timeout_add(600, lambda: (self._mostrar_dialogo_drive(), False)[1])

    def _mostrar_dialogo_drive(self):
        livros = [l for l in self.livros_lista if not l.get("removido")]
        if not livros:
            self._set_status_sync("No books on the shelf yet")
            return

        win = Gtk.Window(title="SPOOK SHELF", transient_for=self.app.janela, modal=True)
        win.set_default_size(640, 520)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("spook-root")

        lab = Gtk.Label(label=f"SELECT A BOOK ({len(livros)})", xalign=0)
        lab.add_css_class("spook-title")
        box.append(lab)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        lista_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        for livro in livros:
            card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            card.add_css_class("spook-card")

            icone_tipo = Gtk.Label(label="[PDF]" if (livro.get("ext") or "") == "pdf" else "[EPUB]")
            icone_tipo.add_css_class("spook-status")
            icone_tipo.add_css_class("spook-warn" if (livro.get("ext") or "") == "pdf" else "spook-ok")
            card.append(icone_tipo)

            info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            info_box.set_hexpand(True)
            tit = Gtk.Label(label=livro.get("titulo", "Unknown"), xalign=0)
            tit.add_css_class("spook-loc")
            tit.set_ellipsize(Pango.EllipsizeMode.END)
            info_box.append(tit)

            sub = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            tam = livro.get("tamanho", 0) or 0
            tam_str = f"{tam/1024/1024:.1f} MB" if tam > 1024 * 1024 else f"{tam/1024:.0f} KB"
            sub.append(Gtk.Label(label=tam_str, xalign=0))
            ret = livro.get("retomar") or {}
            if ret.get("em"):
                sub.append(Gtk.Label(label=f"p.{int(ret.get('page') or 0) + 1}", xalign=0))
            if livro.get("done"):
                tag = Gtk.Label(label="DONE", xalign=0)
                tag.add_css_class("spook-ok")
                sub.append(tag)
            if self._caminho_local(livro.get("bookId"), livro.get("ext")):
                tag = Gtk.Label(label="LOCAL", xalign=0)
                tag.add_css_class("spook-ok")
                sub.append(tag)
            info_box.append(sub)
            card.append(info_box)

            btn = Gtk.Button(label="OPEN")
            btn.add_css_class("spook-btn")
            btn.add_css_class("spook-accent")
            btn.connect("clicked", lambda _, l=livro, w=win: (w.close(), self._abrir_livro(l)))
            card.append(btn)
            lista_box.append(card)

        scroll.set_child(lista_box)
        box.append(scroll)

        rodape = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_navegador = Gtk.Button(label="BROWSER ↗")
        btn_navegador.add_css_class("spook-btn")
        btn_navegador.connect("clicked", lambda _: self._abrir_drive_no_navegador())
        rodape.append(btn_navegador)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        rodape.append(spacer)

        btn_close = Gtk.Button(label="CLOSE")
        btn_close.add_css_class("spook-btn")
        btn_close.connect("clicked", lambda _: win.close())
        rodape.append(btn_close)
        box.append(rodape)

        win.set_child(box)
        win.present()

    def _escolher_arquivo_local(self):
        dialog = Gtk.FileDialog()
        dialog.open(self.app.janela, None, lambda d, res: self._on_arquivo_escolhido(d, res))

    def _on_arquivo_escolhido(self, dialog, res):
        """OPEN FILE: copia para ~/SpookReader/<bookId>.<ext> e publica no Drive."""
        try:
            f = dialog.open_finish(res)
            if not f:
                return
            caminho = Path(f.get_path())
            ext = caminho.suffix.lower().lstrip(".")
            if ext not in ("pdf", "epub"):
                return
            book_id = calcular_book_id(caminho)
            destino = SPOOK_READER_DIR / f"{book_id}.{ext}"
            if not destino.exists():
                shutil.copyfile(caminho, destino)
            livro = self._ficha(book_id) or {
                "bookId": book_id,
                "titulo": caminho.stem,
                "ext": ext,
                "tamanho": destino.stat().st_size,
                "done": False,
            }
            livro["titulo"] = livro.get("titulo") or caminho.stem
            livro["ext"] = ext
            livro["tamanho"] = destino.stat().st_size
            livro["removido"] = False
            self._upsert_ficha(livro)
            self._salvar_livros()
            self._publicar_livro(destino, titulo=livro["titulo"])
            GLib.idle_add(self._carregar_biblioteca)
            GLib.idle_add(lambda: self._abrir_livro(livro))
        except Exception:
            pass

    # ── DOCUMENT VIEWER ──

    def _construir_visualizador(self) -> Gtk.Widget:
        raiz = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        raiz.add_css_class("spook-root")

        # Top Toolbar
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.add_css_class("spook-reader-bar")
        toolbar.set_margin_bottom(6)

        btn_voltar = Gtk.Button(label="← LIBRARY")
        btn_voltar.add_css_class("spook-btn")
        btn_voltar.connect("clicked", lambda _: self._fechar_visualizador())
        toolbar.append(btn_voltar)

        self.label_titulo_leitor = Gtk.Label(label="", xalign=0)
        self.label_titulo_leitor.add_css_class("spook-reader-title")
        self.label_titulo_leitor.set_hexpand(True)
        self.label_titulo_leitor.set_ellipsize(Pango.EllipsizeMode.END)
        toolbar.append(self.label_titulo_leitor)

        btn_ant = Gtk.Button(label="▲ PREV")
        btn_ant.add_css_class("spook-btn")
        btn_ant.connect("clicked", lambda _: self._pagina_anterior())
        toolbar.append(btn_ant)

        self.label_pagina_leitor = Gtk.Button(label="Page 1 / 1")
        self.label_pagina_leitor.add_css_class("spook-btn")
        self.label_pagina_leitor.connect("clicked", lambda _: self._abrir_dialogo_pular_pagina())
        toolbar.append(self.label_pagina_leitor)

        btn_prox = Gtk.Button(label="NEXT ▼")
        btn_prox.add_css_class("spook-btn")
        btn_prox.connect("clicked", lambda _: self._proxima_pagina())
        toolbar.append(btn_prox)

        btn_zoom_out = Gtk.Button(label="−")
        btn_zoom_out.add_css_class("spook-btn")
        btn_zoom_out.connect("clicked", lambda _: self._alterar_zoom(-0.2))
        toolbar.append(btn_zoom_out)

        btn_zoom_in = Gtk.Button(label="+")
        btn_zoom_in.add_css_class("spook-btn")
        btn_zoom_in.connect("clicked", lambda _: self._alterar_zoom(0.2))
        toolbar.append(btn_zoom_in)

        btn_dark = Gtk.Button(label="🌙 TERMINAL")
        btn_dark.add_css_class("spook-btn")
        btn_dark.add_css_class("spook-ok-btn")
        btn_dark.connect("clicked", lambda b: self._toggle_modo_escuro(b))
        toolbar.append(btn_dark)

        btn_toc = Gtk.Button(label="📖 CONTENTS")
        btn_toc.add_css_class("spook-btn")
        btn_toc.connect("clicked", lambda _: self._abrir_sumario())
        toolbar.append(btn_toc)

        raiz.append(toolbar)

        # Progress Bar
        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_fraction(0.0)
        self.progress_bar.set_margin_bottom(6)
        raiz.append(self.progress_bar)

        # Page View Area (ScrolledWindow)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)

        caixa_imagem = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        caixa_imagem.set_halign(Gtk.Align.CENTER)
        caixa_imagem.set_valign(Gtk.Align.START)
        caixa_imagem.set_margin_top(8)
        caixa_imagem.set_margin_bottom(8)

        self.picture_pagina = Gtk.Picture()
        self.picture_pagina.set_can_shrink(True)
        caixa_imagem.append(self.picture_pagina)
        scroll.set_child(caixa_imagem)
        raiz.append(scroll)

        # Bottom Status Bar
        rodape = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        rodape.set_margin_top(4)

        self.label_status_sync = Gtk.Label(label="✓ SPOOK READER", xalign=0)
        self.label_status_sync.add_css_class("spook-status")
        self.label_status_sync.add_css_class("spook-ok")
        self.label_status_sync.set_hexpand(True)
        rodape.append(self.label_status_sync)

        # Chip "ARCH p.40 · IR": o outro aparelho virou a pagina neste livro.
        # NUNCA pula sozinho; o usuario decide.
        self.btn_chip_remoto = Gtk.Button(label="")
        self.btn_chip_remoto.add_css_class("spook-btn")
        self.btn_chip_remoto.add_css_class("spook-accent")
        self.btn_chip_remoto.set_visible(False)
        self.btn_chip_remoto.connect("clicked", lambda _b: self._ir_para_chip())
        rodape.append(self.btn_chip_remoto)

        raiz.append(rodape)

        # Keyboard shortcuts
        teclado = Gtk.EventControllerKey()
        teclado.connect("key-pressed", self._on_tecla_leitor)
        raiz.add_controller(teclado)

        return raiz

    def _on_tecla_leitor(self, _ctrl, keyval, _code, _state):
        if keyval in (Gdk.KEY_Page_Down, Gdk.KEY_Down, Gdk.KEY_j, Gdk.KEY_space):
            self._proxima_pagina()
            return True
        elif keyval in (Gdk.KEY_Page_Up, Gdk.KEY_Up, Gdk.KEY_k):
            self._pagina_anterior()
            return True
        elif keyval in (Gdk.KEY_plus, Gdk.KEY_equal):
            self._alterar_zoom(0.2)
            return True
        elif keyval == Gdk.KEY_minus:
            self._alterar_zoom(-0.2)
            return True
        elif keyval == Gdk.KEY_d:
            self._toggle_modo_escuro(None)
            return True
        elif keyval == Gdk.KEY_Home:
            self._ir_para_pagina(0)
            return True
        elif keyval == Gdk.KEY_End:
            self._ir_para_pagina(self.total_paginas - 1)
            return True
        elif keyval == Gdk.KEY_Escape:
            self._fechar_visualizador()
            return True
        return False

    def _abrir_livro(self, livro: Dict[str, Any]):
        """Abre o livro a partir do arquivo LOCAL (~/SpookReader/<bookId>.<ext>).
        Se ele nao existe aqui mas esta no Drive, baixa e reabre sozinho."""
        book_id = str(livro.get("bookId") or "")
        ext = (livro.get("ext") or "").lower()

        caminho = self._caminho_local(book_id, ext) if book_id else None
        if caminho is None:
            bruto = livro.get("caminho")
            if bruto:
                try:
                    p = Path(bruto)
                    if p.exists() and p.is_file():
                        caminho = p
                        if not book_id:
                            book_id = calcular_book_id(p)
                            livro["bookId"] = book_id
                            ext = p.suffix.lower().lstrip(".")
                except Exception:
                    caminho = None

        if caminho is None:
            if book_id and (livro.get("arquivo") or {}).get("noDrive"):
                if book_id not in self._baixando:
                    self._baixando.add(book_id)
                    self._set_status_sync(f"⇣ Baixando {(livro.get('titulo') or '')[:40]}...")
                    threading.Thread(target=self._baixar_livro, args=(livro, True), daemon=True).start()
            else:
                self._set_status_sync("Arquivo indisponivel aqui: use GOOGLE DRIVE ou OPEN FILE")
            return

        livro["caminho"] = str(caminho)
        livro["bookId"] = book_id
        self.livro_atual = livro

        try:
            if self.doc:
                self.doc.close()
            self.doc = fitz.open(str(caminho))
            self.total_paginas = len(self.doc)
        except Exception as e:
            self._set_status_sync(f"✕ falha ao abrir: {e}")
            return

        # Qual pagina abrir: o ponteiro global (retomar) manda quando for mais
        # novo que o cache local deste aparelho.
        ficha = self._ficha(book_id) or livro
        ret = ficha.get("retomar") or {}
        pos = self.posicoes.get(book_id) or {}
        em_ret = int(ret.get("em") or 0)
        em_pos = int(pos.get("updatedAt") or 0)
        if em_ret and em_ret >= em_pos:
            pag_inicial = int(ret.get("page") or 0)
            self._em_abertura = em_ret
            dev = str(ret.get("device") or "")
        else:
            pag_inicial = int(pos.get("page") or 0)
            self._em_abertura = em_pos
            dev = DEVICE_ID if pos else ""

        self.pagina_atual = min(max(0, pag_inicial), max(0, self.total_paginas - 1))
        self._pagina_abertura = self.pagina_atual

        self.label_titulo_leitor.set_text(livro.get("titulo") or caminho.stem)
        self.stack.set_visible_child_name("visualizador")

        self._cancelar_sync()
        self._esconder_chip_remoto()
        if dev and dev != DEVICE_ID:
            dev_tag = "POCO" if "poco" in dev.lower() else ("ARCH" if "arch" in dev.lower() else dev[:10])
            self._set_status_sync(f"Abrindo na pág. {self.pagina_atual + 1} (de {dev_tag})")
        else:
            self._set_status_sync("Loading document...")

        self._supress_sync += 1
        try:
            self._renderizar_pagina_atual()
        finally:
            self._supress_sync -= 1

        if self.sio and self.sio_conectado:
            try:
                self.sio.emit("livro_puxar", {"bookId": book_id})
            except Exception:
                pass

    def _fechar_visualizador(self):
        self._cancelar_sync()
        if self.livro_atual and self.pagina_atual != self._pagina_abertura:
            self._emitir_atualizacao()
        self._esconder_chip_remoto()
        if self.doc:
            self.doc.close()
            self.doc = None
        self.livro_atual = None
        self.stack.set_visible_child_name("biblioteca")
        self._carregar_biblioteca()

    def _renderizar_pagina_atual(self):
        if not self.doc or self.pagina_atual < 0 or self.pagina_atual >= self.total_paginas:
            return

        pct = (self.pagina_atual + 1) / self.total_paginas if self.total_paginas > 0 else 0.0
        self.label_pagina_leitor.set_label(f"Page {self.pagina_atual + 1} / {self.total_paginas}")
        self.progress_bar.set_fraction(pct)

        def _bg_render():
            try:
                page = self.doc[self.pagina_atual]
                mat = fitz.Matrix(self.zoom, self.zoom)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                if self.modo_escuro:
                    pix.invert_irect()
                png_bytes = pix.tobytes("png")
                gbytes = GLib.Bytes.new(png_bytes)
                texture = Gdk.Texture.new_from_bytes(gbytes)
                GLib.idle_add(lambda: self.picture_pagina.set_paintable(texture))
            except Exception:
                pass

        threading.Thread(target=_bg_render, daemon=True).start()

        # Trigger sync with debounce
        self._agendar_sync()

    def _agendar_sync(self):
        # Render de RESTAURACAO nao pode reemitir: era isso que apagava a
        # posicao de quem estava lendo no outro aparelho.
        if self._supress_sync > 0:
            return
        self._cancelar_sync()
        self.debounce_timer = GLib.timeout_add(1500, self._on_sync_timer)

    def _cancelar_sync(self):
        if self.debounce_timer:
            try:
                GLib.source_remove(self.debounce_timer)
            except Exception:
                pass
            self.debounce_timer = None

    # ── chip de posicao remota ──

    def _mostrar_chip_remoto(self, ret: Dict[str, Any]):
        if not getattr(self, "btn_chip_remoto", None):
            return
        dev = str(ret.get("device") or "")
        if "arch" in dev.lower():
            tag = "ARCH"
        elif "poco" in dev.lower():
            tag = "POCO"
        else:
            tag = dev[:8].upper() or "REMOTO"
        pag = int(ret.get("page") or 0) + 1
        self._chip_alvo = pag - 1
        self.btn_chip_remoto.set_label(f"{tag} p.{pag} · IR")
        self.btn_chip_remoto.set_tooltip_text(f"{dev} esta na pagina {pag}")
        self.btn_chip_remoto.set_visible(True)

    def _esconder_chip_remoto(self):
        self._chip_alvo = None
        if getattr(self, "btn_chip_remoto", None):
            self.btn_chip_remoto.set_visible(False)

    def _ir_para_chip(self):
        alvo = self._chip_alvo
        if alvo is None:
            return
        self._ir_para_pagina(alvo)     # vira leitura real: este aparelho assume
        self._esconder_chip_remoto()

    def _on_sync_timer(self):
        self.debounce_timer = None
        self._emitir_atualizacao()
        return False

    def _proxima_pagina(self):
        if self.pagina_atual < self.total_paginas - 1:
            self.pagina_atual += 1
            self._renderizar_pagina_atual()

    def _pagina_anterior(self):
        if self.pagina_atual > 0:
            self.pagina_atual -= 1
            self._renderizar_pagina_atual()

    def _ir_para_pagina(self, num: int):
        if 0 <= num < self.total_paginas:
            self.pagina_atual = num
            self._renderizar_pagina_atual()

    def _alterar_zoom(self, delta: float):
        novo = max(0.5, min(3.0, self.zoom + delta))
        if novo != self.zoom:
            self.zoom = novo
            self._renderizar_pagina_atual()

    def _toggle_modo_escuro(self, botao: Optional[Gtk.Button]):
        self.modo_escuro = not self.modo_escuro
        if botao:
            botao.set_label("🌙 TERMINAL" if self.modo_escuro else "☀️ LIGHT")
            if self.modo_escuro:
                botao.remove_css_class("spook-btn")
                botao.add_css_class("spook-ok-btn")
            else:
                botao.remove_css_class("spook-ok-btn")
                botao.add_css_class("spook-btn")
        self._renderizar_pagina_atual()

    def _abrir_dialogo_pular_pagina(self):
        win = Gtk.Window(title="GO TO PAGE", transient_for=self.app.janela, modal=True)
        win.set_default_size(300, 140)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("spook-root")

        lab = Gtk.Label(label=f"Enter page number (1 to {self.total_paginas}):", xalign=0)
        lab.add_css_class("spook-note")
        box.append(lab)

        entry = Gtk.Entry()
        entry.add_css_class("spook-input")
        entry.set_text(str(self.pagina_atual + 1))
        box.append(entry)

        row = Gtk.Box(spacing=8)
        btn_ir = Gtk.Button(label="GO")
        btn_ir.add_css_class("spook-btn")
        btn_ir.add_css_class("spook-accent")

        def _pular():
            try:
                val = int(entry.get_text().strip())
                if 1 <= val <= self.total_paginas:
                    self._ir_para_pagina(val - 1)
            except Exception:
                pass
            win.close()

        btn_ir.connect("clicked", lambda _: _pular())
        entry.connect("activate", lambda _: _pular())

        btn_cancel = Gtk.Button(label="CANCEL")
        btn_cancel.add_css_class("spook-btn")
        btn_cancel.connect("clicked", lambda _: win.close())

        row.append(btn_ir)
        row.append(btn_cancel)
        box.append(row)

        win.set_child(box)
        win.present()

    def _abrir_sumario(self):
        if not self.doc:
            return
        toc = self.doc.get_toc()
        if not toc:
            self._set_status_sync("This document has no table of contents.")
            return

        win = Gtk.Window(title="TABLE OF CONTENTS", transient_for=self.app.janela, modal=True)
        win.set_default_size(500, 450)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("spook-root")

        lab = Gtk.Label(label="SELECT A CHAPTER:", xalign=0)
        lab.add_css_class("spook-title")
        box.append(lab)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        lista_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for item in toc:
            # item = [level, title, page_number]
            nivel, titulo, pag = item[0], item[1], item[2]
            indent = "  " * (nivel - 1)
            btn = Gtk.Button(label=f"{indent}• {titulo} (page {pag})")
            btn.add_css_class("spook-btn")
            btn.connect("clicked", lambda _, p=pag: (self._ir_para_pagina(p - 1), win.close()))
            lista_box.append(btn)

        scroll.set_child(lista_box)
        box.append(scroll)

        btn_close = Gtk.Button(label="CLOSE")
        btn_close.add_css_class("spook-btn")
        btn_close.connect("clicked", lambda _: win.close())
        box.append(btn_close)

        win.set_child(box)
        win.present()
