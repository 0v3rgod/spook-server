#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SPOOK // PAD — notas cross-device (Arch <-> Poco), no espirito do Sync do Obsidian.

Mesmo desenho do app Android:
  * uma nota = um JSON no relay (`gdrive:SPOOK/pad/<id>.json`, espelho em
    `data/pad/` na VM); o relay e' a fonte da verdade e NUNCA mescla;
  * sha256 do texto = identidade da versao;
  * `base_hash` = a versao em que eu me baseei; se o relay ja mudou, e'
    conflito e o merge de 3 vias (base, meu, dele) acontece AQUI;
  * historico de versoes + lapide (apagar marca, o arquivo fica).

Transporte: HTTP no relay (o socket do app morre no background da MIUI; para o
Arch o HTTP tambem e' mais simples). O poll da lista roda a cada 20 s.
"""

import datetime
import hashlib
import json
import threading
import time
import uuid
from pathlib import Path

import requests

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import GLib, Gtk, Pango

# ============================== CONFIG ==============================

RELAY_URL = "http://100.70.88.101:5000"
TOKEN_FILE = Path.home() / ".pentestlink" / "token_sessao.txt"
CACHE_DIR = Path.home() / ".cache" / "spook-pad"
NOTAS_FILE = CACHE_DIR / "notas.json"

DEVICE_ID = "arch-desktop"

# paleta neon do app (uma cor estavel por nota)
CORES = [
    "#5CE1E6",  # ciano
    "#FF4D9A",  # rosa
    "#B44AFF",  # violeta
    "#FF7A45",  # laranja
    "#8BE04E",  # lima
    "#FFD54F",  # ambar
    "#4FA3FF",  # azul
    "#FF5C6C",  # vermelho
]

CSS_PAD = """
.pad-card {
  background: #16141F; border-radius: 18px; border: 1px solid #3A3648;
  padding: 12px 16px; transition: all 140ms ease;
}
.pad-card:hover { background: #1E1C2A; border-color: #6E6A81; }
.pad-card-atual { background: #1B1826; border-color: #FF4D9A; }
.pad-cor-0 { border-left: 4px solid #5CE1E6; }
.pad-cor-1 { border-left: 4px solid #FF4D9A; }
.pad-cor-2 { border-left: 4px solid #B44AFF; }
.pad-cor-3 { border-left: 4px solid #FF7A45; }
.pad-cor-4 { border-left: 4px solid #8BE04E; }
.pad-cor-5 { border-left: 4px solid #FFD54F; }
.pad-cor-6 { border-left: 4px solid #4FA3FF; }
.pad-cor-7 { border-left: 4px solid #FF5C6C; }
.pad-bar-0 { background: #5CE1E6; min-height: 2px; }
.pad-bar-1 { background: #FF4D9A; min-height: 2px; }
.pad-bar-2 { background: #B44AFF; min-height: 2px; }
.pad-bar-3 { background: #FF7A45; min-height: 2px; }
.pad-bar-4 { background: #8BE04E; min-height: 2px; }
.pad-bar-5 { background: #FFD54F; min-height: 2px; }
.pad-bar-6 { background: #4FA3FF; min-height: 2px; }
.pad-bar-7 { background: #FF5C6C; min-height: 2px; }
.pad-ed-titulo { font-size: 8px; font-weight: 700; color: #FF4D9A; }
.pad-titulo-card { font-size: 14px; font-weight: 700; color: #F4F1FF; }
.pad-previa {
  font-family: "JetBrains Mono", "JetBrainsMono NF", monospace;
  font-size: 11px; color: #B8B4C8;
}
.pad-meta {
  font-family: "JetBrains Mono", "JetBrainsMono NF", monospace;
  font-size: 9px; color: #8A8F98;
}
.pad-chip {
  font-family: "JetBrains Mono", "JetBrainsMono NF", monospace;
  font-size: 9px; font-weight: 700; border-radius: 6px; padding: 1px 6px;
}
.pad-chip-pend { color: #FFD54F; background-color: rgba(255,213,79,0.14); }
.pad-chip-ok   { color: #5CE1E6; background-color: rgba(92,225,230,0.12); }
.pad-chip-lapide { color: #FF5C6C; background-color: rgba(255,92,108,0.14); }
entry.pad-titulo {
  background: transparent; border: none; box-shadow: none;
  color: #F4F1FF; font-size: 18px; font-weight: 700; padding: 2px 0;
}
entry.pad-titulo:focus { box-shadow: none; }
textview.pad-texto, textview.pad-texto text {
  background: #0B0A12; color: #E7E3F5;
  font-family: "JetBrains Mono", "JetBrainsMono NF", monospace;
  font-size: 13px;
}
.pad-sep { background: #2A2736; min-height: 1px; }
.pad-vazio { font-size: 12px; color: #6E6A81; }
.pad-hist-row { background: #16141F; border-radius: 12px; border: 1px solid #2A2736; padding: 8px 12px; }
.pad-hist-txt {
  font-family: "JetBrains Mono", "JetBrainsMono NF", monospace;
  font-size: 10px; color: #B8B4C8;
}
/* ---- NOTAS | TAREFAS ---- */
button.pad-seg-btn {
  background: transparent; background-image: none; color: #8A8F98;
  border: 1px solid #3A3648; border-radius: 999px;
  padding: 6px 16px; font-size: 10px; font-weight: 700;
  box-shadow: none; outline: none;
}
button.pad-seg-btn:hover {
  background: transparent; background-image: none; color: #F4F1FF;
  border-color: #6E6A81; box-shadow: none;
}
button.pad-seg-on {
  background: rgba(255,77,154,0.14); background-image: none;
  color: #FF4D9A; border: 1px solid rgba(255,77,154,0.55);
  padding: 6px 16px; font-size: 10px; font-weight: 700;
  box-shadow: none; outline: none;
}
button.pad-seg-on:hover {
  background: rgba(255,77,154,0.20); color: #FF7AB0; border-color: #FF4D9A;
  box-shadow: none;
}
.pad-task {
  background: #16141F; border-radius: 16px; border: 1px solid #3A3648;
  padding: 8px 12px; transition: all 140ms ease;
}
.pad-task:hover { background: #1E1C2A; border-color: #6E6A81; }
.pad-task-feita { background: #121019; border-color: #2A2736; }
.pad-task-tit { font-size: 13px; font-weight: 700; color: #F4F1FF; }
.pad-task-feita .pad-task-tit { color: #6E6A81; }
.pad-task-meta {
  font-family: "JetBrains Mono", "JetBrainsMono NF", monospace;
  font-size: 9px; color: #8A8F98;
}
.pad-task-chip {
  font-family: "JetBrains Mono", "JetBrainsMono NF", monospace;
  font-size: 8px; font-weight: 700; border-radius: 999px; padding: 1px 8px;
}
.pad-task-chip-poco { color: #FF4D9A; background-color: rgba(255,77,154,0.14); }
.pad-task-chip-arch { color: #5CE1E6; background-color: rgba(92,225,230,0.12); }
.pad-task-vazio { font-size: 12px; color: #6E6A81; }
progressbar.pad-prog > trough {
  background: #241F31; border-radius: 999px; min-height: 4px; border: none;
}
progressbar.pad-prog > trough > progress {
  background: #8BE04E; border-radius: 999px; min-height: 4px; border: none;
}
"""

# ============================== HELPERS ==============================

def _agora() -> int:
    return int(time.time() * 1000)


def _hash(texto: str) -> str:
    return hashlib.sha256((texto or "").encode("utf-8")).hexdigest()


def carimbo(ms: int) -> str:
    """epoch ms -> '27/10 23:12' na hora local."""
    try:
        return datetime.datetime.fromtimestamp(int(ms) / 1000).strftime("%d/%m %H:%M")
    except Exception:
        return "--"


def carimbo_conflito(ms: int = None) -> str:
    ms = ms or _agora()
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y%m%d%H%M")


def cor_da_nota(nid: str) -> int:
    """Cor estavel por id (mesma ideia do corDaNota() do app)."""
    h = hashlib.sha256((nid or "").encode()).digest()
    return h[0] % len(CORES)


def carregar_token() -> str:
    try:
        if TOKEN_FILE.exists():
            return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def _int(v, padrao=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return padrao


# ========================= MERGE DE 3 VIAS =========================
# Porte fiel do PadStore.kt (app): diff por LINHAS com LCS, hunks que nao se
# cruzam entram os dois; quando se cruzam fica o MEU trecho + marca conflito
# (a versao que perdeu vira uma nota "Conflicted copy").

class Hunk:
    __slots__ = ("inicio", "fim", "linhas")

    def __init__(self, inicio, fim, linhas):
        self.inicio = inicio
        self.fim = fim
        self.linhas = linhas

    def __repr__(self):
        return f"Hunk({self.inicio},{self.fim},{len(self.linhas)}l)"


def _hunks(base, novo):
    """Diff base -> novo em hunks (regioes da base substituidas)."""
    ini = 0
    while ini < len(base) and ini < len(novo) and base[ini] == novo[ini]:
        ini += 1
    fim_b, fim_n = len(base), len(novo)
    while fim_b > ini and fim_n > ini and base[fim_b - 1] == novo[fim_n - 1]:
        fim_b -= 1
        fim_n -= 1
    b, n = base[ini:fim_b], novo[ini:fim_n]
    if not b and not n:
        return []
    # insercao/remocao pura ou diff grande demais: um hunk so
    if not b or not n or len(b) * len(n) > 4_000_000:
        return [Hunk(ini, fim_b, list(n))]

    m, k = len(b), len(n)
    dp = [[0] * (k + 1) for _ in range(m + 1)]
    for i in range(m - 1, -1, -1):
        linha = dp[i]
        prox = dp[i + 1]
        for j in range(k - 1, -1, -1):
            linha[j] = prox[j + 1] + 1 if b[i] == n[j] else max(prox[j], linha[j + 1])

    saida = []
    i = j = 0
    inicio_hunk = -1
    buffer = []

    def fecha():
        nonlocal inicio_hunk, buffer
        if inicio_hunk >= 0:
            saida.append(Hunk(ini + inicio_hunk, ini + i, list(buffer)))
            inicio_hunk = -1
            buffer = []

    while i < m and j < k:
        if b[i] == n[j]:
            fecha()
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            if inicio_hunk < 0:
                inicio_hunk = i
            i += 1
        else:
            if inicio_hunk < 0:
                inicio_hunk = i
            buffer.append(n[j])
            j += 1
    while j < k:
        if inicio_hunk < 0:
            inicio_hunk = i
        buffer.append(n[j])
        j += 1
    fecha()
    if i < m:
        saida.append(Hunk(ini + i, ini + m, []))
    return saida


def mesclar(base_txt: str, meu_txt: str, dele_txt: str):
    """Devolve (texto_fundido, brigou, hunks_aplicados)."""
    meu_txt = meu_txt or ""
    dele_txt = dele_txt or ""
    base_txt = base_txt or ""

    if meu_txt == dele_txt:
        return meu_txt, False, 0
    if meu_txt == base_txt:
        return dele_txt, False, 0
    if dele_txt == base_txt:
        return meu_txt, False, 0

    base = base_txt.split("\n")
    meus = _hunks(base, meu_txt.split("\n"))
    deles = _hunks(base, dele_txt.split("\n"))
    if not meus:
        return dele_txt, False, 0
    if not deles:
        return meu_txt, False, 0

    todos = [(h, True) for h in meus] + [(h, False) for h in deles]
    todos.sort(key=lambda t: (t[0].inicio, 0 if t[1] else 1))

    saida = []
    cursor = 0
    conflito = False
    aplicados = 0
    grupo = []  # [(hunk, is_meu)]

    def fecha_grupo():
        nonlocal conflito, aplicados, grupo
        if not grupo:
            return
        meus_g = [t for t in grupo if t[1]]
        deles_g = [t for t in grupo if not t[1]]
        # dois "append" no MESMO ponto (diario, lista): ficam os dois
        if meus_g and deles_g and all(h.inicio == h.fim for h, _ in grupo):
            for h, _ in sorted(meus_g, key=lambda t: t[0].inicio):
                saida.extend(h.linhas)
            for h, _ in sorted(deles_g, key=lambda t: t[0].inicio):
                saida.extend(h.linhas)
            aplicados += len(grupo)
            grupo = []
            return
        escolhidos = meus_g if meus_g else deles_g
        if meus_g and deles_g:
            a = [l for h, _ in meus_g for l in h.linhas]
            b = [l for h, _ in deles_g for l in h.linhas]
            if a != b:
                conflito = True
        aplicados += len(escolhidos)
        for h, _ in sorted(escolhidos, key=lambda t: t[0].inicio):
            saida.extend(h.linhas)
        grupo = []

    for h, meu_lado in todos:
        if not grupo:
            for i in range(cursor, min(h.inicio, len(base))):
                saida.append(base[i])
            grupo.append((h, meu_lado))
            cursor = h.fim
            continue
        fim_grupo = max(x.fim for x, _ in grupo)
        if h.inicio < fim_grupo:
            grupo.append((h, meu_lado))
            cursor = max(cursor, h.fim)
        else:
            fecha_grupo()
            for i in range(cursor, min(h.inicio, len(base))):
                saida.append(base[i])
            grupo.append((h, meu_lado))
            cursor = h.fim
    fecha_grupo()
    for i in range(cursor, len(base)):
        saida.append(base[i])

    return "\n".join(saida), conflito, aplicados


# ============================== UI ==============================

class SpookPadManager:
    def __init__(self, app):
        self.app = app
        self.notas = {}
        self._lock = threading.RLock()
        self.token = carregar_token()
        self._sincronizando = False
        self._ultimo_sync = 0
        self._erro = ""
        self._filtro = ""
        self._css_instalado = False

        # widgets
        self.stack = None
        self.lista_box = None
        self.busca = None
        self.lbl_count = None
        self.lbl_status = None

        # tarefas (a mesma lista compartilhada do app/overlay antigo)
        self.pilha_vistas = None
        self.seg_notas = None
        self.seg_tarefas = None
        self.tasks_box = None
        self.task_entry = None
        self.lbl_tasks = None
        self.prog_tasks = None
        self.tasks = []
        self.task_listar_cb = None
        self.task_add_cb = None
        self.task_toggle_cb = None
        self.task_del_cb = None

        # editor
        self.ed_id = None
        self.ed_titulo = None
        self.ed_texto = None
        self.ed_status = None
        self.ed_rodape = None
        self.ed_sujo = False
        self._ed_timer = None
        self._buffer_guard = False

        self._carregar()

    # ------------------------------------------------------------ cache local
    def _carregar(self):
        try:
            if NOTAS_FILE.exists():
                d = json.loads(NOTAS_FILE.read_text(encoding="utf-8"))
                for n in d.get("notas", []):
                    if n.get("id"):
                        self.notas[n["id"]] = n
        except Exception:
            self.notas = {}

    def _gravar(self):
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = NOTAS_FILE.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"notas": list(self.notas.values())}, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(NOTAS_FILE)
        except Exception:
            pass

    # --------------------------------------------------------------- modelo
    def _nota_vazia(self, nid, titulo, texto=""):
        t = _agora()
        return {
            "id": nid,
            "title": (titulo or "").strip() or "untitled",
            "texto": texto or "",
            "hash": _hash(texto or ""),
            "em": t,
            "device": DEVICE_ID,
            "criado_em": t,
            "versao": 1,
            "removido": False,
            "removido_em": 0,
            "n_versoes": 0,
            "historico": [],
            "base_hash": "",
            "base_texto": "",
            "pendente": True,
        }

    def criar_nota(self, titulo="untitled", texto=""):
        with self._lock:
            nota = self._nota_vazia(str(uuid.uuid4()), titulo, texto)
            self.notas[nota["id"]] = nota
            self._gravar()
        return nota

    def editar_local(self, nid, titulo, texto):
        with self._lock:
            atual = self.notas.get(nid)
            if not atual:
                return None
            h = _hash(texto)
            tit = (titulo or "").strip() or "untitled"
            if h == atual.get("hash") and tit == atual.get("title"):
                return atual
            atual.update(
                titulo=tit, texto=texto or "", hash=h, em=_agora(),
                device=DEVICE_ID, pendente=True, removido=False, removido_em=0,
            )
            self._gravar()
            return dict(atual)

    def apagar_local(self, nid):
        with self._lock:
            atual = self.notas.get(nid)
            if not atual:
                return None
            atual.update(removido=True, removido_em=_agora(), em=_agora(),
                         device=DEVICE_ID, pendente=True)
            self._gravar()
            return dict(atual)

    def marcar_enviada(self, nid, remoto):
        with self._lock:
            local = self.notas.get(nid)
            if local is None:
                local = {"id": nid, "historico": []}
                self.notas[nid] = local
            for campo in ("title", "texto", "hash", "em", "device", "criado_em",
                          "versao", "removido", "removido_em", "n_versoes"):
                if campo in remoto:
                    local[campo] = remoto[campo]
            local["base_hash"] = remoto.get("hash", local.get("hash", ""))
            local["base_texto"] = remoto.get("texto", local.get("texto", ""))
            local["pendente"] = False
            if remoto.get("historico"):
                local["historico"] = remoto["historico"]
            self._gravar()
            return local

    def aplicar_remoto(self, remoto):
        """Porte do PadStore.aplicarRemoto(): pode deixar a nota `pendente`."""
        nid = remoto.get("id")
        if not nid:
            return None
        with self._lock:
            local = self.notas.get(nid)
            rem_hash = remoto.get("hash", "")
            rem_removido = bool(remoto.get("removido"))

            if local is None:
                nova = dict(remoto)
                nova.setdefault("historico", [])
                nova["base_hash"] = rem_hash
                nova["base_texto"] = remoto.get("texto", "")
                nova["pendente"] = False
                self.notas[nid] = nova
                self._gravar()
                return nova

            if rem_hash == local.get("hash") and not rem_removido:
                local.update(
                    em=_int(remoto.get("em"), local.get("em", 0)),
                    versao=_int(remoto.get("versao"), local.get("versao", 0)),
                    n_versoes=_int(remoto.get("n_versoes"), local.get("n_versoes", 0)),
                    pendente=False, base_hash=rem_hash,
                    base_texto=remoto.get("texto", ""),
                )
                self._gravar()
                return local

            if rem_removido:
                # eu estava editando quando ele apagou: minha edicao ressuscita
                if local.get("pendente") and local.get("hash") != rem_hash:
                    return local
                local.update(
                    removido=True, removido_em=remoto.get("removido_em", 0),
                    em=_int(remoto.get("em"), local.get("em", 0)),
                    versao=_int(remoto.get("versao"), local.get("versao", 0)),
                    pendente=False, base_hash=rem_hash,
                    base_texto=remoto.get("texto", ""),
                )
                self._gravar()
                return local

            if not local.get("pendente"):
                local.update(
                    titulo=remoto.get("title", local.get("title")),
                    texto=remoto.get("texto", ""),
                    hash=rem_hash,
                    em=_int(remoto.get("em"), local.get("em", 0)),
                    device=remoto.get("device", ""),
                    versao=_int(remoto.get("versao"), local.get("versao", 0)),
                    removido=False, removido_em=0,
                    n_versoes=_int(remoto.get("n_versoes"), local.get("n_versoes", 0)),
                    base_hash=rem_hash, base_texto=remoto.get("texto", ""),
                    pendente=False,
                )
                if remoto.get("historico"):
                    local["historico"] = remoto["historico"]
                self._gravar()
                return local

            # local pendente: ele mudou desde a minha base?
            if rem_hash == local.get("base_hash"):
                return local

            base_txt = local.get("base_texto") or remoto.get("texto", "")
            fundido, brigou, _ = mesclar(base_txt, local.get("texto", ""), remoto.get("texto", ""))
            local.update(
                texto=fundido, hash=_hash(fundido), em=_agora(), device=DEVICE_ID,
                base_hash=rem_hash, base_texto=remoto.get("texto", ""),
                versao=max(_int(local.get("versao"), 1), _int(remoto.get("versao"), 1)),
                pendente=True,
            )
            if fundido != local.get("texto") and local.get("title") == "untitled":
                local["title"] = remoto.get("title") or local.get("title")
            self._gravar()

            if brigou and remoto.get("texto") and remoto.get("texto") != fundido:
                copia = self._nota_vazia(
                    str(uuid.uuid4()),
                    f'{local.get("title")} (Conflicted copy '
                    f'{remoto.get("device") or DEVICE_ID} {carimbo_conflito()})',
                    remoto.get("texto", ""),
                )
                self.notas[copia["id"]] = copia
                self._gravar()
            return local

    # ---------------------------------------------------------------- rede
    def _http(self, metodo, rota, corpo=None, timeout=8):
        url = RELAY_URL.rstrip("/") + rota
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            if metodo == "GET":
                r = requests.get(url, headers=headers, timeout=timeout)
            elif metodo == "POST":
                r = requests.post(url, headers=headers, json=corpo or {}, timeout=timeout)
            elif metodo == "DELETE":
                r = requests.delete(url, headers=headers, timeout=timeout)
            else:
                return None
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:
            return None

    def sincronizar(self, forcar=False):
        if self._sincronizando:
            return
        self._sincronizando = True
        threading.Thread(target=self._sync_thread, daemon=True).start()

    def _sync_thread(self):
        pendentes_antes = 0
        try:
            self.token = carregar_token() or self.token
            res = self._http("GET", "/pad", timeout=10)
            if not res or not res.get("ok"):
                self._erro = "relay offline"
                GLib.idle_add(self._pos_sync)
                return
            self._erro = ""
            self._ultimo_sync = _agora()

            for remoto in res.get("notas") or []:
                self.aplicar_remoto(remoto)

            with self._lock:
                pendentes = [n["id"] for n in self.notas.values() if n.get("pendente")]
                pendentes_antes = len(pendentes)
            for nid in pendentes:
                with self._lock:
                    removida = bool((self.notas.get(nid) or {}).get("removido"))
                if removida:
                    self._apagar_remoto(nid)
                else:
                    self._empurrar(nid)
        except Exception as e:
            self._erro = str(e)[:80]
        finally:
            self._sincronizando = False
            GLib.idle_add(self._pos_sync, pendentes_antes)

    def _empurrar(self, nid, profundidade=0):
        with self._lock:
            n = self.notas.get(nid)
            if not n:
                return
            corpo = {
                "title": n.get("title", ""),
                "texto": n.get("texto", ""),
                "em": _int(n.get("em"), _agora()),
                "base_hash": n.get("base_hash", ""),
                "device": DEVICE_ID,
            }
            local_texto = n.get("texto", "")

        res = self._http("POST", f"/pad/{nid}", corpo, timeout=20)
        if not res or not res.get("ok"):
            return  # fica pendente; o proximo sync tenta de novo

        if res.get("conflito") and profundidade < 3:
            remoto = res.get("nota") or {}
            with self._lock:
                local = self.notas.get(nid) or {}
                base_txt = local.get("base_texto") or remoto.get("texto", "")
                fundido, brigou, _ = mesclar(base_txt, local_texto, remoto.get("texto", ""))
                local.update(
                    texto=fundido, hash=_hash(fundido), em=_agora(), device=DEVICE_ID,
                    base_hash=remoto.get("hash", ""),
                    base_texto=remoto.get("texto", ""),
                    versao=max(_int(local.get("versao"), 1), _int(remoto.get("versao"), 1)),
                    pendente=True,
                )
                if brigou and remoto.get("texto") and remoto.get("texto") != fundido:
                    copia = self._nota_vazia(
                        str(uuid.uuid4()),
                        f'{local.get("title")} (Conflicted copy '
                        f'{remoto.get("device") or DEVICE_ID} {carimbo_conflito()})',
                        remoto.get("texto", ""),
                    )
                    self.notas[copia["id"]] = copia
                self._gravar()
            self._empurrar(nid, profundidade + 1)
            return

        nota = res.get("nota") or {}
        if nota:
            self.marcar_enviada(nid, nota)

    # -------------------------------------------------------------- páginas
    def _css(self):
        if self._css_instalado:
            return
        try:
            display = self.stack.get_display()
            provider = Gtk.CssProvider()
            provider.load_from_data(CSS_PAD.encode())
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 10
            )
            self._css_instalado = True
        except Exception:
            pass

    def criar_pagina(self):
        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.add_named(self._pagina_lista(), "lista")
        self.stack.add_named(self._pagina_editor(), "editor")
        self.stack.set_visible_child_name("lista")
        self._css()
        GLib.timeout_add_seconds(20, self._timer_poll)
        GLib.timeout_add_seconds(1, lambda: (self.sincronizar(), False)[1])
        self._render_lista()
        return self.stack

    def _timer_poll(self):
        # nao sincroniza enquanto o usuario esta escrevendo (evita puxar o
        # tapete): o editor fecha com autosave e o poll volta no ciclo seguinte
        if self.stack is not None and self.stack.get_visible_child_name() == "editor" and self.ed_sujo:
            return True
        self.sincronizar()
        return True

    # --------------------------------------------------------------- lista
    def _pagina_lista(self):
        caixa = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        caixa.add_css_class("spook-root")

        topo = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        titulo = Gtk.Label(label="PAD", xalign=0)
        titulo.add_css_class("spook-title")
        topo.append(titulo)
        self.lbl_count = Gtk.Label(label="0 NOTES", xalign=0)
        self.lbl_count.add_css_class("spook-meta")
        self.lbl_count.add_css_class("pad-meta")
        topo.append(self.lbl_count)

        espaco = Gtk.Box()
        espaco.set_hexpand(True)
        topo.append(espaco)

        btn_add = Gtk.Button(label="+ NEW")
        btn_add.add_css_class("spook-btn")
        btn_add.add_css_class("spook-accent")
        btn_add.connect("clicked", lambda *_: self._nova_nota())
        topo.append(btn_add)
        self.btn_add_nota = btn_add

        btn_sync = Gtk.Button(label="SYNC")
        btn_sync.add_css_class("spook-btn")
        btn_sync.connect("clicked", lambda *_: (self.sincronizar(True), self._pintar_status("syncing…"))[1])
        topo.append(btn_sync)
        self.btn_sync_notas = btn_sync
        caixa.append(topo)

        self.busca = Gtk.Entry()
        self.busca.add_css_class("spook-input")
        self.busca.set_placeholder_text("search notes…")
        self.busca.connect("changed", self._ao_buscar)
        # NOTAS | TAREFAS (a janela de tasks vive dentro do PAD)
        seg = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.seg_notas = Gtk.Button(label="NOTES")
        self.seg_notas.add_css_class("pad-seg-btn")
        self.seg_notas.connect("clicked", lambda *_: self._mostrar_vista("notas"))
        seg.append(self.seg_notas)
        self.seg_tarefas = Gtk.Button(label="TASKS")
        self.seg_tarefas.add_css_class("pad-seg-btn")
        self.seg_tarefas.connect("clicked", lambda *_: self._mostrar_vista("tarefas"))
        seg.append(self.seg_tarefas)
        caixa.append(seg)

        self.pilha_vistas = Gtk.Stack()
        self.pilha_vistas.set_vexpand(True)

        notas_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        notas_box.append(self.busca)

        self.lbl_status = Gtk.Label(label="…", xalign=0)
        self.lbl_status.add_css_class("pad-meta")
        notas_box.append(self.lbl_status)

        self.lista_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(self.lista_box)
        notas_box.append(scroll)

        self.pilha_vistas.add_named(notas_box, "notas")
        self.pilha_vistas.add_named(self._caixa_tarefas(), "tarefas")
        self.pilha_vistas.set_visible_child_name("notas")
        self._atualizar_seg("notas")
        caixa.append(self.pilha_vistas)
        return caixa

    # --------------------------------------------------------------- tarefas
    def _caixa_tarefas(self):
        caixa = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        topo = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.lbl_tasks = Gtk.Label(label="0 TASKS", xalign=0)
        self.lbl_tasks.add_css_class("pad-meta")
        topo.append(self.lbl_tasks)
        espaco = Gtk.Box()
        espaco.set_hexpand(True)
        topo.append(espaco)
        btn_refresh = Gtk.Button(label="↻")
        btn_refresh.add_css_class("spook-btn")
        btn_refresh.add_css_class("spook-btn-text-sm")
        btn_refresh.set_tooltip_text("Refresh tasks")
        btn_refresh.connect("clicked", lambda *_: self.pedir_tarefas())
        topo.append(btn_refresh)
        caixa.append(topo)

        self.prog_tasks = Gtk.ProgressBar()
        self.prog_tasks.add_css_class("pad-prog")
        self.prog_tasks.set_fraction(0.0)
        self.prog_tasks.set_show_text(False)
        caixa.append(self.prog_tasks)

        linha = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.task_entry = Gtk.Entry()
        self.task_entry.add_css_class("spook-input")
        self.task_entry.set_hexpand(True)
        self.task_entry.set_placeholder_text("new task…")
        self.task_entry.connect("activate", lambda *_: self._adicionar_tarefa())
        linha.append(self.task_entry)
        btn_add = Gtk.Button(label="+ ADD")
        btn_add.add_css_class("spook-btn")
        btn_add.add_css_class("spook-accent")
        btn_add.connect("clicked", lambda *_: self._adicionar_tarefa())
        linha.append(btn_add)
        caixa.append(linha)

        self.tasks_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(self.tasks_box)
        caixa.append(scroll)
        return caixa

    def _atualizar_seg(self, vista):
        # contagem e botoes de NOTAS somem na vista de tarefas
        if self.lbl_count is not None:
            self.lbl_count.set_visible(vista == "notas")
        for attr in ("btn_add_nota", "btn_sync_notas"):
            botao = getattr(self, attr, None)
            if botao is not None:
                botao.set_visible(vista == "notas")
        for botao, nome in ((self.seg_notas, "notas"), (self.seg_tarefas, "tarefas")):
            if botao is None:
                continue
            botao.remove_css_class("pad-seg-on")
            botao.remove_css_class("pad-seg-btn")
            botao.add_css_class("pad-seg-on" if nome == vista else "pad-seg-btn")

    def _mostrar_vista(self, vista):
        if self.pilha_vistas is not None:
            self.pilha_vistas.set_visible_child_name(vista)
        self._atualizar_seg(vista)
        if vista == "tarefas":
            self.pedir_tarefas()

    def pedir_tarefas(self):
        if callable(self.task_listar_cb):
            self.task_listar_cb()
        else:
            self.aplicar_tarefas([])

    def aplicar_tarefas(self, tasks):
        self.tasks = [dict(t) for t in (tasks or []) if isinstance(t, dict) and t.get("id")]
        GLib.idle_add(lambda: (self._render_tarefas(), False)[1])

    def _adicionar_tarefa(self):
        if self.task_entry is None:
            return
        titulo = (self.task_entry.get_text() or "").strip()
        if not titulo:
            return
        self.task_entry.set_text("")
        if callable(self.task_add_cb):
            self.task_add_cb(titulo)

    def _render_tarefas(self):
        if self.tasks_box is None:
            return
        while (c := self.tasks_box.get_first_child()):
            self.tasks_box.remove(c)

        tasks = sorted(
            self.tasks,
            key=lambda t: (_int(t.get("ts")), ),
            reverse=True,
        )
        feitas = sum(1 for t in tasks if t.get("feito"))
        if self.lbl_tasks is not None:
            self.lbl_tasks.set_text(
                f"{len(tasks) - feitas} OPEN"
                f" · {len(tasks)} TASK{'S' if len(tasks) != 1 else ''}"
            )
        if self.prog_tasks is not None:
            self.prog_tasks.set_fraction(feitas / len(tasks) if tasks else 0.0)

        if not tasks:
            vazio = Gtk.Label(
                label="NO TASKS YET\n\ntype above and hit + ADD.\nthey also show on the phone PAD.",
                justify=Gtk.Justification.CENTER,
            )
            vazio.add_css_class("pad-task-vazio")
            vazio.set_vexpand(True)
            self.tasks_box.append(vazio)
            return

        for t in tasks:
            self.tasks_box.append(self._card_tarefa(t))

    def _card_tarefa(self, t):
        feito = bool(t.get("feito"))
        origem = str(t.get("criado_por") or "?").lower()

        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        card.add_css_class("pad-task")
        if feito:
            card.add_css_class("pad-task-feita")

        chk = Gtk.CheckButton()
        chk.set_active(feito)
        chk.set_valign(Gtk.Align.CENTER)

        def _toggle(b, i=t.get("id"), antes=feito):
            if bool(b.get_active()) == antes:
                return
            if callable(self.task_toggle_cb):
                self.task_toggle_cb(i, bool(b.get_active()))

        chk.connect("toggled", _toggle)
        card.append(chk)

        conteudo = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        conteudo.set_hexpand(True)
        titulo = Gtk.Label(label=(t.get("titulo") or t.get("title") or "")[:300], xalign=0, wrap=True)
        titulo.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        titulo.add_css_class("pad-task-tit")
        conteudo.append(titulo)

        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        chip = Gtk.Label(label="POCO" if origem == "android" else ("ARCH" if origem == "arch" else origem.upper() or "?"))
        chip.add_css_class("pad-task-chip")
        chip.add_css_class("pad-task-chip-poco" if origem == "android" else "pad-task-chip-arch")
        meta.append(chip)
        quando = Gtk.Label(label=carimbo(_int(t.get("ts"))), xalign=0)
        quando.add_css_class("pad-task-meta")
        meta.append(quando)
        conteudo.append(meta)
        card.append(conteudo)

        del_btn = Gtk.Button(label="DEL")
        del_btn.add_css_class("spook-btn")
        del_btn.add_css_class("spook-err-btn")
        del_btn.add_css_class("spook-btn-text-sm")
        del_btn.set_valign(Gtk.Align.CENTER)
        del_btn.set_tooltip_text("Delete task")

        def _del(_, i=t.get("id")):
            if callable(self.task_del_cb):
                self.task_del_cb(i)

        del_btn.connect("clicked", _del)
        card.append(del_btn)
        return card

    def _ao_buscar(self, entry):
        self._filtro = (entry.get_text() or "").strip().lower()
        self._render_lista()

    def _notas_visiveis(self):
        with self._lock:
            notas = [dict(n) for n in self.notas.values()]
        notas = [n for n in notas if not n.get("removido")]
        if self._filtro:
            f = self._filtro
            notas = [
                n for n in notas
                if f in (n.get("title") or "").lower() or f in (n.get("texto") or "").lower()
            ]
        notas.sort(key=lambda n: (_int(n.get("em")), _int(n.get("versao"))), reverse=True)
        return notas

    def _render_lista(self):
        if self.lista_box is None:
            return
        while (c := self.lista_box.get_first_child()):
            self.lista_box.remove(c)
        notas = self._notas_visiveis()
        if self.lbl_count is not None:
            self.lbl_count.set_text(f"{len(notas)} NOTE{'S' if len(notas) != 1 else ''}")
        if not notas:
            vazio = Gtk.Label(
                label="NO NOTES YET\n\ntap + NEW and start writing.\nsyncs automatically with the relay.",
                justify=Gtk.Justification.CENTER,
            )
            vazio.add_css_class("pad-vazio")
            vazio.set_vexpand(True)
            self.lista_box.append(vazio)
        else:
            for n in notas:
                self.lista_box.append(self._card_nota(n))
        self._pintar_status("")

    def _card_nota(self, n):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        card.add_css_class("pad-card")
        card.add_css_class(f"pad-cor-{cor_da_nota(n.get('id', ''))}")

        topo = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        titulo = Gtk.Label(label=(n.get("title") or "untitled")[:120], xalign=0)
        titulo.add_css_class("pad-titulo-card")
        titulo.set_ellipsize(Pango.EllipsizeMode.END)
        titulo.set_hexpand(True)
        topo.append(titulo)
        if n.get("pendente"):
            chip = Gtk.Label(label="PENDING")
            chip.add_css_class("pad-chip")
            chip.add_css_class("pad-chip-pend")
            topo.append(chip)
        card.append(topo)

        previa = " ".join(
            linha.strip() for linha in (n.get("texto") or "").splitlines() if linha.strip()
        )[:220]
        if previa:
            lbl = Gtk.Label(label=previa, xalign=0)
            lbl.add_css_class("pad-previa")
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.set_lines(2)
            lbl.set_wrap(True)
            card.append(lbl)

        meta = (
            f"v{_int(n.get('versao'), 1)} · {carimbo(_int(n.get('em')))}"
            f" · {n.get('device') or '--'}"
        )
        if _int(n.get("n_versoes")):
            meta += f" · {_int(n.get('n_versoes'))}v in history"
        lbl_meta = Gtk.Label(label=meta, xalign=0)
        lbl_meta.add_css_class("pad-meta")
        card.append(lbl_meta)

        clique = Gtk.GestureClick()
        clique.set_button(1)
        clique.connect("released", lambda *_: self._abrir_editor(n.get("id")))
        card.add_controller(clique)
        card.set_cursor_from_name("pointer")
        return card

    def _pintar_status(self, texto=None):
        if self.lbl_status is None:
            return
        if texto:
            self.lbl_status.set_text(texto)
            return
        pend = sum(1 for n in self.notas.values() if n.get("pendente"))
        quando = carimbo(self._ultimo_sync) if self._ultimo_sync else "--:--"
        marca = f"SYNC {quando}"
        if self._erro:
            marca = f"OFFLINE ({self._erro}) · mostra o que esta no aparelho"
        elif pend:
            marca += f" · {pend} pendente{'s' if pend != 1 else ''}"
        self.lbl_status.set_text(marca)

    def _pos_sync(self, *_):
        self._render_lista()
        # se a nota aberta mudou no servidor e eu nao estou digitando, recarrega
        if self.ed_id and not self.ed_sujo:
            self._recarregar_editor()
        return False

    # -------------------------------------------------------------- editor
    def _pagina_editor(self):
        caixa = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        caixa.add_css_class("spook-root")

        topo = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_voltar = Gtk.Button(label="BACK")
        btn_voltar.add_css_class("spook-btn")
        btn_voltar.set_valign(Gtk.Align.CENTER)
        btn_voltar.connect("clicked", lambda *_: self._fechar_editor())
        topo.append(btn_voltar)

        coluna = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        coluna.set_hexpand(True)
        coluna.set_valign(Gtk.Align.CENTER)
        rotulo = Gtk.Label(label="PAD // NOTE", xalign=0)
        rotulo.add_css_class("pad-ed-titulo")
        coluna.append(rotulo)
        self.ed_status = Gtk.Label(label="--", xalign=0)
        self.ed_status.add_css_class("pad-meta")
        coluna.append(self.ed_status)
        topo.append(coluna)

        btn_hist = Gtk.Button(label="HISTORY")
        btn_hist.add_css_class("spook-btn")
        btn_hist.connect("clicked", lambda *_: self._mostrar_historico())
        topo.append(btn_hist)

        btn_apagar = Gtk.Button(label="DELETE")
        btn_apagar.add_css_class("spook-btn")
        btn_apagar.add_css_class("spook-err-btn")
        btn_apagar.connect("clicked", lambda *_: self._confirmar_apagar())
        topo.append(btn_apagar)
        caixa.append(topo)

        self.ed_titulo = Gtk.Entry()
        self.ed_titulo.add_css_class("pad-titulo")
        self.ed_titulo.set_placeholder_text("title")
        self.ed_titulo.connect("changed", self._ao_digitar)
        caixa.append(self.ed_titulo)

        self.ed_barra = Gtk.Box()
        self.ed_barra.set_size_request(-1, 2)
        caixa.append(self.ed_barra)

        self.ed_texto = Gtk.TextView()
        self.ed_texto.add_css_class("pad-texto")
        self.ed_texto.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.ed_texto.set_left_margin(10)
        self.ed_texto.set_right_margin(10)
        self.ed_texto.set_top_margin(8)
        self.ed_texto.set_bottom_margin(8)
        self.ed_texto.set_monospace(True)
        self.ed_texto.set_vexpand(True)
        self.ed_texto.get_buffer().connect("changed", self._ao_digitar)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(self.ed_texto)
        caixa.append(scroll)

        self.ed_rodape = Gtk.Label(label="0L · 0P", xalign=0)
        self.ed_rodape.add_css_class("pad-meta")
        caixa.append(self.ed_rodape)
        return caixa

    def _texto_editor(self):
        buf = self.ed_texto.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def _set_texto_editor(self, texto):
        self._buffer_guard = True
        try:
            self.ed_texto.get_buffer().set_text(texto or "")
        finally:
            self._buffer_guard = False

    def _nova_nota(self):
        nota = self.criar_nota()
        self._abrir_editor(nota["id"])
        GLib.idle_add(lambda: (self.ed_titulo.grab_focus(), False)[1])

    def _abrir_editor(self, nid):
        with self._lock:
            n = self.notas.get(nid)
            if not n or n.get("removido"):
                return
            self.ed_id = nid
            titulo = n.get("title") or "untitled"
            texto = n.get("texto") or ""
        self._pintar_barra(nid)
        self._buffer_guard = True
        self.ed_titulo.set_text(titulo)
        self._set_texto_editor(texto)
        self._buffer_guard = False
        self.ed_sujo = False
        self._atualizar_rodape()
        self.ed_status.set_text(f"v{_int(n.get('versao'), 1)} · {carimbo(_int(n.get('em')))}")
        self.stack.set_visible_child_name("editor")
        # puxa a versao completa (com historico) em segundo plano
        threading.Thread(target=self._baixar_uma, args=(nid,), daemon=True).start()

    def _pintar_barra(self, nid):
        if getattr(self, "ed_barra", None) is None:
            return
        for i in range(len(CORES)):
            self.ed_barra.remove_css_class(f"pad-bar-{i}")
        self.ed_barra.add_css_class(f"pad-bar-{cor_da_nota(nid or '')}")

    def _baixar_uma(self, nid):
        res = self._http("GET", f"/pad/{nid}")
        if res and res.get("ok") and res.get("nota"):
            self.aplicar_remoto(res["nota"])
            GLib.idle_add(self._pos_sync)

    def _recarregar_editor(self):
        with self._lock:
            n = self.notas.get(self.ed_id or "")
            if not n:
                return
            if n.get("removido") and not self.ed_sujo:
                # apagaram a nota no celular enquanto eu olhava: sai da tela
                GLib.idle_add(lambda: (self._fechar_editor(), False)[1])
                return
            titulo, texto = n.get("title", ""), n.get("texto", "")
            versao, em = _int(n.get("versao"), 1), _int(n.get("em"))
        if self.ed_titulo.get_text() != titulo:
            self._buffer_guard = True
            self.ed_titulo.set_text(titulo)
            self._buffer_guard = False
        if self._texto_editor() != texto:
            self._set_texto_editor(texto)
            self._atualizar_rodape()
        self.ed_status.set_text(f"v{versao} · {carimbo(em)}")

    def _ao_digitar(self, *_):
        if self._buffer_guard or self.ed_id is None:
            return
        self.ed_sujo = True
        self._atualizar_rodape()
        self.ed_status.set_text("editing…")
        if self._ed_timer is not None:
            try:
                GLib.source_remove(self._ed_timer)
            except Exception:
                pass
        self._ed_timer = GLib.timeout_add(900, self._salvar_agora)

    def _atualizar_rodape(self):
        if self.ed_rodape is None:
            return
        texto = self._texto_editor()
        linhas = texto.count("\n") + (0 if texto.endswith("\n") or not texto else 1)
        palavras = len([p for p in texto.split() if p])
        self.ed_rodape.set_text(f"{linhas}L · {palavras}P · {_hash(texto)[:6]}")

    def _salvar_agora(self):
        self._ed_timer = None
        if self.ed_id is None or not self.ed_sujo:
            return False
        nid = self.ed_id
        titulo = self.ed_titulo.get_text()
        texto = self._texto_editor()
        self.editar_local(nid, titulo, texto)
        self.ed_sujo = False
        self.ed_status.set_text("AUTO-SAVE…")
        threading.Thread(target=self._salvar_thread, args=(nid,), daemon=True).start()
        return False

    def _salvar_thread(self, nid):
        self._empurrar(nid)
        with self._lock:
            n = self.notas.get(nid) or {}
            ok = not n.get("pendente")
            quando, versao = _int(n.get("em")), _int(n.get("versao"), 1)
        GLib.idle_add(self._pos_salvar, nid, ok, quando, versao)

    def _pos_salvar(self, nid, ok, quando, versao):
        if self.ed_id == nid:
            if ok:
                self.ed_status.set_text(f"saved {carimbo(quando)} · v{versao}")
            else:
                self.ed_status.set_text("offline · saved on device")
        self._pintar_status("")
        return False

    def _fechar_editor(self):
        if self.ed_sujo and self.ed_id:
            self._salvar_agora()
        self.ed_id = None
        self.ed_sujo = False
        self.stack.set_visible_child_name("lista")
        self._render_lista()

    # -------------------------------------------------------------- dialogs
    def _janela_modal(self, titulo, largura=620, altura=460):
        win = Gtk.Window(title=titulo, transient_for=self.app.janela, modal=True)
        win.set_default_size(largura, altura)
        raiz = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        raiz.add_css_class("spook-root")
        win.set_child(raiz)
        return win, raiz

    def _mostrar_historico(self):
        if not self.ed_id:
            return
        nid = self.ed_id
        win, raiz = self._janela_modal("SPOOK // HISTORICO")
        cab = Gtk.Label(label="NOTE HISTORY", xalign=0)
        cab.add_css_class("spook-title")
        raiz.append(cab)
        info = Gtk.Label(label="loading…", xalign=0)
        info.add_css_class("pad-meta")
        raiz.append(info)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        scroll.set_child(box)
        raiz.append(scroll)

        def montar(versoes):
            while (c := box.get_first_child()):
                box.remove(c)
            if not versoes:
                info.set_text("no earlier versions — the relay keeps the last 20")
                return
            info.set_text(f"{len(versoes)} versao(oes) · a atual no topo")
            for v in versoes:
                linha = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                linha.add_css_class("pad-hist-row")
                textos = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                textos.set_hexpand(True)
                marca = "ATUAL" if v.get("atual") else f"v{_int(v.get('versao'))}"
                tit = Gtk.Label(
                    label=f"{carimbo(_int(v.get('em')))} · {marca} · {v.get('device') or '--'}",
                    xalign=0,
                )
                tit.add_css_class("pad-meta")
                textos.append(tit)
                previa = " ".join(
                    l.strip() for l in (v.get("texto") or "").splitlines() if l.strip()
                )[:120] or "(vazia)"
                lbl = Gtk.Label(label=previa, xalign=0)
                lbl.add_css_class("pad-hist-txt")
                lbl.set_ellipsize(Pango.EllipsizeMode.END)
                textos.append(lbl)
                linha.append(textos)
                if not v.get("atual"):
                    btn = Gtk.Button(label="RESTORE")
                    btn.add_css_class("spook-btn")
                    btn.connect("clicked", lambda _b, em=_int(v.get("em")): (restaurar(em), win.close())[1])
                    linha.append(btn)
                box.append(linha)

        def restaurar(em):
            res = self._http("POST", f"/pad/{nid}/restaurar", {"em": em, "device": DEVICE_ID}, timeout=15)
            if res and res.get("ok") and res.get("nota"):
                self.aplicar_remoto(res["nota"])
                GLib.idle_add(self._pos_sync)

        def carregar():
            res = self._http("GET", f"/pad/{nid}/historico", timeout=10)
            versoes = (res or {}).get("versoes") or []
            if not versoes:
                with self._lock:
                    local = self.notas.get(nid) or {}
                    versoes = local.get("historico") or []
                    if not versoes:
                        versoes = [{
                            "em": local.get("em", 0), "device": local.get("device", ""),
                            "texto": local.get("texto", ""), "versao": local.get("versao", 1),
                            "atual": True,
                        }]
            GLib.idle_add(lambda: (montar(versoes), False)[1])

        threading.Thread(target=carregar, daemon=True).start()
        win.present()

    def _confirmar_apagar(self):
        if not self.ed_id:
            return
        nid = self.ed_id
        win, raiz = self._janela_modal("SPOOK // APAGAR", 520, 240)
        lab = Gtk.Label(
            label="Delete this note?\n\nIt disappears from every device.\nThe file stays in Drive (tombstone).",
            justify=Gtk.Justification.CENTER,
        )
        lab.add_css_class("spook-note")
        lab.set_vexpand(True)
        raiz.append(lab)

        linha = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        linha.set_halign(Gtk.Align.END)
        cancelar = Gtk.Button(label="CANCEL")
        cancelar.add_css_class("spook-btn")
        cancelar.connect("clicked", lambda *_: win.close())
        linha.append(cancelar)

        apagar = Gtk.Button(label="DELETE")
        apagar.add_css_class("spook-btn")
        apagar.add_css_class("spook-err-btn")

        def confirmar(*_):
            self.apagar_local(nid)
            threading.Thread(target=self._apagar_thread, args=(nid,), daemon=True).start()
            win.close()
            self.ed_id = None
            self.ed_sujo = False
            self.stack.set_visible_child_name("lista")
            self._render_lista()

        apagar.connect("clicked", confirmar)
        linha.append(apagar)
        raiz.append(linha)
        win.present()

    def _apagar_remoto(self, nid):
        res = self._http("DELETE", f"/pad/{nid}?device={DEVICE_ID}", timeout=15)
        if res and res.get("ok") and res.get("nota"):
            self.marcar_enviada(nid, res["nota"])
            return True
        return False

    def _apagar_thread(self, nid):
        self._apagar_remoto(nid)
        GLib.idle_add(self._pos_sync)
