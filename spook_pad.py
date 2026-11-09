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
.pad-titulo {
  font-size: 20px; font-weight: 700; color: #9CFF57;
  letter-spacing: -0.3px;
}
.pad-count {
  font-family: "JetBrains Mono", "JetBrainsMono NF", monospace;
  font-size: 8px; font-weight: 700; color: #5CE1E6;
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
/* Ícones Material Symbols (os mesmos glifos do app Android) */
.pad-ico-list, .pad-ico-cal {
  font-family: "Material Symbols Rounded"; font-size: 15px; color: #8A8F98;
}
button.pad-seg-on .pad-ico-list, button.pad-seg-on .pad-ico-cal { color: #FF4D9A; }
.fin-ico { font-family: "Material Symbols Rounded"; font-size: 17px; }
.fin-ico-cal { color: #5CE1E6; }
.fin-ico-list { color: #FFC400; }
.fin-ico-sync { color: #5CE1E6; }
.fin-ico-add { color: #0B0A12; font-size: 26px; }
.fin-ico-check { font-family: "Material Symbols Rounded"; font-size: 18px; }
.fin-ico-empty { font-family: "Material Symbols Rounded"; font-size: 44px; color: #8A8F98; }
.fin-ico-del { font-family: "Material Symbols Rounded"; font-size: 16px; color: #FF5C6C; }
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

/* ---- FINANCE CALENDAR ---- */
.pad-fin-cal-cell {
  background: transparent;
  border-radius: 6px;
  border: 1px solid transparent;
  padding: 4px;
  min-width: 40px;
  min-height: 40px;
  transition: all 120ms ease;
}

.pad-fin-cal-cell-today {
  background: rgba(255, 77, 154, 0.18);
  border: 1px solid #FF4D9A;
}

.pad-fin-cal-cell-due {
  background: rgba(255, 196, 0, 0.12);
  border: 1px solid rgba(255, 196, 0, 0.5);
}

.pad-fin-cal-cell-overdue {
  background: rgba(255, 196, 0, 0.12);
  border: 1px solid rgba(255, 196, 0, 0.5);
}

.pad-fin-cal-cell:hover {
  background: #1E1C2A;
}

.pad-fin-cal-day {
  font-size: 13px;
  font-weight: 500;
  color: #F4F1FF;
}

.pad-fin-cal-day-today {
  color: #FF4D9A;
  font-weight: 700;
}

.pad-fin-cal-dots-box {
  margin-top: 2px;
}

.pad-fin-cal-dot-unpaid {
  background: #FF5C6C;
  border-radius: 50%;
  min-width: 6px;
  min-height: 6px;
  margin: 0 1px;
}

.pad-fin-cal-dot-paid {
  background: #5CE1E6;
  border-radius: 50%;
  min-width: 6px;
  min-height: 6px;
  margin: 0 1px;
}

.pad-fin-cal-plus {
  font-size: 7px;
  color: #8A8F98;
  margin-left: 2px;
}

.pad-fin-cal-header {
  font-size: 8px;
  font-weight: 700;
  color: #8A8F98;
  padding: 4px 0;
}

.pad-fin-parcelas-card {
  background: #16141F;
  border-radius: 12px;
  border: 1px solid #3A3648;
  padding: 10px 12px;
  margin-bottom: 6px;
}

.pad-fin-parcelas-card:hover {
  background: #1E1C2A;
  border-color: #FF4D9A;
}

.pad-fin-parcela-tit {
  font-size: 12px;
  font-weight: 700;
  color: #F4F1FF;
}

.pad-fin-parcela-meta {
  font-family: "JetBrains Mono", "JetBrainsMono NF", monospace;
  font-size: 9px;
  color: #8A8F98;
}

.pad-fin-parcela-valor {
  font-family: "JetBrains Mono", "JetBrainsMono NF", monospace;
  font-size: 11px;
  font-weight: 700;
  color: #FF4D9A;
}

.pad-fin-parcela-valor-pago {
  color: #5CE1E6;
}

.pad-fin-parcela-check {
  background: transparent;
  border: 1px solid #3A3648;
  border-radius: 4px;
  min-width: 20px;
  min-height: 20px;
  font-family: "JetBrains Mono", "JetBrainsMono NF", monospace;
  font-size: 10px;
  color: #8A8F98;
  transition: all 100ms ease;
}

.pad-fin-parcela-check:hover {
  background: rgba(255, 77, 154, 0.14);
  border-color: #FF4D9A;
  color: #FF4D9A;
}

.pad-fin-parcela-check-on {
  background: rgba(92, 225, 230, 0.18);
  border-color: #5CE1E6;
  color: #5CE1E6;
}

.pad-fin-cal-mes {
  font-family: "JetBrains Mono", "JetBrainsMono NF", monospace;
  font-size: 11px;
  font-weight: 700;
  color: #FF4D9A;
}

.pad-fin-cal-nav {
  background: transparent;
  background-image: none;
  color: #8A8F98;
  border: 1px solid #3A3648;
  border-radius: 999px;
  padding: 4px 12px;
  font-size: 9px;
  font-weight: 700;
  box-shadow: none;
  outline: none;
  transition: all 120ms ease;
}

.pad-fin-cal-nav:hover {
  background: transparent;
  color: #F4F1FF;
  border-color: #6E6A81;
  box-shadow: none;
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
        self.task_due_cb = None
        self.finance_listar_cb = None
        self.finance_parcela_cb = None

        # calendário tasks
        self.task_stack = None
        self.seg_task_lista = None
        self.seg_task_cal = None
        self.cal_box = None
        self.cal_grid = None
        self.cal_mes_ano = None
        self.cal_tasks_box = None
        self._cal_mes_atual = None
        self._due_dialog_task = None

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
    def _icone_msi(self, glifo, *classes):
        """Label com um glifo Material Symbols Rounded (mesmo ícone do app Android)."""
        lbl = Gtk.Label(label=glifo)
        lbl.add_css_class("fin-ico")
        for classe in classes:
            lbl.add_css_class(classe)
        return lbl

    # =========================================================== IA TAB
    def _criar_pagina_ia(self):
        """IA chat page with shared memory via relay."""
        caixa = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        caixa.add_css_class("spook-root")
        caixa.set_margin_start(16)
        caixa.set_margin_end(16)
        caixa.set_margin_top(16)
        caixa.set_margin_bottom(16)

        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.set_valign(Gtk.Align.CENTER)
        titulo = Gtk.Label(label="SPOOK IA", xalign=0)
        titulo.add_css_class("ia-title")
        header.append(titulo)
        caixa.append(header)

        # Chat area
        self.ia_scrolled = Gtk.ScrolledWindow()
        self.ia_scrolled.set_vexpand(True)
        self.ia_scrolled.set_hexpand(True)
        self.ia_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.ia_chat_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.ia_chat_box.set_margin_top(8)
        self.ia_chat_box.set_margin_bottom(8)
        self.ia_scrolled.set_child(self.ia_chat_box)
        caixa.append(self.ia_scrolled)

        # Input area
        input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.ia_entry = Gtk.Entry()
        self.ia_entry.set_hexpand(True)
        self.ia_entry.set_placeholder_text("Pergunte algo... (tasks, notas, finanças, scans, comandos)")
        self.ia_entry.connect("activate", lambda *_: self._ia_enviar())
        input_box.append(self.ia_entry)

        self.ia_send_btn = Gtk.Button()
        self.ia_send_btn.add_css_class("spook-btn")
        self.ia_send_btn.add_css_class("spook-accent")
        self.ia_send_btn.set_child(self._icone_msi("\ue145", "fin-ico-add"))
        self.ia_send_btn.connect("clicked", lambda *_: self._ia_enviar())
        input_box.append(self.ia_send_btn)

        caixa.append(input_box)
        return caixa

    def _ia_enviar(self):
        texto = (self.ia_entry.get_text() or "").strip()
        if not texto:
            return
        self.ia_entry.set_text("")
        # Adiciona mensagem do usuario
        self._ia_adicionar_mensagem("usuario", texto)
        # Envia via socket /ia namespace
        if hasattr(self, "sio") and getattr(self.sio, "connected", False):
            self.sio.emit("chat_ia_perguntar", {
                "pergunta": texto,
                "sessao_id": self._ia_sessao_id,
                "usuario": "arch"
            }, namespace="/ia")
        else:
            self._ia_adicionar_mensagem("ia", "⚠ Socket não conectado")

    def _ia_adicionar_mensagem(self, origem, texto):
        msg_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        if origem == "usuario":
            msg_box.set_halign(Gtk.Align.END)
            lbl = Gtk.Label(label=f"Você: {texto}", xalign=1, wrap=True)
            lbl.add_css_class("ia-msg-user")
        else:
            msg_box.set_halign(Gtk.Align.START)
            lbl = Gtk.Label(label=f"IA: {texto}", xalign=0, wrap=True)
            lbl.add_css_class("ia-msg-ia")
        lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl.set_max_width_chars(70)
        msg_box.append(lbl)
        self.ia_chat_box.append(msg_box)
        # Auto-scroll
        GLib.idle_add(lambda: self._ia_scroll_fim())

    def _ia_scroll_fim(self):
        adj = self.ia_scrolled.get_vadjustment()
        if adj:
            adj.set_value(adj.get_upper())

    # =========================================================== IA HANDLERS
    def _ia_on_resposta(self, dados):
        """Handler para chat_ia_resposta no namespace /ia."""
        ok = (dados or {}).get("ok", False)
        resposta = (dados or {}).get("resposta", "")
        motor = (dados or {}).get("motor", "")
        if ok:
            self._ia_adicionar_mensagem("ia", f"{resposta}  [{motor}]")
        else:
            self._ia_adicionar_mensagem("ia", f"⚠ {resposta}")

    def _ia_on_tool_call(self, dados):
        """Handler para tool_call - mostra que a IA está executando ação."""
        acao = (dados or {}).get("acao", "")
        params = (dados or {}).get("params", {})
        self._ia_adicionar_mensagem("ia", f"⚙ Executando: {acao} ({params})")

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
        topo.set_margin_top(4)
        coluna = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        coluna.set_hexpand(True)
        titulo = Gtk.Label(label="PAD", xalign=0)
        titulo.add_css_class("pad-titulo")
        coluna.append(titulo)
        self.lbl_count = Gtk.Label(label="0 NOTES", xalign=0)
        self.lbl_count.add_css_class("pad-count")
        coluna.append(self.lbl_count)
        topo.append(coluna)

        btn_add = Gtk.Button(label="+ NEW")
        btn_add.add_css_class("spook-btn")
        btn_add.add_css_class("spook-accent")
        btn_add.set_valign(Gtk.Align.CENTER)
        btn_add.connect("clicked", lambda *_: self._nova_nota())
        topo.append(btn_add)
        self.btn_add_nota = btn_add

        btn_sync = Gtk.Button(label="SYNC")
        btn_sync.add_css_class("spook-btn")
        btn_sync.set_valign(Gtk.Align.CENTER)
        btn_sync.connect("clicked", lambda *_: (self.sincronizar(True), self._pintar_status("syncing…"))[1])
        topo.append(btn_sync)
        self.btn_sync_notas = btn_sync
        caixa.append(topo)

        # NOTAS | TAREFAS | FINANÇAS (a janela de tasks/finance vive dentro do PAD)
        seg = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.seg_notas = Gtk.Button(label="NOTES")
        self.seg_notas.add_css_class("pad-seg-btn")
        self.seg_notas.connect("clicked", lambda *_: self._mostrar_vista("notas"))
        seg.append(self.seg_notas)
        self.seg_tarefas = Gtk.Button(label="TASKS")
        self.seg_tarefas.add_css_class("pad-seg-btn")
        self.seg_tarefas.connect("clicked", lambda *_: self._mostrar_vista("tarefas"))
        seg.append(self.seg_tarefas)
        self.seg_finance = Gtk.Button(label="FINANCE")
        self.seg_finance.add_css_class("pad-seg-btn")
        self.seg_finance.connect("clicked", lambda *_: self._mostrar_vista("finance"))
        seg.append(self.seg_finance)
        caixa.append(seg)

        self.pilha_vistas = Gtk.Stack()
        self.pilha_vistas.set_vexpand(True)

        notas_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.busca = Gtk.Entry()
        self.busca.add_css_class("spook-input")
        self.busca.set_placeholder_text("search notes…")
        self.busca.connect("changed", self._ao_buscar)
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
        self.pilha_vistas.add_named(self._caixa_finance(), "finance")
        self.pilha_vistas.set_visible_child_name("notas")
        self._atualizar_seg("notas")
        caixa.append(self.pilha_vistas)
        return caixa

    # --------------------------------------------------------------- tarefas
    def _caixa_tarefas(self):
        caixa = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        # Header com contador e toggle LISTA/CALENDÁRIO
        topo = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.lbl_tasks = Gtk.Label(label="0 TASKS", xalign=0)
        self.lbl_tasks.add_css_class("pad-meta")
        topo.append(self.lbl_tasks)
        espaco = Gtk.Box()
        espaco.set_hexpand(True)
        topo.append(espaco)

        # Toggle LISTA | CALENDÁRIO (pills com ícone, igual ao app Android)
        def _pill_seg(texto, glifo, classe_ico):
            conteudo = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            conteudo.set_valign(Gtk.Align.CENTER)
            ic = Gtk.Label(label=glifo)
            ic.add_css_class(classe_ico)
            ic.set_valign(Gtk.Align.CENTER)
            conteudo.append(ic)
            conteudo.append(Gtk.Label(label=texto))
            btn = Gtk.Button()
            btn.set_child(conteudo)
            return btn

        self.seg_task_lista = _pill_seg("LIST", "\ue896", "pad-ico-list")
        self.seg_task_lista.add_css_class("pad-seg-btn")
        self.seg_task_lista.connect("clicked", lambda *_: self._mostrar_task_vista("lista"))
        topo.append(self.seg_task_lista)

        self.seg_task_cal = _pill_seg("CALENDAR", "\ue916", "pad-ico-cal")
        self.seg_task_cal.add_css_class("pad-seg-btn")
        self.seg_task_cal.connect("clicked", lambda *_: self._mostrar_task_vista("calendario"))
        topo.append(self.seg_task_cal)

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

        # Stack para LISTA e CALENDÁRIO
        self.task_stack = Gtk.Stack()
        self.task_stack.set_vexpand(True)
        self.task_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        # Lista de tasks
        self.tasks_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        scroll_lista = Gtk.ScrolledWindow()
        scroll_lista.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll_lista.set_vexpand(True)
        scroll_lista.set_child(self.tasks_box)
        self.task_stack.add_named(scroll_lista, "lista")

        # Calendário
        self.cal_box = self._criar_calendario_tarefas()
        self.task_stack.add_named(self.cal_box, "calendario")

        self.task_stack.set_visible_child_name("lista")
        caixa.append(self.task_stack)

        self._atualizar_task_seg("lista")
        return caixa

    # ------------------------------------------------------------ finanças
    def _caixa_finance(self):
        """Finance dashboard matching Android app."""
        import datetime
        
        caixa = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        caixa.add_css_class("spook-root")
        caixa.set_margin_start(16)
        caixa.set_margin_end(16)
        caixa.set_margin_top(16)
        caixa.set_margin_bottom(16)

        # ===== HEADER: Title + Month Navigation + Actions =====
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.set_valign(Gtk.Align.CENTER)

        # Title
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.lbl_finance_title = Gtk.Label(label="FINANCE", xalign=0)
        self.lbl_finance_title.add_css_class("finance-title")
        self.lbl_finance_subtitle = Gtk.Label(label="expense & installment control", xalign=0)
        self.lbl_finance_subtitle.add_css_class("finance-subtitle")
        title_box.append(self.lbl_finance_title)
        title_box.append(self.lbl_finance_subtitle)
        header.append(title_box)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        header.append(spacer)

        # Month navigation
        month_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        month_box.set_valign(Gtk.Align.CENTER)
        
        self.btn_fin_prev = Gtk.Button()
        self.btn_fin_prev.add_css_class("finance-nav-btn")
        self.btn_fin_prev.set_child(Gtk.Label(label="◀"))
        self.btn_fin_prev.connect("clicked", lambda *_: self._fin_mudar_mes(-1))
        month_box.append(self.btn_fin_prev)

        self.lbl_fin_mes = Gtk.Label(label="", xalign=0.5)
        self.lbl_fin_mes.add_css_class("finance-month-label")
        self.lbl_fin_mes.set_size_request(160, -1)
        month_box.append(self.lbl_fin_mes)

        self.btn_fin_next = Gtk.Button()
        self.btn_fin_next.add_css_class("finance-nav-btn")
        self.btn_fin_next.set_child(Gtk.Label(label="▶"))
        self.btn_fin_next.connect("clicked", lambda *_: self._fin_mudar_mes(1))
        month_box.append(self.btn_fin_next)

        header.append(month_box)

        # Actions: Calendar + Refresh
        actions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        
        self.btn_fin_cal = Gtk.Button()
        self.btn_fin_cal.add_css_class("finance-action-btn")
        self.btn_fin_cal.set_tooltip_text("Due dates calendar")
        # Icons.Filled.DateRange do app Android (Material Symbols "date_range")
        self.btn_fin_cal.set_child(self._icone_msi("\ue916", "fin-ico-cal"))
        self.btn_fin_cal.connect("clicked", lambda *_: self._mostrar_finance_vista("calendario"))
        actions_box.append(self.btn_fin_cal)

        self.btn_fin_parcelas = Gtk.Button()
        self.btn_fin_parcelas.add_css_class("finance-action-btn")
        self.btn_fin_parcelas.set_tooltip_text("All installments")
        # Icons.Filled.List do app Android
        self.btn_fin_parcelas.set_child(self._icone_msi("\ue896", "fin-ico-list"))
        self.btn_fin_parcelas.connect("clicked", lambda *_: self._mostrar_finance_vista("parcelas"))
        actions_box.append(self.btn_fin_parcelas)

        self.btn_fin_refresh = Gtk.Button()
        self.btn_fin_refresh.add_css_class("finance-action-btn")
        self.btn_fin_refresh.set_tooltip_text("Sync")
        # Icons.Filled.Refresh do app Android
        self.btn_fin_refresh.set_child(self._icone_msi("\ue5d5", "fin-ico-sync"))
        self.btn_fin_refresh.connect("clicked", lambda *_: self.pedir_finance())
        actions_box.append(self.btn_fin_refresh)

        header.append(actions_box)
        caixa.append(header)

        # ===== MAIN CONTENT: Stack for Dashboard / Installments / Calendar =====
        self.finance_stack = Gtk.Stack()
        self.finance_stack.set_vexpand(True)
        self.finance_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.finance_stack.set_vhomogeneous(False)
        self.finance_stack.set_hhomogeneous(True)

        # --- DASHBOARD (main view) ---
        self.fin_dash_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.fin_dash_box.set_valign(Gtk.Align.START)
        
        dash_scroll = Gtk.ScrolledWindow()
        dash_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        dash_scroll.set_vexpand(True)
        dash_scroll.set_child(self.fin_dash_box)
        dash_scroll.add_css_class("finance-scroll")
        self.finance_stack.add_named(dash_scroll, "dashboard")

        # --- INSTALLMENTS LIST ---
        self.fin_parcelas_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.fin_parcelas_box.set_valign(Gtk.Align.START)
        
        parcelas_scroll = Gtk.ScrolledWindow()
        parcelas_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        parcelas_scroll.set_vexpand(True)
        parcelas_scroll.set_child(self.fin_parcelas_box)
        parcelas_scroll.add_css_class("finance-scroll")
        self.finance_stack.add_named(parcelas_scroll, "parcelas")

        # --- CALENDAR ---
        self.fin_cal_box = self._criar_calendario_finance_v2()
        self.finance_stack.add_named(self.fin_cal_box, "calendario")

        # Default to dashboard
        self.finance_stack.set_visible_child_name("dashboard")
        caixa.append(self.finance_stack)

        # ===== FAB: Add Expense =====
        self.btn_fin_add = Gtk.Button()
        self.btn_fin_add.add_css_class("finance-fab")
        # Icons.Filled.Add do app Android
        self.btn_fin_add.set_child(self._icone_msi("\ue145", "fin-ico-add"))
        self.btn_fin_add.connect("clicked", lambda *_: self._abrir_form_expense(None))
        self.btn_fin_add.set_size_request(56, 56)
        # Position FAB at bottom-right using overlay
        overlay = Gtk.Overlay()
        overlay.set_child(caixa)
        overlay.add_overlay(self.btn_fin_add)
        self.btn_fin_add.set_valign(Gtk.Align.END)
        self.btn_fin_add.set_halign(Gtk.Align.END)
        self.btn_fin_add.set_margin_end(20)
        self.btn_fin_add.set_margin_bottom(20)

        # Initialize month to current
        self._fin_mes_atual = datetime.date.today().replace(day=1)

        return overlay

    # ================================================================ FINANCE HELPERS
    def _fin_mudar_mes(self, delta):
        """Change month and refresh."""
        import datetime
        year = self._fin_mes_atual.year
        month = self._fin_mes_atual.month + delta
        while month > 12:
            month -= 12
            year += 1
        while month < 1:
            month += 12
            year -= 1
        self._fin_mes_atual = datetime.date(year, month, 1)
        self._render_finance_dashboard()

    def _mostrar_finance_vista(self, vista):
        """Switch between dashboard, installments, calendar."""
        if self.finance_stack is not None:
            self.finance_stack.set_visible_child_name(vista)
        if vista == "dashboard":
            self._render_finance_dashboard()
        elif vista == "parcelas":
            self._render_finance_parcelas()
        elif vista == "calendario":
            self._render_finance_calendario_v2()

    def pedir_finance(self):
        """Request finance data from relay."""
        if callable(self.finance_listar_cb):
            self.finance_listar_cb()

    def aplicar_finance(self, expenses):
        """Apply finance data from relay."""
        self.expenses = expenses
        GLib.idle_add(lambda: (self._render_finance_dashboard(), False)[1])

    def _finance_parcela_toggle(self, expense_id, parcela_index, paga):
        """Toggle installment paid state."""
        self._bg(lambda: (hub_store.atualizar_parcela(expense_id, parcela_index, {"paga": bool(paga)}), self._finance_do_hub()))

    def _finance_do_hub(self):
        """Refresh from hub_store after mutation."""
        try:
            expenses = hub_store.listar_expenses()
            GLib.idle_add(lambda: (self.aplicar_finance(expenses), False)[1])
        except Exception as e:
            print(f"[FINANCE] refresh failed: {e}")

    # ================================================================ DASHBOARD RENDER
    def _render_finance_dashboard(self):
        """Render the main dashboard matching Android app."""
        if not hasattr(self, 'fin_dash_box') or self.fin_dash_box is None:
            return
        
        # Clear
        while (c := self.fin_dash_box.get_first_child()):
            self.fin_dash_box.remove(c)

        if not hasattr(self, 'expenses') or not self.expenses:
            self._render_finance_vazio()
            return

        # Filter expenses for current month
        import datetime
        mes = self._fin_mes_atual
        inicio_mes = int(datetime.datetime(mes.year, mes.month, 1).timestamp() * 1000)
        if mes.month == 12:
            fim_mes = int(datetime.datetime(mes.year + 1, 1, 1).timestamp() * 1000)
        else:
            fim_mes = int(datetime.datetime(mes.year, mes.month + 1, 1).timestamp() * 1000)

        # Update month label
        meses_en = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
        self.lbl_fin_mes.set_text(f"{meses_en[mes.month - 1]} {mes.year}")

        # Calculate totals
        despesas_mes = []
        total_mes = 0
        a_vencer_mes = 0
        parcelas_pendentes = []
        categorias_totais = {}

        for exp in self.expenses:
            if exp.get("removido"):
                continue
            purchase_date = exp.get("purchase_date", exp.get("created_at", 0))
            if inicio_mes <= purchase_date < fim_mes:
                despesas_mes.append(exp)
                total_mes += exp.get("total_amount", exp.get("total", 0))
                # Categorias
                cat = exp.get("category", exp.get("categoria", "other"))
                categorias_totais[cat] = categorias_totais.get(cat, 0) + exp.get("total_amount", exp.get("total", 0))
            
            # Parcelas a vencer neste mês
            for p in exp.get("parcelas", []):
                if p.get("paga"):
                    continue
                due = p.get("dueDate") or p.get("due_date")
                if due and inicio_mes <= due < fim_mes:
                    a_vencer_mes += p.get("valor", p.get("amount", 0))
                if not p.get("paga"):
                    parcelas_pendentes.append({
                        "expense_id": exp["id"],
                        "expense_title": exp.get("title") or exp.get("titulo") or "Expense",
                        "parcela": p,
                        "expense_category": exp.get("category", exp.get("categoria", "other")),
                    })

        # Sort pending installments by due date
        parcelas_pendentes.sort(key=lambda x: x["parcela"].get("dueDate", x["parcela"].get("due_date", 0)))

        # Update header counts
        self.lbl_finance_title.set_text(f"FINANCE  ·  {len(despesas_mes)} EXPENSES")

        # ---- MONTH CARD (SPENT / DUE) ----
        card_mes = self._criar_card_mes(total_mes, a_vencer_mes)
        self.fin_dash_box.append(card_mes)

        # ---- CATEGORIES CHART ----
        if categorias_totais:
            card_cat = self._criar_card_categorias(categorias_totais)
            self.fin_dash_box.append(card_cat)

        # ---- UPCOMING INSTALLMENTS ----
        if parcelas_pendentes:
            lbl_sec = Gtk.Label(label="UPCOMING INSTALLMENTS", xalign=0)
            lbl_sec.add_css_class("finance-section-title")
            self.fin_dash_box.append(lbl_sec)

            for p in parcelas_pendentes[:5]:  # Show first 5
                self.fin_dash_box.append(self._criar_card_parcela_dashboard(p))

        # ---- EXPENSES LIST ----
        lbl_sec2 = Gtk.Label(label=f"EXPENSES THIS MONTH ({len(despesas_mes)})", xalign=0)
        lbl_sec2.add_css_class("finance-section-title")
        self.fin_dash_box.append(lbl_sec2)

        if not despesas_mes:
            vazio = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            vazio.add_css_class("finance-vazio")
            ic = Gtk.Label(label="\ue870")  # Icons.Filled.CreditCard
            ic.add_css_class("fin-ico-empty")
            vazio.append(ic)
            t1 = Gtk.Label(label=f"No expenses in {meses_en[mes.month - 1]}")
            t1.add_css_class("finance-vazio-title")
            vazio.append(t1)
            t2 = Gtk.Label(label="Tap + to add")
            t2.add_css_class("finance-vazio-sub")
            vazio.append(t2)
            self.fin_dash_box.append(vazio)
        else:
            for exp in despesas_mes:
                self.fin_dash_box.append(self._criar_card_expense_dashboard(exp))

        # Sync status if any
        if hasattr(self, '_finance_sync_status') and self._finance_sync_status:
            status_lbl = Gtk.Label(label=self._finance_sync_status, xalign=0)
            status_lbl.add_css_class("finance-sync-status")
            self.fin_dash_box.append(status_lbl)

    def _criar_card_mes(self, total_mes, a_vencer_mes):
        """Card showing SPENT and DUE totals."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("finance-card-mes")

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        
        # Left arrow
        btn_prev = Gtk.Button()
        btn_prev.add_css_class("finance-nav-btn-sm")
        btn_prev.set_child(Gtk.Label(label="◀"))
        btn_prev.connect("clicked", lambda *_: self._fin_mudar_mes(-1))
        header.append(btn_prev)

        # Month label (centered)
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title_box.set_hexpand(True)
        self.lbl_fin_mes_card = Gtk.Label(label="", xalign=0.5)
        self.lbl_fin_mes_card.add_css_class("finance-card-mes-title")
        import datetime
        mes = self._fin_mes_atual
        meses_en = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
        self.lbl_fin_mes_card.set_text(f"{meses_en[mes.month - 1]} {mes.year}")
        title_box.append(self.lbl_fin_mes_card)
        header.append(title_box)

        # Right arrow
        btn_next = Gtk.Button()
        btn_next.add_css_class("finance-nav-btn-sm")
        btn_next.set_child(Gtk.Label(label="▶"))
        btn_next.connect("clicked", lambda *_: self._fin_mudar_mes(1))
        header.append(btn_next)

        card.append(header)

        # Two columns: SPENT | DUE
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row.set_hexpand(True)

        col1 = self._coluna_total("SPENT", self._fmt_brl(total_mes), "finance-green")
        col2 = self._coluna_total("DUE", self._fmt_brl(a_vencer_mes), "finance-amber")
        
        row.append(col1)
        # Divider
        div = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        div.add_css_class("finance-divider")
        row.append(div)
        row.append(col2)

        card.append(row)
        return card

    def _coluna_total(self, rotulo, valor, cor_class):
        """Column for SPENT/DUE."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_hexpand(True)
        box.set_halign(Gtk.Align.CENTER)
        
        lbl_rot = Gtk.Label(label=rotulo, xalign=0.5)
        lbl_rot.add_css_class("finance-total-label")
        box.append(lbl_rot)

        lbl_val = Gtk.Label(label=valor, xalign=0.5)
        lbl_val.add_css_class("finance-total-value")
        lbl_val.add_css_class(cor_class)
        box.append(lbl_val)
        
        return box

    def _criar_card_categorias(self, categorias_totais):
        """Bar chart by category matching Android."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("finance-card")

        title = Gtk.Label(label="BY CATEGORY", xalign=0)
        title.add_css_class("finance-card-title")
        card.append(title)

        # Sort by total desc
        sorted_cats = sorted(categorias_totais.items(), key=lambda x: x[1], reverse=True)[:6]
        max_val = max(v for _, v in sorted_cats) if sorted_cats else 1

        # Android colors for categories
        cat_colors = [
            "finance-cat-0", "finance-cat-1", "finance-cat-2",
            "finance-cat-3", "finance-cat-4", "finance-cat-5"
        ]

        bars_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bars_box.set_hexpand(True)
        bars_box.set_halign(Gtk.Align.CENTER)
        bars_box.set_valign(Gtk.Align.END)
        bars_box.set_size_request(-1, 100)

        for idx, (cat, total) in enumerate(sorted_cats):
            height_pct = (total / max_val) * 64 if max_val > 0 else 4
            height_pct = max(height_pct, 4)
            
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            col.set_hexpand(True)
            col.set_halign(Gtk.Align.CENTER)
            col.set_valign(Gtk.Align.END)

            bar = Gtk.Box()
            bar.set_hexpand(True)
            bar.set_size_request(-1, int(height_pct))
            bar.add_css_class("finance-bar")
            bar.add_css_class(cat_colors[idx % len(cat_colors)])
            col.append(bar)

            lbl_cat = Gtk.Label(label=cat[:8].upper(), xalign=0.5)
            lbl_cat.add_css_class("finance-bar-label")
            col.append(lbl_cat)

            lbl_val = Gtk.Label(label=self._fmt_brl(total), xalign=0.5)
            lbl_val.add_css_class("finance-bar-value")
            col.append(lbl_val)

            bars_box.append(col)

        card.append(bars_box)
        return card

    def _criar_card_parcela_dashboard(self, p):
        """Installment card for dashboard (clickable)."""
        parc = p["parcela"]
        due = parc.get("dueDate") or parc.get("due_date")
        now_ms = int(time.time() * 1000)
        atrasada = due and due < now_ms and not parc.get("paga", False)
        paga = parc.get("paga", False)

        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        card.add_css_class("finance-parcela-card")
        if atrasada:
            card.add_css_class("finance-parcela-overdue")
        if paga:
            card.add_css_class("finance-parcela-paid")

        # Left: title + due date
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        left.set_hexpand(True)

        lbl_title = Gtk.Label(label=f'Installment {p.get("parcela_index", 0)+1}', xalign=0)
        lbl_title.add_css_class("finance-parcela-title")
        left.append(lbl_title)

        due_str = ""
        if due:
            from datetime import datetime as dt
            due_str = dt.fromtimestamp(due / 1000).strftime("%m/%d")
            if atrasada:
                due_str += " · OVERDUE"
        lbl_due = Gtk.Label(label=due_str, xalign=0)
        lbl_due.add_css_class("finance-parcela-due")
        if atrasada:
            lbl_due.add_css_class("finance-overdue")
        elif paga:
            lbl_due.add_css_class("finance-paid")
        left.append(lbl_due)

        card.append(left)

        # Right: amount + check
        right = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        right.set_valign(Gtk.Align.CENTER)

        valor = parc.get("valor", parc.get("amount", 0))
        lbl_val = Gtk.Label(label=self._fmt_brl(valor), xalign=1)
        lbl_val.add_css_class("finance-parcela-valor")
        right.append(lbl_val)

        if paga:
            chk = Gtk.Label(label="\ue5ca")  # Icons.Filled.Check
            chk.add_css_class("fin-ico-check")
            chk.add_css_class("finance-check-paid")
            right.append(chk)

        card.append(right)

        # Click to toggle
        def on_click(gesture, n_press, x, y, data=p):
            self._toggle_parcela(data["expense_id"], data["parcela"].get("parcela_index", 0), not data["parcela"].get("paga", False))

        gesture = Gtk.GestureClick()
        gesture.connect("pressed", on_click)
        card.add_controller(gesture)

        return card

    def _criar_card_expense_dashboard(self, exp):
        """Expense card for dashboard list."""
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        card.add_css_class("finance-expense-card")

        # Left: description + category/date
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        left.set_hexpand(True)

        title = exp.get("title") or exp.get("titulo") or exp.get("description") or "Expense"
        lbl_title = Gtk.Label(label=title, xalign=0)
        lbl_title.add_css_class("finance-expense-title")
        left.append(lbl_title)

        cat = exp.get("category", exp.get("categoria", "other"))
        purchase = exp.get("purchase_date", exp.get("created_at", 0))
        from datetime import datetime as dt
        date_str = dt.fromtimestamp(purchase / 1000).strftime("%m/%d/%Y") if purchase else ""
        lbl_meta = Gtk.Label(label=f"{cat} · {date_str}", xalign=0)
        lbl_meta.add_css_class("finance-expense-meta")
        left.append(lbl_meta)

        installments = exp.get("installments", 1)
        if installments > 1:
            inst_amt = exp.get("installment_amount", exp.get("installmentAmount", exp.get("valorParcela", 0)))
            lbl_inst = Gtk.Label(label=f"{installments}x · {self._fmt_brl(inst_amt)}", xalign=0)
            lbl_inst.add_css_class("finance-expense-inst")
            left.append(lbl_inst)

        card.append(left)

        # Right: total + sync status
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        right.set_halign(Gtk.Align.END)

        total = exp.get("total_amount", exp.get("total", 0))
        lbl_total = Gtk.Label(label=self._fmt_brl(total), xalign=1)
        lbl_total.add_css_class("finance-expense-total")
        right.append(lbl_total)

        if not exp.get("synced", True):
            lbl_sync = Gtk.Label(label="pending", xalign=1)
            lbl_sync.add_css_class("finance-sync-pending")
            right.append(lbl_sync)

        card.append(right)

        # Delete button
        btn_del = Gtk.Button()
        btn_del.add_css_class("finance-btn-delete")
        btn_del.set_child(self._icone_msi("\ue872", "fin-ico-del"))  # Icons.Filled.Delete
        btn_del.connect("clicked", lambda *_, eid=exp["id"]: self._confirm_delete_expense(eid))
        card.append(btn_del)

        # Click to edit
        def on_click(gesture, n_press, x, y, e=exp):
            self._abrir_form_expense(e)

        gesture = Gtk.GestureClick()
        gesture.connect("pressed", on_click)
        card.add_controller(gesture)

        return card

    def _render_finance_vazio(self):
        """Empty state."""
        vazio = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vazio.add_css_class("finance-vazio")
        vazio.set_vexpand(True)
        vazio.set_halign(Gtk.Align.CENTER)
        vazio.set_valign(Gtk.Align.CENTER)

        ic = Gtk.Label(label="\ue870")  # Icons.Filled.CreditCard
        ic.add_css_class("fin-ico-empty")
        vazio.append(ic)

        t1 = Gtk.Label(label="No expenses yet")
        t1.add_css_class("finance-vazio-title")
        vazio.append(t1)

        t2 = Gtk.Label(label="Tap + to add your first expense")
        t2.add_css_class("finance-vazio-sub")
        vazio.append(t2)

        self.fin_dash_box.append(vazio)

    # ================================================================ INSTALLMENTS LIST
    def _render_finance_parcelas(self):
        """Render all installments list."""
        if not hasattr(self, 'fin_parcelas_box') or self.fin_parcelas_box is None:
            return

        while (c := self.fin_parcelas_box.get_first_child()):
            self.fin_parcelas_box.remove(c)

        if not hasattr(self, 'expenses') or not self.expenses:
            lbl = Gtk.Label(label="No installments", xalign=0.5)
            lbl.add_css_class("finance-vazio")
            lbl.set_vexpand(True)
            self.fin_parcelas_box.append(lbl)
            return

        all_parcelas = []
        for exp in self.expenses:
            if exp.get("removido"):
                continue
            for i, p in enumerate(exp.get("parcelas", [])):
                all_parcelas.append({
                    "expense_id": exp["id"],
                    "expense_title": exp.get("title") or exp.get("titulo") or "Expense",
                    "expense_category": exp.get("category", exp.get("categoria", "other")),
                    "parcela_index": i,
                    "parcela": p,
                })

        all_parcelas.sort(key=lambda x: x["parcela"].get("dueDate", x["parcela"].get("due_date", 0)))

        for p in all_parcelas:
            self.fin_parcelas_box.append(self._criar_card_parcela_full(p))

    def _criar_card_parcela_full(self, p):
        """Full installment card with toggle button."""
        parc = p["parcela"]
        due = parc.get("dueDate") or parc.get("due_date")
        now_ms = int(time.time() * 1000)
        atrasada = due and due < now_ms and not parc.get("paga", False)
        paga = parc.get("paga", False)

        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        card.add_css_class("finance-parcela-card")
        if atrasada:
            card.add_css_class("finance-parcela-overdue")
        if paga:
            card.add_css_class("finance-parcela-paid")

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        left.set_hexpand(True)

        lbl_title = Gtk.Label(label=f'{p["expense_title"]} · Installment {p["parcela_index"]+1}', xalign=0)
        lbl_title.add_css_class("finance-parcela-title")
        left.append(lbl_title)

        due_str = ""
        if due:
            from datetime import datetime as dt
            due_str = dt.fromtimestamp(due / 1000).strftime("%m/%d/%Y")
            if atrasada:
                due_str += " · OVERDUE"
        lbl_due = Gtk.Label(label=due_str, xalign=0)
        lbl_due.add_css_class("finance-parcela-due")
        if atrasada:
            lbl_due.add_css_class("finance-overdue")
        elif paga:
            lbl_due.add_css_class("finance-paid")
        left.append(lbl_due)

        card.append(left)

        right = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        right.set_valign(Gtk.Align.CENTER)

        valor = parc.get("valor", parc.get("amount", 0))
        lbl_val = Gtk.Label(label=self._fmt_brl(valor), xalign=1)
        lbl_val.add_css_class("finance-parcela-valor")
        right.append(lbl_val)

        # Toggle button
        btn_toggle = Gtk.Button()
        btn_toggle.add_css_class("finance-toggle-btn")
        if paga:
            btn_toggle.add_css_class("finance-toggle-on")
        # Label filho com a classe: garante a fonte Material Symbols (herdar a
        # fonte do botao nao funciona no GTK4).
        btn_toggle.set_child(self._icone_msi("\ue834" if paga else "\ue835", "fin-ico-check"))
        btn_toggle.connect("clicked", lambda *_, pid=p["expense_id"], pi=p["parcela_index"]: self._toggle_parcela(pid, pi, not paga))
        right.append(btn_toggle)

        card.append(right)
        return card

    def _toggle_parcela(self, expense_id, parcela_index, paga):
        """Toggle installment paid state."""
        if callable(self.finance_parcela_cb):
            self.finance_parcela_cb(expense_id, parcela_index, paga)

    # ================================================================ CALENDAR V2
    def _criar_calendario_finance_v2(self):
        """Create calendar view matching Android app (Sunday-first grid)."""
        import datetime

        caixa = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        caixa.add_css_class("spook-root")

        # Header
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        
        btn_back = Gtk.Button()
        btn_back.add_css_class("finance-cal-nav")
        btn_back.set_child(self._icone_msi("\ue5c4", "fin-nav-back"))  # arrow_back
        btn_back.connect("clicked", lambda *_: self._mostrar_finance_vista("dashboard"))
        hdr.append(btn_back)

        self.fin_cal_mes_ano = Gtk.Label(label="", xalign=0.5)
        self.fin_cal_mes_ano.add_css_class("finance-cal-title")
        self.fin_cal_mes_ano.set_hexpand(True)
        hdr.append(self.fin_cal_mes_ano)

        btn_prev = Gtk.Button()
        btn_prev.add_css_class("finance-cal-nav")
        btn_prev.set_child(self._icone_msi("\ue5c4", "fin-nav-prev"))  # chevron_left
        btn_prev.connect("clicked", lambda *_: self._fin_cal_mudar_mes(-1))
        hdr.append(btn_prev)

        btn_today = Gtk.Button(label="TODAY")
        btn_today.add_css_class("finance-cal-nav")
        btn_today.connect("clicked", lambda *_: self._fin_cal_ir_hoje())
        hdr.append(btn_today)

        btn_next = Gtk.Button()
        btn_next.add_css_class("finance-cal-nav")
        btn_next.set_child(self._icone_msi("\ue5c8", "fin-nav-next"))  # chevron_right
        btn_next.connect("clicked", lambda *_: self._fin_cal_mudar_mes(1))
        hdr.append(btn_next)

        caixa.append(hdr)

        # Calendar grid with weekday headers in row 0 (Sunday-first)
        self.fin_cal_grid = Gtk.Grid()
        self.fin_cal_grid.set_row_spacing(2)
        self.fin_cal_grid.set_column_spacing(2)
        self.fin_cal_grid.set_margin_top(8)
        self.fin_cal_grid.set_margin_bottom(8)
        self.fin_cal_grid.set_column_homogeneous(True)

        # Weekday headers (row 0)
        weekdays = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]
        for col, wd in enumerate(weekdays):
            lbl = Gtk.Label(label=wd, xalign=0.5)
            lbl.add_css_class("finance-cal-weekday")
            lbl.set_hexpand(True)
            lbl.set_size_request(-1, 28)
            self.fin_cal_grid.attach(lbl, col, 0, 1, 1)

        cal_scroll = Gtk.ScrolledWindow()
        cal_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        cal_scroll.set_vexpand(True)
        cal_scroll.set_child(self.fin_cal_grid)
        caixa.append(cal_scroll)

        # Installments list below calendar
        self.fin_cal_parcelas_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        parcelas_scroll = Gtk.ScrolledWindow()
        parcelas_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        parcelas_scroll.set_vexpand(True)
        parcelas_scroll.set_child(self.fin_cal_parcelas_box)
        caixa.append(parcelas_scroll)

        # Initialize to current month
        self._fin_cal_mes_atual = datetime.date.today().replace(day=1)
        return caixa

    def _fin_cal_mudar_mes(self, delta):
        import datetime
        year = self._fin_cal_mes_atual.year
        month = self._fin_cal_mes_atual.month + delta
        while month > 12:
            month -= 12
            year += 1
        while month < 1:
            month += 12
            year -= 1
        self._fin_cal_mes_atual = datetime.date(year, month, 1)
        self._render_finance_calendario_v2()

    def _fin_cal_ir_hoje(self):
        import datetime
        self._fin_cal_mes_atual = datetime.date.today().replace(day=1)
        self._render_finance_calendario_v2()

    def _render_finance_calendario_v2(self):
        """Render calendar matching Android app (Sunday first, row 0 = weekdays)."""
        import datetime
        import calendar as cal_mod

        # Clear grid (keep row 0 weekday headers)
        children = list(self.fin_cal_grid)
        for c in children:
            child = c.get_child() if hasattr(c, 'get_child') else c
            # Get position
            if hasattr(self.fin_cal_grid, 'child_get_property'):
                pass
            # Simple approach: remove all, then re-add weekday headers
        # Rebuild grid entirely for simplicity
        while (c := self.fin_cal_grid.get_first_child()):
            self.fin_cal_grid.remove(c)

        # Re-add weekday headers (row 0)
        weekdays = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]
        for col, wd in enumerate(weekdays):
            lbl = Gtk.Label(label=wd, xalign=0.5)
            lbl.add_css_class("finance-cal-weekday")
            lbl.set_hexpand(True)
            lbl.set_size_request(-1, 28)
            self.fin_cal_grid.attach(lbl, col, 0, 1, 1)

        # Clear installments list
        while (c := self.fin_cal_parcelas_box.get_first_child()):
            self.fin_cal_parcelas_box.remove(c)

        mes = self._fin_cal_mes_atual
        meses_en = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
                    "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]
        self.fin_cal_mes_ano.set_text(f"{meses_en[mes.month - 1]} {mes.year}")

        # Sunday first
        cal_mod.setfirstweekday(cal_mod.SUNDAY)
        # mes is first day of month; weekday() returns Mon=0, Sun=6
        # Convert to Sun=0: (weekday + 1) % 7
        primeiro_dia_dom = (mes.weekday() + 1) % 7
        dias_no_mes = cal_mod.monthrange(mes.year, mes.month)[1]
        hoje = datetime.date.today()
        now_ms = int(time.time() * 1000)

        # Group installments by day (including paid for color coding)
        parcelas_por_dia = {}
        parcelas_com_venc = []
        tasks_por_dia = {}
        
        if hasattr(self, 'expenses') and self.expenses:
            for exp in self.expenses:
                if exp.get("removido"):
                    continue
                forma = (exp.get("payment_method") or exp.get("formaPagamento") or "").lower()
                is_cartao_boleto = forma in ("credit card", "boleto", "cartão de crédito", "cartao de credito")
                for i, p in enumerate(exp.get("parcelas", [])):
                    due = p.get("dueDate") or p.get("due_date")
                    if not due:
                        continue
                    try:
                        dt = datetime.datetime.fromtimestamp(due / 1000).date()
                        if dt.year == mes.year and dt.month == mes.month:
                            paga = bool(p.get("paga", False))
                            # For credit card/boleto: show as pending until ALL installments paid
                            if is_cartao_boleto:
                                todas_pagas = all(parc.get("paga", False) for parc in exp.get("parcelas", []))
                                if todas_pagas:
                                    paga = True  # Show as paid only when all are paid
                            parcelas_por_dia.setdefault(dt.day, []).append({
                                "expense_id": exp["id"],
                                "expense_title": exp.get("title") or exp.get("titulo") or "Expense",
                                "parcela_index": i,
                                "valor": p.get("valor", p.get("amount", 0)),
                                "due_date": due,
                                "paga": paga,
                                "is_cartao_boleto": is_cartao_boleto,
                            })
                        parcelas_com_venc.append({
                            "expense_id": exp["id"],
                            "expense_title": exp.get("title") or exp.get("titulo") or "Expense",
                            "parcela_index": i,
                            "valor": p.get("valor", p.get("amount", 0)),
                            "due_date": due,
                            "paga": p.get("paga", False),
                            "is_cartao_boleto": is_cartao_boleto,
                        })
                    except Exception:
                        pass

        # Add tasks with due dates to calendar
        if hasattr(self, 'tasks') and self.tasks:
            for t in self.tasks:
                if t.get("feito"):
                    continue
                due = t.get("due_date") or t.get("dueDate")
                if not due:
                    continue
                try:
                    dt = datetime.datetime.fromtimestamp(due / 1000).date()
                    if dt.year == mes.year and dt.month == mes.month:
                        tasks_por_dia.setdefault(dt.day, []).append({
                            "task_id": t.get("id"),
                            "task_title": t.get("titulo") or t.get("title") or "Task",
                            "due_date": due,
                        })
                except Exception:
                    pass

        parcelas_com_venc.sort(key=lambda p: p.get("due_date", 0))

        # Fill calendar days (rows 1-6, columns 0-6)
        dia = 1
        for semana in range(6):
            for col in range(7):
                grid_row = semana + 1  # Row 0 is weekdays
                if semana == 0 and col < primeiro_dia_dom:
                    box = Gtk.Box()
                    box.set_size_request(40, 50)
                    self.fin_cal_grid.attach(box, col, grid_row, 1, 1)
                elif dia <= dias_no_mes:
                    cell = self._criar_celula_calendario_v2(dia, mes, hoje, 
                        parcelas_por_dia.get(dia, []), tasks_por_dia.get(dia, []), now_ms)
                    self.fin_cal_grid.attach(cell, col, grid_row, 1, 1)
                    dia += 1
                else:
                    box = Gtk.Box()
                    box.set_size_request(40, 50)
                    self.fin_cal_grid.attach(box, col, grid_row, 1, 1)

        # Render installments list (all installments for the month, with colors)
        if not parcelas_com_venc:
            lbl = Gtk.Label(label="No installments this month", xalign=0.5)
            lbl.add_css_class("finance-vazio")
            lbl.set_vexpand(True)
            self.fin_cal_parcelas_box.append(lbl)
        else:
            for p in parcelas_com_venc:
                self.fin_cal_parcelas_box.append(self._criar_card_parcela_calendario_v2(p))

    def _criar_celula_calendario_v2(self, dia, mes, hoje, parcelas_dia, tasks_dia, now_ms):
        """Create calendar cell matching Android - shows installments and tasks."""
        import datetime
        eh_hoje = (dia == hoje.day and mes.month == hoje.month and mes.year == hoje.year)
        tem_parcela = len(parcelas_dia) > 0
        tem_task = len(tasks_dia) > 0
        tem_venc = tem_parcela or tem_task
        tem_atrasada = any(p.get("due_date", 0) < now_ms for p in parcelas_dia)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_size_request(40, 50)
        box.set_valign(Gtk.Align.START)
        box.set_halign(Gtk.Align.FILL)
        box.add_css_class("finance-cal-cell")

        if eh_hoje:
            box.add_css_class("finance-cal-cell-today")
        elif tem_atrasada:
            box.add_css_class("finance-cal-cell-overdue")
        elif tem_venc:
            box.add_css_class("finance-cal-cell-due")

        lbl_dia = Gtk.Label(label=str(dia), xalign=0.5)
        lbl_dia.add_css_class("finance-cal-day")
        if eh_hoje:
            lbl_dia.add_css_class("finance-cal-day-today")
        box.append(lbl_dia)

        if tem_venc:
            dots_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
            dots_box.set_halign(Gtk.Align.CENTER)
            dots_box.add_css_class("finance-cal-dots")
            
            # Installment dots (max 2)
            for p in parcelas_dia[:2]:
                dot = Gtk.Box()
                dot.set_size_request(6, 6)
                paga = p.get("paga", False)
                is_cartao_boleto = p.get("is_cartao_boleto", False)
                if paga:
                    dot.add_css_class("finance-cal-dot-paid")  # Cyan
                elif is_cartao_boleto:
                    dot.add_css_class("finance-cal-dot-pending")  # Amber (credit card/boleto always pending)
                elif p.get("due_date", 0) < now_ms:
                    dot.add_css_class("finance-cal-dot-overdue")  # Red
                else:
                    dot.add_css_class("finance-cal-dot-pending")  # Amber
                dots_box.append(dot)
            
            # Task indicator (calendar icon) if there are tasks
            if tem_task:
                task_dot = Gtk.Label()
                task_dot.set_markup('<span font_family="Material Symbols Rounded" size="small">\ue916</span>')
                task_dot.add_css_class("finance-cal-task-dot")
                dots_box.append(task_dot)
            
            if len(parcelas_dia) + len(tasks_dia) > 3:
                mais = Gtk.Label(label=f"+{len(parcelas_dia) + len(tasks_dia) - 3}", xalign=0.5)
                mais.add_css_class("finance-cal-plus")
                dots_box.append(mais)
            box.append(dots_box)

        return box

    def _criar_card_parcela_calendario_v2(self, p):
        """Installment card for calendar list - shows pending/paid with colors."""
        import time as time_mod
        now_ms = int(time_mod.time() * 1000)
        due_ms = p.get("due_date", 0)
        atrasada = due_ms < now_ms
        paga = bool(p.get("paga", False))
        is_cartao_boleto = p.get("is_cartao_boleto", False)
        
        # For credit card/boleto: show as pending until ALL installments paid
        # The paga flag already reflects this logic from _render_finance_calendario_v2
        status_pendente = not paga

        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        card.add_css_class("finance-parcela-card")
        if atrasada and status_pendente:
            card.add_css_class("finance-parcela-overdue")
        elif paga:
            card.add_css_class("finance-parcela-paid")

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        left.set_hexpand(True)

        # Title with payment method indicator
        title_parts = [p["expense_title"], f'Installment {p["parcela_index"]+1}']
        if is_cartao_boleto:
            title_parts.append("💳" if "credit" in (p.get("payment_method") or "").lower() else "📄")
        lbl_title = Gtk.Label(label=" · ".join(title_parts), xalign=0)
        lbl_title.add_css_class("finance-parcela-title")
        left.append(lbl_title)

        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        valor = p.get("valor", 0)
        lbl_val = Gtk.Label(label=self._fmt_brl(valor), xalign=0)
        lbl_val.add_css_class("finance-parcela-valor")
        if paga:
            lbl_val.add_css_class("finance-paid")  # Cyan
        elif atrasada:
            lbl_val.add_css_class("finance-overdue")  # Red
        else:
            lbl_val.add_css_class("finance-pending")  # Amber
        meta.append(lbl_val)

        from datetime import datetime as dt
        try:
            due_str = dt.fromtimestamp(due_ms / 1000).strftime("%m/%d")
            status_text = " · PAID" if paga else (" · OVERDUE" if atrasada else " · PENDING")
            due_lbl = Gtk.Label(xalign=0)
            due_lbl.set_markup(
                f'<span font_family="Material Symbols Rounded">\ue916</span> {due_str}{status_text}'
            )
            due_lbl.add_css_class("finance-parcela-meta")
            due_lbl.set_use_markup(True)
            if paga:
                due_lbl.add_css_class("finance-paid")
            elif atrasada:
                due_lbl.add_css_class("finance-overdue")
            else:
                due_lbl.add_css_class("finance-warn")  # Amber
            meta.append(due_lbl)
        except Exception:
            pass

        left.append(meta)
        card.append(left)

        # Toggle button - shows check_box (filled) for paid, check_box_outline_blank for pending
        btn_toggle = Gtk.Button()
        btn_toggle.add_css_class("finance-toggle-btn")
        if paga:
            btn_toggle.add_css_class("finance-toggle-on")
        btn_toggle.set_child(self._icone_msi("\ue834" if paga else "\ue835", "fin-ico-check"))
        btn_toggle.connect("clicked", lambda *_, pid=p["expense_id"], pi=p["parcela_index"]: self._toggle_parcela(pid, pi, not paga))
        card.append(btn_toggle)

        return card

    # ================================================================ EXPENSE FORM
    def _abrir_form_expense(self, expense):
        """Open expense form dialog matching Android."""
        import datetime
        
        editando = expense is not None
        expense = expense or {}

        # Os campos registram-se em `_fin_form_data["widgets"]` logo ao serem
        # criados: o dicionario TEM que existir antes de montar o formulario.
        self._fin_form_data = {
            "editando": editando,
            "expense_id": expense.get("id") if editando else None,
            "origem": expense.get("origem", "arch") if editando else "arch",
            "widgets": {
                "finance_desc": None,
                "finance_total": None,
                "finance_cat": None,
                "finance_purchase_date": None,
                "finance_parcelas": None,
                "finance_valor_parcela": None,
                "finance_first_due": None,
                "finance_payment": None,
            },
        }

        dialog = Gtk.Window()
        dialog.set_title("EDIT EXPENSE" if editando else "NEW EXPENSE")
        dialog.set_modal(True)
        dialog.set_transient_for(self.app.janela)
        dialog.set_default_size(480, 640)
        dialog.add_css_class("finance-form-dialog")

        # Main scrollable content
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add_css_class("finance-form-scroll")
        
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        
        # Description
        content.append(self._criar_campo_texto("Description", 
            expense.get("description") or expense.get("title") or expense.get("titulo") or "",
            "finance_desc", placeholder="e.g. Supermarket, electricity bill..."))

        # Total amount
        total_val = expense.get("total_amount", expense.get("total", 0)) if editando else 0
        content.append(self._criar_campo_moeda("Total (R$)", total_val, "finance_total"))

        # Category
        categorias = ["food", "housing", "transport", "health", "leisure", "education", "subscriptions", "other"]
        cat_atual = expense.get("category", expense.get("categoria", "other")) if editando else "other"
        content.append(self._criar_campo_dropdown("Category", cat_atual, categorias, "finance_cat"))

        # Purchase date
        purchase_ms = expense.get("purchase_date", expense.get("created_at", int(time.time() * 1000))) if editando else int(time.time() * 1000)
        content.append(self._criar_campo_data("Purchase Date", purchase_ms, "finance_purchase_date"))

        # Divider
        sep = Gtk.Separator()
        sep.add_css_class("finance-form-sep")
        content.append(sep)

        # Installments label
        lbl_inst = Gtk.Label(label="INSTALLMENTS", xalign=0)
        lbl_inst.add_css_class("finance-form-section")
        content.append(lbl_inst)

        # Number of installments
        installments = expense.get("installments", 1) if editando else 1
        content.append(self._criar_campo_numero("Number of installments", installments, "finance_parcelas", 1, 60))

        # Installment amount
        inst_amt = 0
        if editando:
            inst_amt = expense.get(
                "installment_amount",
                expense.get("installmentAmount", expense.get("valorParcela", 0)),
            )
        content.append(self._criar_campo_moeda("Installment amount (R$)", inst_amt, "finance_valor_parcela"))

        # First due date
        first_due = expense.get(
            "first_due_date",
            expense.get("firstDueDate", expense.get("primeiroVencimento", int(time.time() * 1000))),
        ) if editando else int(time.time() * 1000)
        content.append(self._criar_campo_data("First due date", first_due, "finance_first_due"))

        # Payment method
        formas = ["pix", "credit card", "debit card", "boleto", "cash", "transfer"]
        forma_atual = expense.get("payment_method", expense.get("formaPagamento", "pix")) if editando else "pix"
        # Normalize
        forma_map = {
            "cartão de crédito": "credit card",
            "cartão de débito": "debit card",
            "dinheiro": "cash",
            "transferência": "transfer",
        }
        forma_atual = forma_map.get(forma_atual.lower(), forma_atual)
        content.append(self._criar_campo_dropdown("Payment method", forma_atual, formas, "finance_payment"))

        # Installment preview (if > 1 and not immediate payment)
        try:
            inst_amt = float(inst_amt or 0)
        except (TypeError, ValueError):
            inst_amt = 0.0
        self._fin_form_parcelas = installments
        self._fin_form_valor_parcela = inst_amt
        self._fin_form_first_due = first_due
        self._fin_form_payment = forma_atual
        self._fin_form_is_avista = forma_atual.lower() in {"pix", "debit card", "cash", "transfer", "boleto"}
        
        if installments > 1 and not self._fin_form_is_avista:
            self._fin_preview_box = self._criar_preview_parcelas(installments, inst_amt, first_due)
            content.append(self._fin_preview_box)

        # Buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_halign(Gtk.Align.END)
        btn_box.set_margin_top(16)

        if editando:
            btn_del = Gtk.Button(label="DELETE")
            btn_del.add_css_class("finance-btn-delete")
            btn_del.connect("clicked", lambda *_: self._confirm_delete_expense_form(expense["id"], dialog))
            btn_box.append(btn_del)

        btn_cancel = Gtk.Button(label="CANCEL")
        btn_cancel.add_css_class("finance-btn-cancel")
        btn_cancel.connect("clicked", lambda *_: dialog.close())
        btn_box.append(btn_cancel)

        btn_save = Gtk.Button(label="SAVE")
        btn_save.add_css_class("finance-btn-save")
        btn_save.connect("clicked", lambda *_: self._salvar_expense_form(dialog))
        btn_box.append(btn_save)

        content.append(btn_box)
        scroll.set_child(content)
        dialog.set_child(scroll)
        dialog.present()

    def _criar_campo_texto(self, label, valor, key, placeholder=""):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        lbl = Gtk.Label(label=label, xalign=0)
        lbl.add_css_class("finance-form-label")
        box.append(lbl)

        entry = Gtk.Entry()
        entry.set_text(str(valor))
        entry.set_placeholder_text(placeholder)
        entry.add_css_class("finance-form-entry")
        entry.connect("changed", lambda e, k=key: self._fin_form_widget_changed(k, e.get_text()))
        box.append(entry)
        self._fin_form_data["widgets"][key] = entry
        return box

    def _criar_campo_moeda(self, label, valor_reais, key):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        lbl = Gtk.Label(label=label, xalign=0)
        lbl.add_css_class("finance-form-label")
        box.append(lbl)

        entry = Gtk.Entry()
        try:
            v = float(valor_reais)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            entry.set_text(f"{v:.2f}".replace(".", ","))
        entry.set_placeholder_text("0,00")
        entry.set_input_purpose(Gtk.InputPurpose.NUMBER)
        entry.add_css_class("finance-form-entry")
        entry.connect("changed", lambda e, k=key: self._fin_form_widget_changed(k, e.get_text()))
        box.append(entry)
        self._fin_form_data["widgets"][key] = entry
        return box

    def _criar_campo_numero(self, label, valor, key, min_val, max_val):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        lbl = Gtk.Label(label=label, xalign=0)
        lbl.add_css_class("finance-form-label")
        box.append(lbl)

        spin = Gtk.SpinButton.new_with_range(min_val, max_val, 1)
        spin.set_value(valor)
        spin.add_css_class("finance-form-entry")
        spin.connect("value-changed", lambda s, k=key: self._fin_form_widget_changed(k, int(s.get_value())))
        box.append(spin)
        self._fin_form_data["widgets"][key] = spin
        return box

    def _criar_campo_dropdown(self, label, valor, opcoes, key):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        lbl = Gtk.Label(label=label, xalign=0)
        lbl.add_css_class("finance-form-label")
        box.append(lbl)

        combo = Gtk.ComboBoxText()
        for opt in opcoes:
            combo.append_text(opt)
        combo.set_active(opcoes.index(valor) if valor in opcoes else 0)
        combo.add_css_class("finance-form-combo")
        combo.connect("changed", lambda c, k=key: self._fin_form_widget_changed(k, c.get_active_text()))
        box.append(combo)
        self._fin_form_data["widgets"][key] = combo
        return box

    def _criar_campo_data(self, label, valor_ms, key):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        lbl = Gtk.Label(label=label, xalign=0)
        lbl.add_css_class("finance-form-label")
        box.append(lbl)

        btn = Gtk.Button()
        btn.add_css_class("finance-form-date-btn")
        if valor_ms > 0:
            from datetime import datetime as dt
            btn.set_label(dt.fromtimestamp(valor_ms / 1000).strftime("%m/%d/%Y"))
        else:
            btn.set_label("Select date")
        btn.connect("clicked", lambda *_, k=key: self._abrir_date_picker(k))
        box.append(btn)
        self._fin_form_data["widgets"][key] = btn
        return box

    def _fin_form_widget_changed(self, key, value):
        """Update form state when widget changes."""
        if key == "finance_parcelas":
            self._fin_form_parcelas = value
        elif key == "finance_valor_parcela":
            # O campo de moeda do formulario fala REAIS (igual ao relay).
            try:
                self._fin_form_valor_parcela = float(str(value).replace(",", "."))
            except (TypeError, ValueError):
                self._fin_form_valor_parcela = 0.0
            self._atualizar_preview_parcelas()
        elif key == "finance_first_due":
            self._fin_form_first_due = value
        elif key == "finance_payment":
            self._fin_form_payment = value
            self._fin_form_is_avista = value.lower() in {"pix", "debit card", "cash", "transfer", "boleto"}
            # Update installments if payment method changed
            if self._fin_form_is_avista:
                self._fin_form_parcelas = 1
                if "finance_parcelas" in self._fin_form_data["widgets"]:
                    w = self._fin_form_data["widgets"]["finance_parcelas"]
                    if isinstance(w, Gtk.SpinButton):
                        w.set_value(1)
            # Update preview
            self._atualizar_preview_parcelas()
        elif key == "finance_purchase_date" and self._fin_form_is_avista:
            # For immediate payments, first due = purchase date
            self._fin_form_first_due = value
            if "finance_first_due" in self._fin_form_data["widgets"]:
                w = self._fin_form_data["widgets"]["finance_first_due"]
                if isinstance(w, Gtk.Button):
                    from datetime import datetime as dt
                    w.set_label(dt.fromtimestamp(value / 1000).strftime("%m/%d/%Y"))

    def _criar_preview_parcelas(self, parcelas, valor_parcela, first_due):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("finance-preview-box")

        lbl = Gtk.Label(label="INSTALLMENT PREVIEW", xalign=0)
        lbl.add_css_class("finance-preview-title")
        box.append(lbl)

        import datetime
        cal = datetime.datetime.fromtimestamp(first_due / 1000)
        
        for i in range(1, parcelas + 1):
            if i > 1:
                cal = cal + datetime.timedelta(days=30)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.set_margin_start(8)
            row.set_margin_end(8)
            
            lbl_num = Gtk.Label(label=f"Installment {i}", xalign=0)
            lbl_num.add_css_class("finance-preview-item")
            row.append(lbl_num)

            lbl_val = Gtk.Label(label=f"{self._fmt_brl(valor_parcela)} · {cal.strftime('%m/%d/%Y')}", xalign=1)
            lbl_val.add_css_class("finance-preview-item")
            lbl_val.set_hexpand(True)
            row.append(lbl_val)

            box.append(row)

        return box

    def _atualizar_preview_parcelas(self):
        """Update installment preview when form changes."""
        if not hasattr(self, '_fin_preview_box') or self._fin_preview_box is None:
            return
        
        # Remove old preview
        parent = self._fin_preview_box.get_parent()
        if parent is None:
            self._fin_preview_box = None
            return
        parent.remove(self._fin_preview_box)

        # Recreate if needed
        if self._fin_form_parcelas > 1 and not self._fin_form_is_avista:
            self._fin_preview_box = self._criar_preview_parcelas(
                self._fin_form_parcelas, self._fin_form_valor_parcela, self._fin_form_first_due)
            content = parent
            children = []
            while (c := content.get_first_child()):
                children.append(c)
                content.remove(c)

            # Re-add all, inserting preview after payment method
            inserted = False
            for c in children:
                content.append(c)
                if not inserted and "finance_payment" in str(c):
                    content.append(self._fin_preview_box)
                    inserted = True
            if not inserted:
                content.append(self._fin_preview_box)
        else:
            self._fin_preview_box = None

    def _abrir_date_picker(self, key):
        """Open date picker dialog."""
        dialog = Gtk.Calendar()
        dialog.set_show_heading(True)
        dialog.set_show_day_names(True)
        
        win = Gtk.Window()
        win.set_title("Select Date")
        win.set_modal(True)
        win.set_transient_for(self.app.janela)
        win.set_default_size(320, 300)
        
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.append(dialog)
        
        btn_ok = Gtk.Button(label="OK")
        btn_ok.add_css_class("finance-btn-save")
        btn_ok.connect("clicked", lambda *_: self._confirm_date_picker(key, dialog, win))
        box.append(btn_ok)
        
        win.set_child(box)
        win.present()
        
        self._fin_date_picker = {"key": key, "dialog": dialog, "win": win}

    def _confirm_date_picker(self, key, cal, win):
        year = cal.get_year()
        month = cal.get_month() + 1
        day = cal.get_day_of_month()
        import datetime
        dt = datetime.datetime(year, month, day, 12, 0, 0)
        ms = int(dt.timestamp() * 1000)
        self._fin_form_widget_changed(key, ms)
        win.close()

    def _salvar_expense_form(self, dialog):
        """Save expense from form (valores em REAIS, igual ao relay)."""
        w = self._fin_form_data["widgets"]

        def _num(txt):
            try:
                return float(str(txt).strip().replace(",", "."))
            except (TypeError, ValueError):
                return 0.0

        # Collect values
        desc = w["finance_desc"].get_text() if w["finance_desc"] else ""
        total = _num(w["finance_total"].get_text() if w["finance_total"] else "0")
        cat = w["finance_cat"].get_active_text() if w["finance_cat"] else "other"
        purchase_ms = w["finance_purchase_date"].get_label() if w["finance_purchase_date"] else ""
        # Parse date from button label
        import datetime
        try:
            purchase_ms = int(datetime.datetime.strptime(purchase_ms, "%m/%d/%Y").timestamp() * 1000)
        except:
            purchase_ms = int(time.time() * 1000)
        
        parcelas = self._fin_form_parcelas
        valor_parcela = _num(self._fin_form_valor_parcela)
        first_due = self._fin_form_first_due
        payment = self._fin_form_payment

        if not desc.strip() or total <= 0:
            return

        # For immediate payments, force 1 installment, paid
        if self._fin_form_is_avista:
            parcelas = 1
            first_due = purchase_ms
            valor_parcela = total
        elif valor_parcela <= 0 and parcelas > 0:
            # Sem valor de parcela informado: divide o total.
            valor_parcela = round(total / parcelas, 2)

        # Build expense data (snake_case = formato canonico do relay/Drive)
        expense_data = {
            "title": desc.strip(),
            "total_amount": round(total, 2),
            "category": cat,
            "purchase_date": purchase_ms,
            "installments": parcelas,
            "installment_amount": round(valor_parcela, 2),
            "first_due_date": first_due,
            "payment_method": payment,
        }

        if self._fin_form_data["editando"]:
            expense_data["id"] = self._fin_form_data["expense_id"]
            expense_data["origem"] = self._fin_form_data["origem"]
            # Update via relay HTTP
            self._bg(lambda: self._atualizar_expense_http(expense_data))
        else:
            # Create via relay HTTP
            self._bg(lambda: self._criar_expense_http(expense_data))

        dialog.close()
        self._finance_sync_status = "Syncing..."
        self._render_finance_dashboard()

    def _criar_expense_http(self, expense_data):
        """Create expense via relay HTTP."""
        import requests
        try:
            token = hub_store.obter_token_sessao()
            r = requests.post(
                f"http://100.70.88.101:5000/finance",
                json=expense_data,
                headers={"Authorization": f"Bearer {token}"},
                timeout=10
            )
            if r.status_code == 200:
                self._finance_sync_status = "Created successfully"
                self.pedir_finance()
            else:
                self._finance_sync_status = f"Error: {r.text[:100]}"
        except Exception as e:
            self._finance_sync_status = f"Error: {e}"
        GLib.idle_add(lambda: (self._render_finance_dashboard(), False)[1])

    def _atualizar_expense_http(self, expense_data):
        """Update expense via relay HTTP."""
        import requests
        try:
            token = hub_store.obter_token_sessao()
            expense_id = expense_data.pop("id", "")
            r = requests.post(
                f"http://100.70.88.101:5000/finance/{expense_id}",
                json=expense_data,
                headers={"Authorization": f"Bearer {token}"},
                timeout=10
            )
            if r.status_code == 200:
                self._finance_sync_status = "Updated successfully"
                self.pedir_finance()
            else:
                self._finance_sync_status = f"Error: {r.text[:100]}"
        except Exception as e:
            self._finance_sync_status = f"Error: {e}"
        GLib.idle_add(lambda: (self._render_finance_dashboard(), False)[1])

    def _confirm_delete_expense(self, expense_id):
        """Confirm delete expense."""
        dialog = Gtk.AlertDialog()
        dialog.set_message("Delete this expense?")
        dialog.set_detail("This action cannot be undone.")
        dialog.set_buttons(["Cancel", "DELETE"])
        dialog.set_default_button(1)
        dialog.set_cancel_button(0)
        dialog.choose(self.app.janela, None, lambda d, r: self._do_delete_expense(expense_id) if r == 1 else None)

    def _confirm_delete_expense_form(self, expense_id, form_dialog):
        """Confirm delete from form."""
        dialog = Gtk.AlertDialog()
        dialog.set_message("Delete this expense?")
        dialog.set_detail("This action cannot be undone.")
        dialog.set_buttons(["Cancel", "DELETE"])
        dialog.set_default_button(1)
        dialog.set_cancel_button(0)
        def on_response(d, r):
            if r == 1:
                self._do_delete_expense(expense_id)
                form_dialog.close()
        dialog.choose(self.app.janela, None, on_response)

    def _do_delete_expense(self, expense_id):
        """Delete expense via relay HTTP."""
        import requests
        try:
            token = hub_store.obter_token_sessao()
            r = requests.delete(
                f"http://100.70.88.101:5000/finance/{expense_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10
            )
            if r.status_code == 200:
                self._finance_sync_status = "Deleted"
                self.pedir_finance()
            else:
                self._finance_sync_status = f"Error: {r.text[:100]}"
        except Exception as e:
            self._finance_sync_status = f"Error: {e}"
        GLib.idle_add(lambda: (self._render_finance_dashboard(), False)[1])

    def _fmt_brl(self, valor):
        """Formata um valor EM REAIS (o relay/Drive trafega reais, nao centavos)."""
        try:
            return "R$ " + f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except (TypeError, ValueError):
            return "R$ 0,00"


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
        due = t.get("due_date") or t.get("dueDate")

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

        if due:
            from datetime import datetime as dt
            try:
                due_str = dt.fromtimestamp(due / 1000).strftime("%d/%m")
                atrasada = due < int(time.time() * 1000)
                due_lbl = Gtk.Label(xalign=0)
                due_lbl.set_markup(
                    f'<span font_family="Material Symbols Rounded">\ue916</span> {due_str}'
                    f'{" · OVERDUE" if atrasada else ""}'
                )
                due_lbl.set_use_markup(True)
                due_lbl.add_css_class("pad-task-meta")
                if atrasada:
                    due_lbl.add_css_class("spook-err")
                else:
                    due_lbl.add_css_class("spook-warn")
                meta.append(due_lbl)
            except Exception:
                pass

        conteudo.append(meta)
        card.append(conteudo)

        # Botão para definir/editar vencimento (Icons.Filled.DateRange do app)
        cal_btn = Gtk.Button()
        cal_btn.add_css_class("spook-btn")
        cal_btn.set_tooltip_text("Set due date")
        cal_btn.set_child(self._icone_msi("\ue916", "fin-ico-cal"))
        cal_btn.connect("clicked", lambda *_: self._abrir_due_date_dialog(t))
        card.append(cal_btn)

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

    # ================================================================ THREADS
    def _bg(self, fn):
        """Roda fn numa thread daemon (a UI e' atualizada via GLib.idle_add)."""
        def _runner():
            try:
                fn()
            except Exception as erro:
                print(f"[PAD] erro em thread: {erro}", flush=True)

        threading.Thread(target=_runner, daemon=True).start()

    # ============================================== NOTAS | TAREFAS | FINANCE
    def _atualizar_seg(self, vista):
        # contagem e botoes de NOTAS somem nas vistas de tarefas/finance
        if self.lbl_count is not None:
            self.lbl_count.set_visible(vista == "notas")
        for attr in ("btn_add_nota", "btn_sync_notas"):
            botao = getattr(self, attr, None)
            if botao is not None:
                botao.set_visible(vista == "notas")
        for botao, nome in (
            (self.seg_notas, "notas"),
            (self.seg_tarefas, "tarefas"),
            (self.seg_finance, "finance"),
        ):
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
        elif vista == "finance":
            self.pedir_finance()

    # ================================================ TAREFAS: transporte
    def pedir_tarefas(self):
        if callable(self.task_listar_cb):
            self.task_listar_cb()
        else:
            self.aplicar_tarefas([])

    def aplicar_tarefas(self, tasks):
        self.tasks = [dict(t) for t in (tasks or []) if isinstance(t, dict) and t.get("id")]
        GLib.idle_add(lambda: (self._render_tarefas(), False)[1])

    # ============================================ TAREFAS: LISTA | CALENDARIO
    def _atualizar_task_seg(self, vista):
        for botao, nome in ((self.seg_task_lista, "lista"), (self.seg_task_cal, "calendario")):
            if botao is None:
                continue
            botao.remove_css_class("pad-seg-on")
            botao.remove_css_class("pad-seg-btn")
            botao.add_css_class("pad-seg-on" if nome == vista else "pad-seg-btn")

    def _mostrar_task_vista(self, vista):
        if self.task_stack is not None:
            self.task_stack.set_visible_child_name(vista)
        self._atualizar_task_seg(vista)
        if vista == "calendario":
            self._render_calendario()

    def _criar_calendario_tarefas(self):
        """Calendario mensal de vencimentos (domingo primeiro, igual ao app)."""
        caixa = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        # Titulo (mesma hierarquia do app: rosa, mono, caixa alta)
        titulo = Gtk.Label(label="DUE DATES", xalign=0)
        titulo.add_css_class("pad-fin-cal-mes")
        caixa.append(titulo)

        # Navegacao de mes
        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_prev = Gtk.Button(label="←")
        btn_prev.add_css_class("pad-fin-cal-nav")
        btn_prev.set_tooltip_text("Previous month")
        btn_prev.connect("clicked", lambda *_: self._cal_mudar_mes(-1))
        nav.append(btn_prev)

        self.cal_mes_ano = Gtk.Label(label="", xalign=0.5)
        self.cal_mes_ano.add_css_class("pad-fin-cal-mes")
        self.cal_mes_ano.set_hexpand(True)
        self.cal_mes_ano.set_cursor_from_name("pointer")
        self.cal_mes_ano.set_tooltip_text("Jump to current month")
        clique = Gtk.GestureClick()
        clique.connect("released", lambda *_: self._cal_ir_hoje())
        self.cal_mes_ano.add_controller(clique)
        nav.append(self.cal_mes_ano)

        btn_next = Gtk.Button(label="→")
        btn_next.add_css_class("pad-fin-cal-nav")
        btn_next.set_tooltip_text("Next month")
        btn_next.connect("clicked", lambda *_: self._cal_mudar_mes(1))
        nav.append(btn_next)
        caixa.append(nav)

        # Cabecalho dos dias (domingo primeiro)
        semana_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        for nome in ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"):
            lbl = Gtk.Label(label=nome, xalign=0.5)
            lbl.add_css_class("pad-fin-cal-header")
            lbl.set_hexpand(True)
            lbl.set_size_request(24, 20)
            semana_box.append(lbl)
        caixa.append(semana_box)

        # Grade + lista ficam no mesmo scroll (como o LazyColumn do app)
        conteudo = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        self.cal_grid = Gtk.Grid()
        self.cal_grid.set_row_spacing(2)
        self.cal_grid.set_column_spacing(2)
        self.cal_grid.set_column_homogeneous(True)
        self.cal_grid.set_margin_top(4)
        conteudo.append(self.cal_grid)

        self.cal_tasks_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        conteudo.append(self.cal_tasks_box)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(conteudo)
        caixa.append(scroll)

        self._cal_mes_atual = datetime.date.today().replace(day=1)
        return caixa

    def _cal_mudar_mes(self, delta):
        ano = self._cal_mes_atual.year
        mes = self._cal_mes_atual.month + delta
        while mes > 12:
            mes -= 12
            ano += 1
        while mes < 1:
            mes += 12
            ano -= 1
        self._cal_mes_atual = datetime.date(ano, mes, 1)
        self._render_calendario()

    def _cal_ir_hoje(self):
        self._cal_mes_atual = datetime.date.today().replace(day=1)
        self._render_calendario()

    def _render_calendario(self):
        """Desenha o mes atual + lista de tasks com vencimento."""
        import calendar as cal_mod

        if self.cal_grid is None or self.cal_tasks_box is None:
            return

        while (c := self.cal_grid.get_first_child()):
            self.cal_grid.remove(c)
        while (c := self.cal_tasks_box.get_first_child()):
            self.cal_tasks_box.remove(c)

        mes = self._cal_mes_atual
        meses = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
                 "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]
        self.cal_mes_ano.set_text(f"{meses[mes.month - 1]} {mes.year}")

        # 0 = domingo (igual ao app)
        primeiro_dom = (mes.weekday() + 1) % 7
        dias_no_mes = cal_mod.monthrange(mes.year, mes.month)[1]
        hoje = datetime.date.today()
        agora = int(time.time() * 1000)

        tasks_por_dia = {}
        for t in self.tasks:
            if t.get("feito"):
                continue
            due = t.get("due_date") or t.get("dueDate")
            if not due:
                continue
            try:
                dt = datetime.datetime.fromtimestamp(due / 1000).date()
                if dt.year == mes.year and dt.month == mes.month:
                    tasks_por_dia.setdefault(dt.day, []).append(t)
            except Exception:
                pass

        dia = 1
        for semana in range(6):
            for col in range(7):
                if semana == 0 and col < primeiro_dom:
                    vazio = Gtk.Box()
                    vazio.set_size_request(24, 34)
                    self.cal_grid.attach(vazio, col, semana, 1, 1)
                elif dia <= dias_no_mes:
                    celula = self._criar_celula_calendario(dia, mes, hoje, tasks_por_dia.get(dia, []))
                    self.cal_grid.attach(celula, col, semana, 1, 1)
                    dia += 1
                else:
                    vazio = Gtk.Box()
                    vazio.set_size_request(24, 34)
                    self.cal_grid.attach(vazio, col, semana, 1, 1)

        # Rodape: total + lista (so as nao feitas, ordenadas por data)
        com_venc = [
            t for t in self.tasks
            if not t.get("feito") and (t.get("due_date") or t.get("dueDate"))
        ]
        com_venc.sort(key=lambda t: t.get("due_date") or t.get("dueDate") or 0)

        rodape = Gtk.Label(label=f"TOTAL WITH DUE DATE: {len(com_venc)}", xalign=0)
        rodape.add_css_class("pad-task-meta")
        rodape.add_css_class("spook-warn")
        self.cal_tasks_box.append(rodape)

        if not com_venc:
            vazio = Gtk.Label(label="No tasks with a due date.", xalign=0)
            vazio.add_css_class("pad-task-vazio")
            vazio.set_vexpand(True)
            self.cal_tasks_box.append(vazio)
            return

        for t in com_venc:
            self.cal_tasks_box.append(self._card_tarefa(t))

    def _criar_celula_calendario(self, dia, mes, hoje, tasks_dia):
        eh_hoje = (dia == hoje.day and mes.month == hoje.month and mes.year == hoje.year)
        agora = int(time.time() * 1000)
        atrasadas = [
            t for t in tasks_dia
            if (t.get("due_date") or t.get("dueDate") or 0) < agora
        ]

        celula = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        celula.set_size_request(24, 34)
        celula.set_valign(Gtk.Align.START)
        celula.set_halign(Gtk.Align.FILL)
        celula.add_css_class("pad-fin-cal-cell")

        if eh_hoje:
            celula.add_css_class("pad-fin-cal-cell-today")
        elif tasks_dia:
            celula.add_css_class(
                "pad-fin-cal-cell-overdue" if atrasadas else "pad-fin-cal-cell-due"
            )

        lbl = Gtk.Label(label=str(dia), xalign=0.5)
        lbl.add_css_class("pad-fin-cal-day")
        if eh_hoje:
            lbl.add_css_class("pad-fin-cal-day-today")
        celula.append(lbl)

        if tasks_dia:
            pontos = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
            pontos.set_halign(Gtk.Align.CENTER)
            pontos.add_css_class("pad-fin-cal-dots-box")
            for _t in tasks_dia[:3]:
                ponto = Gtk.Box()
                ponto.add_css_class("pad-fin-cal-dot-unpaid")
                pontos.append(ponto)
            if len(tasks_dia) > 3:
                mais = Gtk.Label(label=f"+{len(tasks_dia) - 3}")
                mais.add_css_class("pad-fin-cal-plus")
                pontos.append(mais)
            celula.append(pontos)

        return celula

    # ========================================================= VENCIMENTO
    def _abrir_due_date_dialog(self, task):
        """Dialog para definir/limpar a data de vencimento de uma task."""
        win = Gtk.Window(title="SET DUE DATE", transient_for=self.app.janela, modal=True)
        win.set_default_size(320, 380)

        caixa = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        caixa.add_css_class("spook-root")
        caixa.set_margin_top(16)
        caixa.set_margin_bottom(16)
        caixa.set_margin_start(16)
        caixa.set_margin_end(16)

        titulo = Gtk.Label(
            label=(task.get("titulo") or task.get("title") or "Task")[:80], xalign=0
        )
        titulo.add_css_class("spook-title")
        titulo.set_wrap(True)
        caixa.append(titulo)

        self._due_dialog_task = task
        self._due_cal = Gtk.Calendar()
        self._due_cal.set_show_heading(True)
        self._due_cal.set_show_day_names(True)

        due = task.get("due_date") or task.get("dueDate")
        try:
            if due:
                dt = datetime.datetime.fromtimestamp(due / 1000)
                # GTK 4.24 rework: set_year/set_month (0-based)/set_day no lugar de
                # select_month/select_day (que agora recebe GLib.DateTime).
                if hasattr(self._due_cal, "set_year"):
                    self._due_cal.set_year(dt.year)
                    self._due_cal.set_month(dt.month - 1)
                    self._due_cal.set_day(dt.day)
                else:
                    self._due_cal.select_month(dt.month - 1, dt.year)
                    self._due_cal.select_day(dt.day)
        except Exception:
            pass
        caixa.append(self._due_cal)

        linha = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        linha.set_halign(Gtk.Align.END)

        btn_limpar = Gtk.Button(label="CLEAR")
        btn_limpar.add_css_class("spook-btn")
        btn_limpar.add_css_class("spook-err-btn")
        btn_limpar.connect("clicked", lambda *_: self._due_date_clear(win, task))
        linha.append(btn_limpar)

        btn_ok = Gtk.Button(label="SET")
        btn_ok.add_css_class("spook-btn")
        btn_ok.add_css_class("spook-accent")
        btn_ok.connect("clicked", lambda *_: self._due_date_confirm(win, task))
        linha.append(btn_ok)

        caixa.append(linha)
        win.set_child(caixa)
        win.present()

    def _due_date_clear(self, win, task):
        if callable(self.task_due_cb):
            self.task_due_cb(task.get("id"), 0)
        win.close()

    def _due_date_confirm(self, win, task):
        try:
            # GTK4: get_date() devolve GLib.DateTime (get_month() é 1-based).
            # Nas versões antigas do PyGObject devolvia a tupla (ano, mes0, dia).
            valor = self._due_cal.get_date()
            if isinstance(valor, tuple):
                ano, mes0, dia = valor
            else:
                ano = valor.get_year()
                mes0 = valor.get_month() - 1
                dia = valor.get_day_of_month()
            dt = datetime.datetime(ano, mes0 + 1, dia, 23, 59, 59)
            due_ms = int(dt.timestamp() * 1000)
            if callable(self.task_due_cb):
                self.task_due_cb(task.get("id"), due_ms)
        except Exception as erro:
            print(f"[PAD] due date: {erro}", flush=True)
        win.close()
