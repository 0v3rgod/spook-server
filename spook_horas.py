#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SPOOK // HORAS — registro de horas trabalhadas (Arch <-> Poco).

Mesmo desenho do PAD/TASKS: um dia = um JSON no relay
(`gdrive:SPOOK/horas/<YYYY-MM-DD>.json`, espelho em `data/horas/` na VM).
O cache local (`~/.cache/spook-horas/dias.json`) e' a fonte da verdade
offline; o relay resolve conflito por LWW no campo `em` (epoch ms) e apagar
vira lapide (`removido`).

Regras do relatorio (identicas ao app Android):
  * horas = saida - entrada - intervalos;
  * fator do dia: normal 1x, fim de semana 1 + fds%, feriado 1 + feriado%;
  * o que passar da jornada diaria e' extra e recebe + extra% SOBRE o valor
    da hora ja corrigido pelo tipo do dia;
  * o relatorio mostra dia a dia, totais por tipo e o valor final.
"""

import datetime
import json
import threading
import time
import zipfile
from pathlib import Path

import requests

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango

import relatorio_doc
from relatorio_doc import D, _aplica_fonte, _pacote_pandoc, _par, _run

# ============================== CONFIG ==============================

RELAY_URL = "http://100.70.88.101:5000"
TOKEN_FILE = Path.home() / ".pentestlink" / "token_sessao.txt"
CACHE_DIR = Path.home() / ".cache" / "spook-horas"
DIAS_FILE = CACHE_DIR / "dias.json"
EXPORT_DIR = Path.home() / "Downloads" / "SPOOK"

DEVICE_ID = "arch-desktop"

TIPO_NORMAL = "normal"
TIPO_FIM_SEMANA = "fim_semana"
TIPO_FERIADO = "feriado"
ROTULO_TIPO = {
    TIPO_NORMAL: "NORMAL",
    TIPO_FIM_SEMANA: "WEEKEND",
    TIPO_FERIADO: "HOLIDAY",
}

CSS_HORAS = """
.horas-titulo {
  font-size: 20px; font-weight: 700; color: #B44AFF; letter-spacing: -0.3px;
}
.horas-sub {
  font-family: "JetBrains Mono", "JetBrainsMono NF", monospace;
  font-size: 8px; font-weight: 700; color: #8A8F98;
}
.horas-card {
  background: #16141F; border-radius: 18px; border: 1px solid #3A3648;
  padding: 12px 16px;
}
.horas-card-vivo { border-color: rgba(156, 255, 87, 0.55); }
.horas-relogio {
  font-family: "JetBrains Mono", "JetBrainsMono NF", monospace;
  font-size: 30px; font-weight: 700; color: #F4F1FF;
}
.horas-estado {
  font-family: "JetBrains Mono", "JetBrainsMono NF", monospace;
  font-size: 9px; font-weight: 700;
}
.horas-estado-vivo { color: #9CFF57; }
.horas-estado-pausa { color: #FFD54F; }
.horas-estado-fim { color: #5CE1E6; }
.horas-estado-fora { color: #8A8F98; }
.horas-linha {
  font-family: "JetBrains Mono", "JetBrainsMono NF", monospace;
  font-size: 10px; color: #B8B4C8;
}
.horas-hora-valor {
  font-family: "JetBrains Mono", "JetBrainsMono NF", monospace;
  font-size: 11px; font-weight: 700; color: #F4F1FF;
}
.horas-mini {
  font-family: "JetBrains Mono", "JetBrainsMono NF", monospace;
  font-size: 8px; font-weight: 700; color: #8A8F98;
}
.horas-chip {
  font-family: "JetBrains Mono", "JetBrainsMono NF", monospace;
  font-size: 9px; font-weight: 700;
  border: 1px solid #3A3648; border-radius: 999px;
  padding: 2px 10px; color: #8A8F98; background: transparent;
}
button.horas-chip-on-normal { color: #9CFF57; border-color: #9CFF57; }
button.horas-chip-on-fds { color: #5CE1E6; border-color: #5CE1E6; }
button.horas-chip-on-feriado { color: #FF5C6C; border-color: #FF5C6C; }
.horas-dia {
  background: #101019; border-radius: 12px; border: 1px solid #2A2736;
  padding: 8px 12px;
}
.horas-dia-titulo {
  font-family: "JetBrains Mono", "JetBrainsMono NF", monospace;
  font-size: 11px; font-weight: 700; color: #F4F1FF;
}
.horas-dia-meta {
  font-family: "JetBrains Mono", "JetBrainsMono NF", monospace;
  font-size: 9px; color: #8A8F98;
}
.horas-tipo-normal { color: #9CFF57; }
.horas-tipo-fds { color: #5CE1E6; }
.horas-tipo-feriado { color: #FF5C6C; }
.horas-total {
  font-family: "JetBrains Mono", "JetBrainsMono NF", monospace;
  font-size: 12px; font-weight: 700; color: #9CFF57;
}
.horas-erro { color: #FF5C6C; }
"""


# ============================== UTILIDADES ==============================

def _agora() -> int:
    return int(time.time() * 1000)


def _int(v, padrao=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return padrao


def carregar_token() -> str:
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _hoje_id() -> str:
    return datetime.date.today().isoformat()


def _data_de(hid: str) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(str(hid)[:10])
    except Exception:
        return None


def _fmt_data(hid: str) -> str:
    d = _data_de(hid)
    return d.strftime("%d/%m/%Y") if d else str(hid)


def _fmt_data_curta(hid: str) -> str:
    d = _data_de(hid)
    return d.strftime("%d/%m") if d else str(hid)


def _nome_dia(hid: str) -> str:
    d = _data_de(hid)
    if not d:
        return ""
    return ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")[d.weekday()]


def _tipo_sugerido(hid: str) -> str:
    d = _data_de(hid)
    if d and d.weekday() >= 5:
        return TIPO_FIM_SEMANA
    return TIPO_NORMAL


def _fmt_hora(ms: int) -> str:
    if not ms or ms <= 0:
        return "--:--"
    return datetime.datetime.fromtimestamp(ms / 1000.0).strftime("%H:%M")


def _fmt_duracao(minutos: int) -> str:
    m = max(0, int(minutos))
    return "%dh%02d" % (m // 60, m % 60)


def _dinheiro(v: float) -> str:
    s = "%.2f" % float(v or 0.0)
    inteiro, decimal = s.split(".")
    partes = []
    while len(inteiro) > 3:
        partes.insert(0, inteiro[-3:])
        inteiro = inteiro[:-3]
    partes.insert(0, inteiro)
    return "R$ " + ".".join(partes) + "," + decimal


def _minutos_do_dia(dia: dict, agora: int | None = None) -> int:
    """Mesma conta do app: saida (ou agora) - entrada - intervalos."""
    entrada = _int(dia.get("entrada"))
    if entrada <= 0:
        return 0
    agora = agora or _agora()
    saida = _int(dia.get("saida"))
    fim = saida if saida > entrada else agora
    total = fim - entrada
    for it in dia.get("intervalos") or []:
        ini = _int(it.get("inicio"))
        f = _int(it.get("fim"))
        if ini <= 0:
            continue
        if f <= ini:
            f = agora
        if f > ini:
            total -= f - ini
    return max(0, total // 60000)


def _minutos_intervalos(dia: dict) -> int:
    total = 0
    for it in dia.get("intervalos") or []:
        ini = _int(it.get("inicio"))
        f = _int(it.get("fim"))
        if ini > 0 and f > ini:
            total += f - ini
    return total // 60000


def _normalizar(rec: dict) -> dict:
    """Deixa o registro no formato unico (aceita o que vem do relay/app)."""
    hid = str(rec.get("id") or rec.get("data") or "").strip()[:64]
    intervalos = []
    for it in (rec.get("intervalos") or [])[:12]:
        if not isinstance(it, dict):
            continue
        ini = _int(it.get("inicio"))
        if ini <= 0:
            continue
        intervalos.append({"inicio": ini, "fim": max(_int(it.get("fim")), 0)})
    local = rec.get("local") if isinstance(rec.get("local"), dict) else {}
    tipo = str(rec.get("tipo") or TIPO_NORMAL).strip().lower()
    if tipo not in ROTULO_TIPO:
        tipo = TIPO_NORMAL
    return {
        "id": hid,
        "data": str(rec.get("data") or hid)[:10],
        "entrada": _int(rec.get("entrada")),
        "saida": _int(rec.get("saida")),
        "intervalos": intervalos,
        "descricao": str(rec.get("descricao") or "")[:4000],
        "tipo": tipo,
        "local": {
            "lat": float(local.get("lat") or 0.0),
            "lon": float(local.get("lon") or 0.0),
            "precisao": float(local.get("precisao") or 0.0),
            "texto": str(local.get("texto") or "")[:200],
        },
        "em": _int(rec.get("em")) or _agora(),
        "device": str(rec.get("device") or DEVICE_ID)[:64],
        "removido": bool(rec.get("removido")),
        "removido_em": _int(rec.get("removido_em") or rec.get("removidoEm") or 0),
        "pendente": bool(rec.get("pendente")),
    }


def _com_hora(hid: str, hora: int, minuto: int) -> int:
    d = _data_de(hid) or datetime.date.today()
    dt = datetime.datetime.combine(
        d, datetime.time(max(0, min(23, hora)), max(0, min(59, minuto)))
    )
    return int(dt.timestamp() * 1000)


def _hora_de(ms: int) -> tuple:
    if not ms or ms <= 0:
        return (8, 0)
    dt = datetime.datetime.fromtimestamp(ms / 1000.0)
    return (dt.hour, dt.minute)


# ============================== RELATORIO ==============================

def calcular(dias, valor_hora=0.0, jornada_min=480, extra_pct=50.0,
             feriado_pct=100.0, fds_pct=50.0, contar_extras=True):
    """Devolve o resumo (mesma matematica do HorasRelatorio.kt)."""
    linhas = []
    total_min = total_normais = total_extras = 0
    min_normal = min_fds = min_feriado = 0
    valor_total = 0.0

    fechados = [d for d in dias if not d.get("removido")
                and _int(d.get("entrada")) > 0
                and _int(d.get("saida")) > _int(d.get("entrada"))]
    for dia in sorted(fechados, key=lambda x: str(x.get("data") or "")):
        minutos = _minutos_do_dia(dia)
        if minutos <= 0:
            continue
        tipo = dia.get("tipo") or TIPO_NORMAL
        fator = 1.0
        if tipo == TIPO_FIM_SEMANA:
            fator += fds_pct / 100.0
        elif tipo == TIPO_FERIADO:
            fator += feriado_pct / 100.0
        extras = max(0, minutos - jornada_min) if contar_extras else 0
        normais = minutos - extras
        v_norm = (normais / 60.0) * valor_hora * fator
        v_ext = (extras / 60.0) * valor_hora * fator * (1.0 + extra_pct / 100.0)
        linhas.append({
            "dia": dia, "minutos": minutos, "normais": normais,
            "extras": extras, "fator": fator, "valor": v_norm + v_ext,
        })
        total_min += minutos
        total_normais += normais
        total_extras += extras
        valor_total += v_norm + v_ext
        if tipo == TIPO_FIM_SEMANA:
            min_fds += minutos
        elif tipo == TIPO_FERIADO:
            min_feriado += minutos
        else:
            min_normal += minutos

    return {
        "linhas": linhas,
        "total_min": total_min,
        "total_normais": total_normais,
        "total_extras": total_extras,
        "min_normal": min_normal,
        "min_fds": min_fds,
        "min_feriado": min_feriado,
        "valor_total": valor_total,
        "valor_hora": valor_hora,
        "jornada_min": jornada_min,
        "extra_pct": extra_pct,
        "feriado_pct": feriado_pct,
        "fds_pct": fds_pct,
        "contar_extras": contar_extras,
        "inicio": linhas[0]["dia"].get("data") if linhas else "",
        "fim": linhas[-1]["dia"].get("data") if linhas else "",
    }


def _csv(resumo) -> str:
    linhas = [
        "data;dia_semana;entrada;saida;intervalos_min;horas;minutos_normais;"
        "minutos_extras;tipo;fator;valor;descricao;local"
    ]
    for l in resumo["linhas"]:
        d = l["dia"]
        local = (d.get("local") or {}).get("texto") or ""
        linhas.append(";".join([
            str(d.get("data") or ""),
            _nome_dia(d.get("data") or ""),
            _fmt_hora(_int(d.get("entrada"))),
            _fmt_hora(_int(d.get("saida"))),
            str(_minutos_intervalos(d)),
            _fmt_duracao(l["minutos"]),
            str(l["normais"]),
            str(l["extras"]),
            str(d.get("tipo") or TIPO_NORMAL),
            "%.2f" % l["fator"],
            "%.2f" % l["valor"],
            str(d.get("descricao") or "").replace("\n", " ").replace(";", ","),
            local.replace(";", ","),
        ]))
    linhas.append("")
    linhas.append("total_horas;%s" % _fmt_duracao(resumo["total_min"]))
    linhas.append("horas_normais;%s" % _fmt_duracao(resumo["total_normais"]))
    linhas.append("horas_extras;%s" % _fmt_duracao(resumo["total_extras"]))
    linhas.append("minutos_normal;%s" % _fmt_duracao(resumo["min_normal"]))
    linhas.append("minutos_fim_semana;%s" % _fmt_duracao(resumo["min_fds"]))
    linhas.append("minutos_feriado;%s" % _fmt_duracao(resumo["min_feriado"]))
    linhas.append("valor_hora;%.2f" % resumo["valor_hora"])
    linhas.append("jornada_min;%d" % resumo["jornada_min"])
    linhas.append("extra_pct;%.2f" % resumo["extra_pct"])
    linhas.append("feriado_pct;%.2f" % resumo["feriado_pct"])
    linhas.append("fds_pct;%.2f" % resumo["fds_pct"])
    linhas.append("valor_total;%.2f" % resumo["valor_total"])
    return "\n".join(linhas) + "\n"


def _cor_tipo(tipo: str) -> str:
    if tipo == TIPO_FIM_SEMANA:
        return D["fechado"]
    if tipo == TIPO_FERIADO:
        return D["erro"]
    return D["aberto"]


def _col(v, largura: int, esquerda=True) -> str:
    s = str(v)
    return s.ljust(largura)[:largura] if esquerda else s.rjust(largura)[:largura]


def _docx_horas(resumo, destino: str) -> str:
    """DOCX com a paleta do app (pacote base do pandoc, corpo proprio)."""
    inicio = _fmt_data(resumo["inicio"]) if resumo["inicio"] else "-"
    fim = _fmt_data(resumo["fim"]) if resumo["fim"] else "-"
    corpo = [
        _par(_run("SPOOK // HORAS TRABALHADAS", D["titulo"], negrito=True, meio=26)),
        _par(_run("periodo  %s a %s" % (inicio, fim), D["aberto"], meio=20)),
        _par(
            _run("%d dia(s)   total %s   extra %s" % (
                len(resumo["linhas"]),
                _fmt_duracao(resumo["total_min"]),
                _fmt_duracao(resumo["total_extras"]),
            ), D["apagado"], meio=18),
            _run("   spook://horas", D["apagado"], meio=16),
        ),
        _par(_run(
            "R$ %.2f/h   jornada %s   extra %.0f%%   fds %.0f%%   feriado %.0f%%" % (
                resumo["valor_hora"], _fmt_duracao(resumo["jornada_min"]),
                resumo["extra_pct"], resumo["fds_pct"], resumo["feriado_pct"],
            ), D["texto"], meio=18)),
        _par(_run(" " * 96, D["texto"], meio=8)),
    ]

    cabecalho = (
        _col("DATA", 10) + " " + _col("DIA", 3) + " " +
        _col("ENTRADA", 7) + " " + _col("SAIDA", 5) + " " +
        _col("INT", 5) + " " + _col("HORAS", 5) + " " +
        _col("NORM", 5) + " " + _col("EXTRAS", 6) + " " +
        _col("TIPO", 13) + " " + _col("FATOR", 5, False) + " " +
        _col("VALOR", 12, False)
    )
    corpo.append(_par(_run(cabecalho, D["aviso"], negrito=True, meio=16)))
    corpo.append(_par(_run("-" * 96, D["apagado"], meio=16)))

    for l in resumo["linhas"]:
        d = l["dia"]
        data = str(d.get("data") or "")
        linha = (
            _col(data, 10) + " " + _col(_nome_dia(data), 3) + " " +
            _col(_fmt_hora(_int(d.get("entrada"))), 7) + " " +
            _col(_fmt_hora(_int(d.get("saida"))), 5) + " " +
            _col(_fmt_duracao(_minutos_intervalos(d)), 5) + " " +
            _col(_fmt_duracao(l["minutos"]), 5) + " " +
            _col(l["normais"], 5) + " " + _col(l["extras"], 6) + " "
        )
        corpo.append(_par(
            _run(linha, D["texto"], meio=16),
            _run(_col(ROTULO_TIPO.get(d.get("tipo"), "NORMAL"), 13), _cor_tipo(d.get("tipo")), meio=16),
            _run(" " + _col("%.2f" % l["fator"], 5, False), D["texto"], meio=16),
            _run(" " + _col(_dinheiro(l["valor"]), 12, False), D["titulo"], negrito=True, meio=16),
        ))

    corpo.append(_par(_run("-" * 96, D["apagado"], meio=16)))
    corpo.append(_par(
        _run("TOTAL  ", D["texto"], negrito=True, meio=18),
        _run(_fmt_duracao(resumo["total_min"]), D["aberto"], negrito=True, meio=18),
        _run("   normais ", D["texto"], meio=18),
        _run(_fmt_duracao(resumo["total_normais"]), D["aberto"], meio=18),
        _run("   extras ", D["texto"], meio=18),
        _run(_fmt_duracao(resumo["total_extras"]), D["versao"], negrito=True, meio=18),
        _run("   VALOR ", D["texto"], meio=18),
        _run(_dinheiro(resumo["valor_total"]), D["titulo"], negrito=True, meio=20),
    ))
    corpo.append(_par(
        _run("normal %s   fim de semana %s   feriado %s" % (
            _fmt_duracao(resumo["min_normal"]),
            _fmt_duracao(resumo["min_fds"]),
            _fmt_duracao(resumo["min_feriado"]),
        ), D["apagado"], meio=17),
    ))

    com_desc = [l["dia"] for l in resumo["linhas"]
                if str(l["dia"].get("descricao") or "").strip()]
    if com_desc:
        corpo.append(_par(_run(" ", D["texto"], meio=10)))
        corpo.append(_par(_run("DESCRICAO POR DIA", D["aviso"], negrito=True, meio=20)))
        for d in com_desc:
            corpo.append(_par(
                _run("%s  " % _fmt_data_curta(str(d.get("data") or "")), D["apagado"], meio=17),
                _run(str(d.get("descricao") or "").replace("\n", " ")[:400], D["texto"], meio=17),
            ))

        com_local = [d for d in com_desc
                     if str((d.get("local") or {}).get("texto") or "").strip()]
        if com_local:
            corpo.append(_par(_run(" ", D["texto"], meio=10)))
            corpo.append(_par(_run("LOCAL", D["aviso"], negrito=True, meio=20)))
            for d in com_local:
                corpo.append(_par(
                    _run("%s  " % _fmt_data_curta(str(d.get("data") or "")), D["apagado"], meio=17),
                    _run(str((d.get("local") or {}).get("texto"))[:200], D["texto"], meio=17),
                ))

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>%s"
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"'
        ' w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>" % "".join(corpo)
    )
    base = _pacote_pandoc()
    if base and "word/styles.xml" in base:
        base["word/styles.xml"] = _aplica_fonte(base["word/styles.xml"])
        base["word/document.xml"] = document.encode("utf-8")
        with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
            for nome, dados in base.items():
                z.writestr(nome, dados)
        return destino

    # sem pandoc: pacote minimo (LibreOffice/Word abrem; o Drive pode recusar)
    estilos = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:docDefaults><w:rPrDefault><w:rPr>"
        '<w:rFonts w:ascii="%s" w:hAnsi="%s" w:cs="%s"/>'
        '<w:sz w:val="17"/><w:szCs w:val="17"/>'
        "</w:rPr></w:rPrDefault></w:docDefaults>"
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        '<w:name w:val="Normal"/><w:qFormat/></w:style></w:styles>'
        % (relatorio_doc.FONTE_DOCX, relatorio_doc.FONTE_DOCX, relatorio_doc.FONTE_DOCX)
    )
    ct = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2003/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2003/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    rels_doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2003/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        "</Relationships>"
    )
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/_rels/document.xml.rels", rels_doc)
        z.writestr("word/styles.xml", estilos)
        z.writestr("word/document.xml", document)
    return destino


# ============================== UI ==============================

class SpookHorasManager:
    def __init__(self, app):
        self.app = app
        self.token = carregar_token()
        self.dias = {}
        self._lock = threading.RLock()
        self._sincronizando = False
        self._erro = ""
        self._css_instalado = False
        self._desc_timer = None
        self._desc_sujo = False

        # widgets
        self.lbl_data = None
        self.lbl_relogio = None
        self.lbl_estado = None
        self.lbl_status = None
        self.box_acoes = None
        self.box_tempos = None
        self.box_hist = None
        self.lbl_hist = None
        self.txt_desc = None
        self.ent_local = None
        self.seg_normal = None
        self.seg_fds = None
        self.seg_feriado = None

        self._carregar()

    # ------------------------------------------------------------ cache local
    def _carregar(self):
        try:
            if DIAS_FILE.exists():
                dados = json.loads(DIAS_FILE.read_text(encoding="utf-8"))
                for d in dados.get("dias", []):
                    if d.get("id"):
                        self.dias[d["id"]] = _normalizar(d)
        except Exception:
            self.dias = {}

    def _gravar(self):
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            lista = sorted(self.dias.values(), key=lambda d: str(d.get("data") or ""),
                           reverse=True)
            tmp = DIAS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps({"dias": lista}, ensure_ascii=False),
                           encoding="utf-8")
            tmp.replace(DIAS_FILE)
        except Exception:
            pass

    # ---------------------------------------------------------------- modelo
    def _dia_hoje(self) -> dict:
        hid = _hoje_id()
        with self._lock:
            dia = self.dias.get(hid)
        if dia is None or dia.get("removido"):
            dia = {
                "id": hid, "data": hid, "entrada": 0, "saida": 0,
                "intervalos": [], "descricao": "", "tipo": _tipo_sugerido(hid),
                "local": {"lat": 0.0, "lon": 0.0, "precisao": 0.0, "texto": ""},
                "em": _agora(), "device": DEVICE_ID,
                "removido": False, "removido_em": 0, "pendente": False,
            }
        return dict(dia)

    def _salvar_local(self, dia: dict) -> dict:
        novo = _normalizar(dia)
        novo["em"] = _agora()
        novo["device"] = DEVICE_ID
        novo["pendente"] = True
        with self._lock:
            self.dias[novo["id"]] = novo
            self._gravar()
        return novo

    def listar(self) -> list:
        with self._lock:
            return sorted(
                [dict(d) for d in self.dias.values() if not d.get("removido")],
                key=lambda d: str(d.get("data") or ""), reverse=True,
            )

    # ------------------------------------------------------------------ rede
    def _http(self, metodo, rota, corpo=None, timeout=10):
        url = RELAY_URL.rstrip("/") + rota
        headers = {"Authorization": "Bearer %s" % self.token}
        try:
            if metodo == "GET":
                r = requests.get(url, headers=headers, timeout=timeout)
            elif metodo == "POST":
                r = requests.post(url, headers=headers, json=corpo or {},
                                  timeout=timeout)
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
        try:
            self.token = carregar_token() or self.token
            res = self._http("GET", "/horas")
            if not res or not res.get("ok"):
                self._erro = "relay offline"
                GLib.idle_add(self._pos_sync)
                return
            self._erro = ""
            for remoto in res.get("dias") or []:
                self._aplicar_remoto(remoto)
            with self._lock:
                pendentes = [d["id"] for d in self.dias.values() if d.get("pendente")]
            for hid in pendentes:
                with self._lock:
                    dia = dict(self.dias.get(hid) or {})
                if not dia:
                    continue
                if dia.get("removido"):
                    self._apagar_remoto(hid)
                else:
                    self._empurrar(hid)
        except Exception as e:
            self._erro = str(e)[:80]
        finally:
            with self._lock:
                self._gravar()
            self._sincronizando = False
            GLib.idle_add(self._pos_sync)

    def _aplicar_remoto(self, remoto: dict):
        if not isinstance(remoto, dict):
            return
        novo = _normalizar(remoto)
        if not novo["id"]:
            return
        with self._lock:
            local = self.dias.get(novo["id"])
            if local is None or novo["em"] > _int(local.get("em")):
                novo["pendente"] = False
                self.dias[novo["id"]] = novo

    def _empurrar(self, hid: str):
        with self._lock:
            dia = dict(self.dias.get(hid) or {})
        if not dia:
            return
        corpo = {k: v for k, v in dia.items() if k not in ("pendente",)}
        res = self._http("POST", "/horas/%s" % hid, corpo, timeout=20)
        if not res or not res.get("ok"):
            return
        gravado = res.get("dia") or corpo
        novo = _normalizar(gravado)
        novo["pendente"] = False
        with self._lock:
            local = self.dias.get(hid)
            if local is None or novo["em"] >= _int(local.get("em")):
                self.dias[hid] = novo
            self._gravar()
        GLib.idle_add(self._pos_sync)

    def _apagar_remoto(self, hid: str):
        res = self._http("DELETE", "/horas/%s?device=%s" % (hid, DEVICE_ID))
        if not res or not res.get("ok"):
            return
        gravado = res.get("dia") or {}
        novo = _normalizar(gravado) if gravado.get("id") else None
        with self._lock:
            if novo:
                novo["pendente"] = False
                self.dias[hid] = novo
            elif hid in self.dias:
                self.dias[hid]["pendente"] = False
            self._gravar()
        GLib.idle_add(self._pos_sync)

    # ------------------------------------------------------------------- UI
    def _css(self):
        if self._css_instalado:
            return
        try:
            display = self.app.janela.get_display() if getattr(self.app, "janela", None) else None
            if display is None:
                display = Gtk.Window().get_display()
            provider = Gtk.CssProvider()
            provider.load_from_data(CSS_HORAS.encode())
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 10
            )
            self._css_instalado = True
        except Exception:
            pass

    def criar_pagina(self):
        self._css()
        caixa = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        caixa.add_css_class("spook-root")

        topo = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        coluna = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        coluna.set_hexpand(True)
        titulo = Gtk.Label(label="HOURS", xalign=0)
        titulo.add_css_class("horas-titulo")
        coluna.append(titulo)
        self.lbl_status = Gtk.Label(label="…", xalign=0)
        self.lbl_status.add_css_class("horas-sub")
        coluna.append(self.lbl_status)
        topo.append(coluna)

        # SYNC button with icon (same style as Android)
        btn_sync = Gtk.Button()
        btn_sync.set_child(Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6))
        ic_sync = Gtk.Label(label="\uf61b")  # sync_alt
        ic_sync.add_css_class("fin-ico")
        ic_sync.add_css_class("spook-msi")
        btn_sync.get_child().append(ic_sync)
        lbl_sync = Gtk.Label(label="SYNC")
        lbl_sync.add_css_class("spook-btn-text-sm")
        btn_sync.get_child().append(lbl_sync)
        btn_sync.add_css_class("spook-btn")
        btn_sync.set_valign(Gtk.Align.CENTER)
        btn_sync.connect("clicked", lambda *_: (self.sincronizar(True),
                                                self._pintar_status("syncing…"))[1])
        topo.append(btn_sync)
        caixa.append(topo)

        # ---------------------------------------------------------- TODAY
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("horas-card")
        self.card_hoje = card

        linha = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.lbl_data = Gtk.Label(label="", xalign=0)
        self.lbl_data.add_css_class("horas-linha")
        self.lbl_data.set_hexpand(True)
        linha.append(self.lbl_data)
        self.lbl_estado = Gtk.Label(label="OUT", xalign=1)
        self.lbl_estado.add_css_class("horas-estado")
        self.lbl_estado.add_css_class("horas-estado-fora")
        linha.append(self.lbl_estado)
        card.append(linha)

        self.lbl_relogio = Gtk.Label(label="0h00", xalign=0)
        self.lbl_relogio.add_css_class("horas-relogio")
        card.append(self.lbl_relogio)

        self.box_acoes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        card.append(self.box_acoes)

        self.box_tempos = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        card.append(self.box_tempos)

        # day type
        tipo_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl_tipo = Gtk.Label(label="TYPE", xalign=0)
        lbl_tipo.add_css_class("horas-mini")
        lbl_tipo.set_size_request(58, -1)
        tipo_box.append(lbl_tipo)

        def _chip(texto, classe_on):
            btn = Gtk.Button(label=texto)
            btn.add_css_class("horas-chip")
            btn.connect("clicked", lambda *_: self._definir_tipo(
                {"NORMAL": TIPO_NORMAL, "WEEKEND": TIPO_FIM_SEMANA,
                 "HOLIDAY": TIPO_FERIADO}[texto], classe_on))
            return btn

        self.seg_normal = _chip("NORMAL", "horas-chip-on-normal")
        self.seg_fds = _chip("WEEKEND", "horas-chip-on-fds")
        self.seg_feriado = _chip("HOLIDAY", "horas-chip-on-feriado")
        tipo_box.append(self.seg_normal)
        tipo_box.append(self.seg_fds)
        tipo_box.append(self.seg_feriado)
        card.append(tipo_box)

        # description (autosave)
        lbl_desc = Gtk.Label(label="DAY NOTE", xalign=0)
        lbl_desc.add_css_class("horas-mini")
        card.append(lbl_desc)
        self.txt_desc = Gtk.TextView()
        self.txt_desc.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.txt_desc.set_size_request(-1, 64)
        self.txt_desc.get_buffer().connect("changed", self._desc_alterada)
        scroll_desc = Gtk.ScrolledWindow()
        scroll_desc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll_desc.set_child(self.txt_desc)
        scroll_desc.add_css_class("spook-input")
        card.append(scroll_desc)

        # location (free text; GPS comes from phone)
        local_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl_local = Gtk.Label(label="LOCATION", xalign=0)
        lbl_local.add_css_class("horas-mini")
        lbl_local.set_size_request(58, -1)
        local_box.append(lbl_local)
        ic_loc = Gtk.Label(label="\ue0c8")  # pin / place
        ic_loc.add_css_class("fin-ico")
        ic_loc.add_css_class("spook-msi")
        local_box.append(ic_loc)
        self.ent_local = Gtk.Entry()
        self.ent_local.add_css_class("spook-input")
        self.ent_local.set_hexpand(True)
        self.ent_local.set_placeholder_text("location (optional)")
        self.ent_local.connect("activate", lambda *_: self._salvar_local_texto())
        local_box.append(self.ent_local)
        btn_local = Gtk.Button(label="SAVE")
        btn_local.add_css_class("spook-btn")
        btn_local.add_css_class("spook-btn-text-sm")
        btn_local.connect("clicked", lambda *_: self._salvar_local_texto())
        local_box.append(btn_local)
        card.append(local_box)

        caixa.append(card)

        # ---------------------------------------------------- HISTORY
        topo_hist = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.lbl_hist = Gtk.Label(label="HISTORY", xalign=0)
        self.lbl_hist.add_css_class("horas-sub")
        self.lbl_hist.set_hexpand(True)
        topo_hist.append(self.lbl_hist)

        # REPORT button with icon
        btn_exp = Gtk.Button()
        btn_exp.set_child(Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6))
        ic_report = Gtk.Label(label="\ue873")  # report
        ic_report.add_css_class("fin-ico")
        ic_report.add_css_class("spook-msi")
        btn_exp.get_child().append(ic_report)
        lbl_report = Gtk.Label(label="REPORT")
        lbl_report.add_css_class("spook-btn-text-sm")
        btn_exp.get_child().append(lbl_report)
        btn_exp.add_css_class("spook-btn")
        btn_exp.connect("clicked", lambda *_: self._abrir_exportar())
        topo_hist.append(btn_exp)
        caixa.append(topo_hist)

        self.box_hist = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        scroll_hist = Gtk.ScrolledWindow()
        scroll_hist.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll_hist.set_vexpand(True)
        scroll_hist.set_child(self.box_hist)
        caixa.append(scroll_hist)

        self._render_tudo()
        GLib.timeout_add_seconds(1, self._tick)
        GLib.timeout_add_seconds(20, self._timer_poll)
        GLib.timeout_add(600, lambda: (self.sincronizar(), False)[1])
        return caixa

    # ------------------------------------------------------------- pintura
    def _pintar_status(self, texto, erro=False):
        if self.lbl_status is None:
            return
        self.lbl_status.set_text(texto)
        if erro:
            self.lbl_status.add_css_class("horas-erro")
        else:
            self.lbl_status.remove_css_class("horas-erro")

    def _pos_sync(self):
        self._render_tudo()
        if self._erro:
            self._pintar_status(self._erro, erro=True)
        else:
            pend = sum(1 for d in self.dias.values() if d.get("pendente"))
            self._pintar_status("sincronizado" if not pend else "%d pendente(s)" % pend)
        return False

    def _tick(self):
        dia = self._dia_hoje()
        minutos = _minutos_do_dia(dia)
        if self.lbl_relogio is not None:
            self.lbl_relogio.set_text(_fmt_duracao(minutos))
        return True

    def _timer_poll(self):
        if self.txt_desc is not None and self.txt_desc.has_focus():
            return True
        self.sincronizar()
        return True

    def _render_tudo(self):
        self._render_hoje()
        self._render_hist()

    def _render_hoje(self):
        if self.lbl_data is None:
            return
        dia = self._dia_hoje()
        hid = dia["id"]
        entrada = _int(dia.get("entrada"))
        saida = _int(dia.get("saida"))
        abertos = [i for i in dia.get("intervalos") or []
                   if _int(i.get("inicio")) > 0 and _int(i.get("fim")) <= 0]
        trabalhando = entrada > 0 and saida <= 0
        encerrado = saida > 0
        pausa = bool(abertos)

        self.lbl_data.set_text("%s · %s" % (_fmt_data(hid), _nome_dia(hid)))
        if pausa:
            estado, classe = "ON BREAK", "horas-estado-pausa"
        elif trabalhando:
            estado, classe = "WORKING", "horas-estado-vivo"
        elif encerrado:
            estado, classe = "CLOSED", "horas-estado-fim"
        else:
            estado, classe = "OUT", "horas-estado-fora"
        for c in ("horas-estado-vivo", "horas-estado-pausa",
                  "horas-estado-fim", "horas-estado-fora"):
            self.lbl_estado.remove_css_class(c)
        self.lbl_estado.add_css_class(classe)
        self.lbl_estado.set_text(estado)
        self.lbl_relogio.set_text(_fmt_duracao(_minutos_do_dia(dia)))
        if trabalhando:
            self.card_hoje.add_css_class("horas-card-vivo")
        else:
            self.card_hoje.remove_css_class("horas-card-vivo")

        # action buttons with icons (matching Android app style)
        while (c := self.box_acoes.get_first_child()):
            self.box_acoes.remove(c)

        def _botao_icon(texto, icone, classe_extra, acao):
            """Create a button with Material Symbol icon + text."""
            btn = Gtk.Button()
            btn.set_child(Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6))
            ic = Gtk.Label(label=icone)
            ic.add_css_class("fin-ico")
            ic.add_css_class("spook-msi")
            btn.get_child().append(ic)
            lbl = Gtk.Label(label=texto)
            lbl.add_css_class("spook-btn-text-sm")
            btn.get_child().append(lbl)
            btn.add_css_class("spook-btn")
            if classe_extra:
                btn.add_css_class(classe_extra)
            btn.set_hexpand(True)
            btn.connect("clicked", lambda *_: acao())
            self.box_acoes.append(btn)
            return btn

        if not trabalhando and not encerrado:
            # CLOCK IN - login icon
            _botao_icon("CLOCK IN", "\ue231", "spook-accent", self._acao_entrada)
        elif trabalhando:
            if pausa:
                # BACK - restart_alt not found, use arrow_back_2 equivalent or play
                _botao_icon("BACK", "\uf080", None, self._acao_voltei)  # play_arrow.fill as "back to work"
            else:
                # BREAK - pause
                _botao_icon("BREAK", "\ue82e", None, self._acao_intervalo)
            # CLOCK OUT - logout
            _botao_icon("CLOCK OUT", "\ue230", None, self._acao_saida)
        else:
            # REOPEN - restart_alt not found, use autorenew
            _botao_icon("REOPEN", "\ue87a", None, self._acao_reabrir)
            # CLOSE DAY - check
            _botao_icon("CLOSE DAY", "\ue255", "spook-accent", self._abrir_exportar)

        # time rows
        while (c := self.box_tempos.get_first_child()):
            self.box_tempos.remove(c)

        self._linha_hora("CLOCK IN", entrada, lambda: self._editar_hora(
            "Clock In", entrada, self._definir_entrada))
        self._linha_hora("CLOCK OUT", saida, lambda: self._editar_hora(
            "Clock Out", saida, self._definir_saida))
        for i, it in enumerate(dia.get("intervalos") or []):
            inicio = _int(it.get("inicio"))
            fim = _int(it.get("fim"))
            self._linha_intervalo(i, inicio, fim)

        # type
        tipo = dia.get("tipo") or TIPO_NORMAL
        for botao, nome, classe in (
            (self.seg_normal, TIPO_NORMAL, "horas-chip-on-normal"),
            (self.seg_fds, TIPO_FIM_SEMANA, "horas-chip-on-fds"),
            (self.seg_feriado, TIPO_FERIADO, "horas-chip-on-feriado"),
        ):
            botao.remove_css_class("horas-chip-on-normal")
            botao.remove_css_class("horas-chip-on-fds")
            botao.remove_css_class("horas-chip-on-feriado")
            if nome == tipo:
                botao.add_css_class(classe)

        # description and local (don't overwrite while user is typing)
        if not self.txt_desc.has_focus() and not self._desc_sujo:
            buffer = self.txt_desc.get_buffer()
            if buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True) != (
                    dia.get("descricao") or ""):
                buffer.set_text(dia.get("descricao") or "")
                self._desc_sujo = False
        if not self.ent_local.has_focus():
            texto_local = (dia.get("local") or {}).get("texto") or ""
            if self.ent_local.get_text() != texto_local:
                self.ent_local.set_text(texto_local)

    def _linha_hora(self, rotulo, valor, ao_editar):
        linha = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lbl = Gtk.Label(label=rotulo, xalign=0)
        lbl.add_css_class("horas-linha")
        lbl.set_size_request(120, -1)
        linha.append(lbl)
        valor_lbl = Gtk.Label(label=_fmt_hora(valor), xalign=0)
        valor_lbl.add_css_class("horas-hora-valor")
        valor_lbl.set_hexpand(True)
        linha.append(valor_lbl)
        # EDIT button with icon
        btn = Gtk.Button()
        btn.set_child(Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4))
        ic_edit = Gtk.Label(label="\uec0f")  # edit.fill
        ic_edit.add_css_class("fin-ico")
        ic_edit.add_css_class("spook-msi")
        btn.get_child().append(ic_edit)
        lbl_edit = Gtk.Label(label="EDIT")
        lbl_edit.add_css_class("spook-btn-text-sm")
        btn.get_child().append(lbl_edit)
        btn.add_css_class("spook-btn")
        btn.add_css_class("spook-btn-text-sm")
        btn.connect("clicked", lambda *_: ao_editar())
        linha.append(btn)
        self.box_tempos.append(linha)
        return linha

    def _linha_intervalo(self, indice, inicio, fim):
        linha = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lbl = Gtk.Label(label="BREAK %d" % (indice + 1), xalign=0)
        lbl.add_css_class("horas-linha")
        lbl.set_size_request(120, -1)
        linha.append(lbl)
        texto = "%s → %s" % (_fmt_hora(inicio), _fmt_hora(fim))
        valor_lbl = Gtk.Label(label=texto, xalign=0)
        valor_lbl.add_css_class("horas-hora-valor")
        valor_lbl.set_hexpand(True)
        linha.append(valor_lbl)

        # START button with icon
        btn_ini = Gtk.Button()
        btn_ini.set_child(Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4))
        ic_start = Gtk.Label(label="\uf080")  # play_arrow.fill
        ic_start.add_css_class("fin-ico")
        ic_start.add_css_class("spook-msi")
        btn_ini.get_child().append(ic_start)
        lbl_start = Gtk.Label(label="START")
        lbl_start.add_css_class("spook-btn-text-sm")
        btn_ini.get_child().append(lbl_start)
        btn_ini.add_css_class("spook-btn")
        btn_ini.add_css_class("spook-btn-text-sm")
        btn_ini.connect("clicked", lambda *_: self._editar_hora(
            "Break %d — start" % (indice + 1), inicio,
            lambda valor: self._definir_intervalo(indice, inicio=valor)))
        linha.append(btn_ini)

        # END button with icon
        btn_fim = Gtk.Button()
        btn_fim.set_child(Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4))
        ic_end = Gtk.Label(label="\ue82e")  # pause.fill
        ic_end.add_css_class("fin-ico")
        ic_end.add_css_class("spook-msi")
        btn_fim.get_child().append(ic_end)
        lbl_end = Gtk.Label(label="END")
        lbl_end.add_css_class("spook-btn-text-sm")
        btn_fim.get_child().append(lbl_end)
        btn_fim.add_css_class("spook-btn")
        btn_fim.add_css_class("spook-btn-text-sm")
        btn_fim.connect("clicked", lambda *_: self._editar_hora(
            "Break %d — end" % (indice + 1), fim,
            lambda valor: self._definir_intervalo(indice, fim=valor)))
        linha.append(btn_fim)

        # DELETE button with icon (close)
        btn_del = Gtk.Button()
        btn_del.set_child(Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4))
        ic_del = Gtk.Label(label="\ue317")  # close
        ic_del.add_css_class("fin-ico")
        ic_del.add_css_class("spook-msi")
        ic_del.add_css_class("spook-msi-del")
        btn_del.get_child().append(ic_del)
        btn_del.add_css_class("spook-btn")
        btn_del.add_css_class("spook-btn-text-sm")
        btn_del.connect("clicked", lambda *_: self._remover_intervalo(indice))
        linha.append(btn_del)
        self.box_tempos.append(linha)

    def _render_hist(self):
        if self.box_hist is None:
            return
        while (c := self.box_hist.get_first_child()):
            self.box_hist.remove(c)

        hoje = _hoje_id()
        outros = [d for d in self.listar() if str(d.get("id")) != hoje]
        if self.lbl_hist is not None:
            self.lbl_hist.set_text("HISTORY · %d DAY(S)" % len(outros))
        if not outros:
            vazio = Gtk.Label(label="no days recorded yet", xalign=0)
            vazio.add_css_class("horas-dia-meta")
            self.box_hist.append(vazio)
            return

        for dia in outros[:60]:
            card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            card.add_css_class("horas-dia")

            coluna = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            coluna.set_hexpand(True)
            data = str(dia.get("data") or dia.get("id") or "")
            titulo = Gtk.Label(
                label="%s %s   %s   %s" % (
                    _fmt_data_curta(data), _nome_dia(data),
                    _fmt_duracao(_minutos_do_dia(dia)),
                    ROTULO_TIPO.get(dia.get("tipo"), "NORMAL"),
                ), xalign=0)
            titulo.add_css_class("horas-dia-titulo")
            titulo.set_ellipsize(Pango.EllipsizeMode.END)
            coluna.append(titulo)

            meta = "%s → %s" % (_fmt_hora(_int(dia.get("entrada"))),
                                _fmt_hora(_int(dia.get("saida"))))
            intervalos = _minutos_intervalos(dia)
            if intervalos > 0:
                meta += "   break %s" % _fmt_duracao(intervalos)
            desc = str(dia.get("descricao") or "").replace("\n", " ").strip()
            if desc:
                meta += "   %s" % desc[:90]
            lbl_meta = Gtk.Label(label=meta, xalign=0)
            lbl_meta.add_css_class("horas-dia-meta")
            lbl_meta.set_ellipsize(Pango.EllipsizeMode.END)
            coluna.append(lbl_meta)
            card.append(coluna)

            # DELETE button with icon
            btn_del = Gtk.Button()
            btn_del.set_child(Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4))
            ic_del = Gtk.Label(label="\ue317")  # close
            ic_del.add_css_class("fin-ico")
            ic_del.add_css_class("spook-msi")
            ic_del.add_css_class("spook-msi-del")
            btn_del.get_child().append(ic_del)
            btn_del.add_css_class("spook-btn")
            btn_del.add_css_class("spook-btn-text-sm")
            btn_del.connect("clicked", lambda *_, hid=str(dia.get("id")): self._apagar(hid))
            card.append(btn_del)
            self.box_hist.append(card)

    # --------------------------------------------------------------- acoes
    def _mutar(self, fn):
        dia = self._dia_hoje()
        novo = fn(dia)
        if novo is None:
            return
        salvo = self._salvar_local(novo)
        self._render_tudo()
        self._empurrar_thread(salvo["id"])

    def _empurrar_thread(self, hid):
        threading.Thread(target=self._empurrar, args=(hid,), daemon=True).start()

    def _acao_entrada(self):
        agora = _agora()
        descricao = self._texto_desc()
        self._mutar(lambda d: dict(
            d, entrada=agora, saida=0, intervalos=[],
            descricao=descricao, tipo=d.get("tipo") or _tipo_sugerido(d["id"]),
        ))

    def _acao_intervalo(self):
        agora = _agora()
        self._mutar(lambda d: dict(
            d, intervalos=list(d.get("intervalos") or []) +
            [{"inicio": agora, "fim": 0}],
        ))

    def _acao_voltei(self):
        agora = _agora()
        self._mutar(lambda d: dict(d, intervalos=[
            dict(i, fim=agora) if (_int(i.get("inicio")) > 0 and _int(i.get("fim")) <= 0)
            else i for i in (d.get("intervalos") or [])
        ]))

    def _acao_saida(self):
        agora = _agora()
        self._mutar(lambda d: dict(
            d, saida=agora, intervalos=[
                dict(i, fim=agora) if (_int(i.get("inicio")) > 0 and _int(i.get("fim")) <= 0)
                else i for i in (d.get("intervalos") or [])
            ],
        ))

    def _acao_reabrir(self):
        self._mutar(lambda d: dict(d, saida=0))

    def _definir_entrada(self, valor):
        self._mutar(lambda d: dict(d, entrada=valor, saida=0, intervalos=[]))

    def _definir_saida(self, valor):
        self._mutar(lambda d: dict(d, saida=valor))

    def _definir_intervalo(self, indice, inicio=None, fim=None):
        def _fn(d):
            intervalos = [dict(i) for i in (d.get("intervalos") or [])]
            if indice < 0 or indice >= len(intervalos):
                return d
            if inicio is not None:
                intervalos[indice]["inicio"] = inicio
            if fim is not None:
                intervalos[indice]["fim"] = fim
            return dict(d, intervalos=intervalos)
        self._mutar(_fn)

    def _remover_intervalo(self, indice):
        def _fn(d):
            intervalos = [dict(i) for i in (d.get("intervalos") or [])]
            if 0 <= indice < len(intervalos):
                intervalos.pop(indice)
            return dict(d, intervalos=intervalos)
        self._mutar(_fn)

    def _definir_tipo(self, tipo, classe_on):
        self._mutar(lambda d: dict(d, tipo=tipo))

    def _texto_desc(self) -> str:
        if self.txt_desc is None:
            return ""
        buffer = self.txt_desc.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

    def _desc_alterada(self, _buffer):
        self._desc_sujo = True
        if self._desc_timer is not None:
            GLib.source_remove(self._desc_timer)
        self._desc_timer = GLib.timeout_add(900, self._salvar_descricao)

    def _salvar_descricao(self):
        self._desc_timer = None
        texto = self._texto_desc()
        dia = self._dia_hoje()
        if texto == (dia.get("descricao") or ""):
            self._desc_sujo = False
            return False
        novo = self._salvar_local(dict(dia, descricao=texto))
        self._desc_sujo = False
        self._empurrar_thread(novo["id"])
        return False

    def _salvar_local_texto(self):
        texto = self.ent_local.get_text().strip() if self.ent_local is not None else ""
        dia = self._dia_hoje()
        local = dict(dia.get("local") or {})
        local["texto"] = texto
        self._mutar(lambda d: dict(d, local=local))

    def _apagar(self, hid):
        def _fn(d):
            return dict(d, removido=True, removido_em=_agora(), em=_agora())
        if hid == _hoje_id():
            self._mutar(_fn)
        else:
            with self._lock:
                dia = self.dias.get(hid)
            if not dia:
                return
            novo = _normalizar(dict(dia, removido=True, removido_em=_agora(),
                                    em=_agora(), device=DEVICE_ID))
            novo["pendente"] = True
            with self._lock:
                self.dias[hid] = novo
                self._gravar()
            self._render_tudo()
            self._empurrar_thread(hid)

    # ------------------------------------------------------------- dialogos
    def _editar_hora(self, titulo, valor, ao_escolher):
        janela = self.app.janela if getattr(self.app, "janela", None) else None
        dialogo = Gtk.Dialog(title=titulo, transient_for=janela, modal=True)
        dialogo.add_button("CANCELAR", Gtk.ResponseType.CANCEL)
        dialogo.add_button("OK", Gtk.ResponseType.OK)
        dialogo.set_default_response(Gtk.ResponseType.OK)

        area = dialogo.get_content_area()
        area.set_spacing(8)
        area.set_margin_top(12)
        area.set_margin_bottom(12)
        area.set_margin_start(12)
        area.set_margin_end(12)

        hora, minuto = _hora_de(valor)
        linha = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        spin_h = Gtk.SpinButton.new_with_range(0, 23, 1)
        spin_h.set_value(hora)
        spin_h.set_numeric(True)
        spin_h.set_wrap(True)
        linha.append(spin_h)
        linha.append(Gtk.Label(label=":"))
        spin_m = Gtk.SpinButton.new_with_range(0, 59, 1)
        spin_m.set_value(minuto)
        spin_m.set_numeric(True)
        spin_m.set_wrap(True)
        linha.append(spin_m)
        area.append(linha)

        def _resposta(_d, resposta):
            if resposta == Gtk.ResponseType.OK:
                ao_escolher(_com_hora(_hoje_id(), int(spin_h.get_value()),
                                      int(spin_m.get_value())))
            dialogo.destroy()

        dialogo.connect("response", _resposta)
        dialogo.present()

    def _abrir_exportar(self):
        janela = self.app.janela if getattr(self.app, "janela", None) else None
        dialogo = Gtk.Dialog(title="Export hours", transient_for=janela, modal=True)
        dialogo.add_button("CLOSE", Gtk.ResponseType.CLOSE)
        dialogo.set_default_size(560, -1)

        area = dialogo.get_content_area()
        area.set_spacing(8)
        area.set_margin_top(12)
        area.set_margin_bottom(12)
        area.set_margin_start(14)
        area.set_margin_end(14)

        titulo = Gtk.Label(label="EXPORT HOURS", xalign=0)
        titulo.add_css_class("horas-titulo")
        area.append(titulo)

        self.lbl_exp_resumo = Gtk.Label(label="", xalign=0)
        self.lbl_exp_resumo.add_css_class("horas-sub")
        area.append(self.lbl_exp_resumo)

        grade = Gtk.Grid()
        grade.set_row_spacing(6)
        grade.set_column_spacing(12)
        area.append(grade)

        campos = {}

        def _campo(linha, coluna, rotulo, valor):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            lbl = Gtk.Label(label=rotulo, xalign=0)
            lbl.add_css_class("horas-mini")
            box.append(lbl)
            ent = Gtk.Entry()
            ent.add_css_class("spook-input")
            ent.set_text(valor)
            ent.set_size_request(110, -1)
            box.append(ent)
            grade.attach(box, coluna, linha, 1, 1)
            return ent

        campos["valor_hora"] = _campo(0, 0, "R$/HOUR", "")
        campos["jornada"] = _campo(0, 1, "DAY JOURNEY (h)", "8")
        campos["extra"] = _campo(1, 0, "OVERTIME %", "50")
        campos["feriado"] = _campo(1, 1, "HOLIDAY %", "100")
        campos["fds"] = _campo(1, 2, "WEEKEND %", "50")

        chk = Gtk.CheckButton(label="count hours above journey as overtime")
        chk.set_active(True)
        chk.add_css_class("horas-linha")
        area.append(chk)

        lbl_total = Gtk.Label(label="", xalign=0)
        lbl_total.add_css_class("horas-total")
        area.append(lbl_total)
        lbl_tipos = Gtk.Label(label="", xalign=0)
        lbl_tipos.add_css_class("horas-sub")
        area.append(lbl_tipos)
        lbl_resultado = Gtk.Label(label="", xalign=0)
        lbl_resultado.add_css_class("horas-sub")
        lbl_resultado.set_wrap(True)
        area.append(lbl_resultado)

        def _num(texto):
            try:
                return float(str(texto).replace(",", ".").strip() or 0)
            except ValueError:
                return 0.0

        def _resumo():
            cfg = dict(
                valor_hora=_num(campos["valor_hora"].get_text()),
                jornada_min=int(max(0.0, _num(campos["jornada"].get_text())) * 60),
                extra_pct=_num(campos["extra"].get_text()),
                feriado_pct=_num(campos["feriado"].get_text()),
                fds_pct=_num(campos["fds"].get_text()),
                contar_extras=chk.get_active(),
            )
            resumo = calcular(self.listar(), **cfg)
            return resumo

        def _atualizar(_widget=None, *_args):
            resumo = _resumo()
            self.lbl_exp_resumo.set_text(
                "%d day(s) · %s · %s overtime" % (
                    len(resumo["linhas"]),
                    _fmt_duracao(resumo["total_min"]),
                    _fmt_duracao(resumo["total_extras"]),
                ))
            lbl_total.set_text("TOTAL: %s  ·  %s" % (
                _fmt_duracao(resumo["total_min"]), _dinheiro(resumo["valor_total"])))
            lbl_tipos.set_text(
                "normal %s · weekend %s · holiday %s" % (
                    _fmt_duracao(resumo["min_normal"]),
                    _fmt_duracao(resumo["min_fds"]),
                    _fmt_duracao(resumo["min_feriado"]),
                ))
            return resumo

        for ent in campos.values():
            ent.connect("changed", _atualizar)
        chk.connect("toggled", _atualizar)

        acoes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        acoes.set_halign(Gtk.Align.END)
        btn_csv = Gtk.Button()
        btn_csv.set_child(Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6))
        ic_csv = Gtk.Label(label="\ue013")  # note.fill
        ic_csv.add_css_class("fin-ico")
        ic_csv.add_css_class("spook-msi")
        btn_csv.get_child().append(ic_csv)
        lbl_csv = Gtk.Label(label="EXPORT CSV")
        lbl_csv.add_css_class("spook-btn-text-sm")
        btn_csv.get_child().append(lbl_csv)
        btn_csv.add_css_class("spook-btn")

        btn_docx = Gtk.Button()
        btn_docx.set_child(Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6))
        ic_docx = Gtk.Label(label="\ue873")  # report
        ic_docx.add_css_class("fin-ico")
        ic_docx.add_css_class("spook-msi")
        btn_docx.get_child().append(ic_docx)
        lbl_docx = Gtk.Label(label="EXPORT DOCX")
        lbl_docx.add_css_class("spook-btn-text-sm")
        btn_docx.get_child().append(lbl_docx)
        btn_docx.add_css_class("spook-btn")
        btn_docx.add_css_class("spook-accent")
        acoes.append(btn_csv)
        acoes.append(btn_docx)
        area.append(acoes)

        def _exportar(formato):
            resumo = _atualizar()
            if not resumo["linhas"]:
                lbl_resultado.set_text("> no closed days to export")
                return
            try:
                EXPORT_DIR.mkdir(parents=True, exist_ok=True)
                inicio = resumo["inicio"] or _hoje_id()
                fim = resumo["fim"] or inicio
                carimbo = datetime.datetime.now().strftime("%H%M%S")
                base = "hours_%s_%s_%s" % (inicio, fim, carimbo)
                if formato == "csv":
                    destino = EXPORT_DIR / (base + ".csv")
                    destino.write_text(_csv(resumo), encoding="utf-8")
                else:
                    destino = EXPORT_DIR / (base + ".docx")
                    _docx_horas(resumo, str(destino))
                lbl_resultado.set_text("> saved to %s" % destino)
            except Exception as e:
                lbl_resultado.set_text("> failed: %s" % e)

        btn_csv.connect("clicked", lambda *_: _exportar("csv"))
        btn_docx.connect("clicked", lambda *_: _exportar("docx"))

        _atualizar()
        dialogo.connect("response", lambda d, _r: d.destroy())
        dialogo.present()
