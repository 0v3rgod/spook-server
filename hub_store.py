#!/usr/bin/env python3
import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path

REMOTE = os.environ.get("SPOOK_REMOTE", "gdrive:SPOOK")
# 2026-10-21: desativado por decisão do usuário. NÃO sincronizar com Google Drive
# (token revogado). Backup vai para nextcloud conforme configuração.
DRIVE_SYNC_DISABLED = False
CHAT = "chat"
MEDIA = "media"
TASKS = "tasks"
TASKS_FILE = "tasks.json"
FINANCE = "finance"
ORIGEM = "arch"

# Espelhos locais: rclone sync (1 chamada) + leitura de disco = muito mais rapido
# que o antigo lsjson + 1 rclone cat por mensagem.
_DADOS = Path(os.environ.get("SPOOK_DADOS", "/tmp"))
_CHAT_DIR = _DADOS / Path(os.environ.get("SPOOK_CHAT_DIR", "spook_chat_local"))
_TASKS_DIR = _DADOS / Path(os.environ.get("SPOOK_TASKS_DIR", "spook_tasks_local"))
_TASKS_CACHE = _DADOS / Path(os.environ.get("SPOOK_TASKS_CACHE", "spook_tasks_cache.json"))
_FINANCE_DIR = _DADOS / Path(os.environ.get("SPOOK_FINANCE_DIR", "spook_finance_local"))
_FINANCE_CACHE = _DADOS / Path(os.environ.get("SPOOK_FINANCE_CACHE", "spook_finance_cache.json"))

# Um unico sync por vez em toda a MAQUINA, nao por processo: servidor, overlay e
# qualquer processo importando hub_store rodam refreshers concorrentes no MESMO
# espelho; 2 rclone sync simultaneos no mesmo dir clobram um ao outro (cada um
# poda o que o outro baixou, com cache de listagem do Drive). O flock serializa.
_CHAT_LOCK = threading.Lock()
_TASKS_LOCK = threading.Lock()
_REFRESHER_STARTED = False
_REFRESHER_INTERVAL = 5
_CHAT_VERSION = [0]
_ULTIMO_SYNC = [0.0]
_SYNC_LOCK_PATH = "/tmp/spook_sync.lock"

import fcntl


def _acquire_sync_lock():
    """Tenta exclusao mutua entre processos (nao-bloqueante). Retorna fd ou None."""
    fd = open(_SYNC_LOCK_PATH, "a+")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        fd.close()
        return None


def _release_sync_lock(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
    except (OSError, ValueError):
        pass


# Pendentes: mensagens escritas localmente (local-first) cujo upload ao Drive
# ainda nao confirmou. O rclone sync prunaria esses .json do espelho local (que
# e COMPARTILHADO entre servidor/overlay), entao sao "pinados" de volta apos cada
# sync ate o upload confirmar. Como varios processos escrevem no mesmo espelho,
# a fila fica em ARQUIVO com flock BLOQUEANTE (a fila e curta e o lock so vive
# microssegundos; quem sincroniza re-aplica TODOS, nao so os do proprio processo —
# era isso que fazia a msg sumir/voltar). Se um processo morrer segurando o lock,
# o kernel libera sozinho ao fechar o fd.
_PENDING_FILE = Path("/tmp/spook_pending.json")
_PENDING_LOCK_PATH = "/tmp/spook_pending.lock"
_PENDING = {}


def _pend_lock():
    fd = open(_PENDING_LOCK_PATH, "a+")
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


# Serializa as GRAVACOES NO DRIVE entre processos do mesmo host. O lock do
# pending (acima) so protege o arquivo de fila; sem este, servidor E overlay
# faziam copyto/rcat do mesmo nome em paralelo e o Drive guardava 2 documentos
# identicos (nome repetido) -> o chat ficava com mensagem ja gravada duas vezes.
_UPLOAD_LOCK_PATH = "/tmp/spook_flush.lock"


def _upload_lock():
    fd = open(_UPLOAD_LOCK_PATH, "a+")
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _carregar_pending():
    try:
        arr = json.loads(_PENDING_FILE.read_text())
        return {m["id"]: m for m in arr if isinstance(m, dict) and m.get("id")} if isinstance(arr, list) else {}
    except Exception:
        return {}


def _salvar_pending():
    try:
        tmp = _PENDING_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(list(_PENDING.values())))
        tmp.replace(_PENDING_FILE)
    except Exception:
        pass


def _pin(msg, _tipo_arquivo=None, _src=None):
    """Registra a msg como pendente no arquivo compartilhado (sobrescreve se ja
    existir; mesmo id enviado de 2 process diferentes e idempotente)."""
    item = dict(msg)
    item["_tipo_arquivo"] = _tipo_arquivo or ""
    item["_src"] = _src or ""
    fd = _pend_lock()
    try:
        _PENDING.update(_carregar_pending())
        _PENDING[msg["id"]] = item
        _salvar_pending()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


def _flush_pending():
    """Tenta subir TODOS os pendentes do arquivo compartilhado (de qualquer
    processo). Após cada sync, quem pegar o flock re-aplica os que sobraram.
    A gravacao no Drive e serializada com _upload_lock e so sobe o que ainda
    NAO existe (o Drive permite nomes duplicados, e 2 processos do host
    gravando o mesmo id criavam doc duplicado no chat/midia)."""
    if globals().get("DRIVE_SYNC_DISABLED"):
        # Desativado: manter apenas em memoria/espelho local. Não tenta subir pro Drive.
        return
    fd = _pend_lock()
    try:
        _PENDING.update(_carregar_pending())
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
    # lapide sempre vence: nao sobe msg apagada que sobrou no outbox
    _tombs_carregar()
    descartados = [m for m in _PENDING if m in _TOMBS]
    if descartados:
        for m in descartados:
            _PENDING.pop(m, None)
        _salvar_pending()
    for mid in [m for m in _PENDING.copy()]:
        fdu = _upload_lock()
        try:
            _PENDING.update(_carregar_pending())
            item = _PENDING.get(mid)
            if not item:
                continue
            msg = {k: v for k, v in item.items() if not k.startswith("_")}
            if item.get("_tipo_arquivo") and item.get("_src") and not _remoto_tem(item["arquivo"]):
                _run(["copyto", item["_src"], _base(item["arquivo"])], timeout=900)
            if not _remoto_tem(f"{CHAT}/{mid}.json"):
                _escrever_json(f"{CHAT}/{mid}.json", msg)
            if not _remoto_tem(f"{CHAT}/{mid}.json"):
                continue  # listagem ainda nao refletiu; mantem pendente (pin/merge)
            fd = _pend_lock()
            try:
                _PENDING.update(_carregar_pending())
                _PENDING.pop(mid, None)
                _salvar_pending()
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                fd.close()
        except Exception:
            continue
        finally:
            fcntl.flock(fdu, fcntl.LOCK_UN)
            fdu.close()


def pendentes_ids():
    fd = _pend_lock()
    try:
        _PENDING.update(_carregar_pending())
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
    return set(_PENDING)


def _gravar_local(msg):
    os.makedirs(_CHAT_DIR, exist_ok=True)
    try:
        (_CHAT_DIR / f"{msg['id']}.json").write_text(json.dumps(msg))
    except Exception:
        pass


def _aaplica_pendentes_locais():
    """Re-escreve no espelho local os .json que ainda constam como pendentes no
    arquivo compartilhado (protege contra o prune do rclone sync de OUTRO
    processo que tenha vencido o flock antes do upload confirmar)."""
    fd = _pend_lock()
    try:
        _PENDING.update(_carregar_pending())
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
    _tombs_carregar()
    for item in _PENDING.values():
        msg = {k: v for k, v in item.items() if not k.startswith("_")}
        if msg.get("id") in _TOMBS:
            continue  # lapidada: nao ressuscita no espelho local
        _gravar_local(msg)


def _sync_chat():
    with _CHAT_LOCK:
        fd = _acquire_sync_lock()
        if fd is None:
            return False  # outro processo ja esta sincronizando
        try:
            _sync_tombs()   # lapides primeiro: impede ressurreicao
            _flush_pending()
            _run(["sync", "--fast-list", "--update", _base(CHAT), str(_CHAT_DIR)], check=False, timeout=300)
            try:
                if _tombs_confere_locais():
                    _tombs_publica_bruto()   # lapide nao gruda no Drive: corrige o registro
            except Exception:
                pass
            # o sync poda local-exclusivos; pinamos os nossos de volta p/ nao sumir
            _aaplica_pendentes_locais()
            _CHAT_VERSION[0] += 1
            _ULTIMO_SYNC[0] = time.time()
            return True
        except Exception:
            return False
        finally:
            _release_sync_lock(fd)


def _sync_tasks():
    with _TASKS_LOCK:
        fd = _acquire_sync_lock()
        if fd is None:
            return False
        try:
            _flush_tpending()
            os.makedirs(_TASKS_DIR, exist_ok=True)
            _run(["sync", "--fast-list", "--update", _base(TASKS), str(_TASKS_DIR)], check=False, timeout=120)
            _aaplica_tpend_locais()  # re-garante os nossos contra o prune
            _migra_tasks_json()      # converte a lista unica antiga, uma vez
            return True
        except Exception:
            return False
        finally:
            _release_sync_lock(fd)


# ---------------------------------------------------------------------------
# LAPIDES DE MENSAGEM APAGADA (portado do relay em 05/10/2026)
# `rclone sync` e BIDIRECIONAL: se a maquina A apaga uma mensagem e a maquina B
# ainda tem o arquivo no espelho local, o proximo sync da B RE-SOBE o arquivo e a
# mensagem volta (bug "apaguei e reapareceu" / "num aparelho sim, no outro
# nao"). A lapide e um registro unico no Drive (fonte da verdade) que todas as
# maquinas respeitam: antes de sincronizar, cada uma remove do espelho local
# todo id lapidado -> nunca mais sobe. Nunca usar `purge` aqui.
_TOMBS_REMOTE = "_apagados_chat.json"
_TOMB_CACHE = Path(os.environ.get("SPOOK_TOMB_CACHE", "/tmp/spook_chat_apagados.json"))
_TOMB_MAX_IDS = 4000
_TOMBS = {}          # {id: epoch do registro}
_TOMBS_VISTO = {}     # {id: ciclo de sync em que a lapide foi vista}
_CICLO = [0]         # contador de ciclos de sync (evita depender do relogio)
_TOMB_PUB_OK = [None]   # ultima versao publicada no Drive


def _tombs_carregar():
    global _TOMBS
    try:
        d = json.loads(_TOMB_CACHE.read_text())
        if isinstance(d, dict):
            _TOMBS = {str(k): float(v) for k, v in d.items()}
    except Exception:
        pass
    return _TOMBS


def _tombs_salvar():
    global _TOMBS
    if len(_TOMBS) > _TOMB_MAX_IDS:
        _TOMBS = dict(sorted(_TOMBS.items(), key=lambda kv: kv[1])[-_TOMB_MAX_IDS:])
    try:
        _TOMB_CACHE.write_text(json.dumps(_TOMBS))
    except Exception:
        pass


def marcar_apagado(msg_id):
    """Lapida a mensagem: cache local (sincrono) + Drive (background)."""
    try:
        _tombs_carregar()
        _TOMBS[str(msg_id)] = time.time()
        _tombs_salvar()
    except Exception:
        return
    try:
        _bg(_tombs_publicar)
    except Exception:
        pass


def _parse_tombs(txt):
    """Le o registro de lapides aceitando VARIOS objetos JSON concatenados.

    Duas maquinas gravando ao mesmo tempo podem deixar o objeto no Drive como
    "{...}{...}" (o json.loads normal quebra com 'Extra data' e a gente perderia
    a leitura inteira). Aqui parseamos objeto por objeto e unimos tudo."""
    out = {}
    dec = json.JSONDecoder()
    s = (txt or "").strip()
    i = 0
    while i < len(s):
        while i < len(s) and s[i] in " \r\n\t,":
            i += 1
        if i >= len(s):
            break
        try:
            obj, j = dec.raw_decode(s, i)
        except Exception:
            break
        i = j
        if isinstance(obj, dict):
            for k, v in obj.items():
                try:
                    out[str(k)] = float(v)
                except Exception:
                    pass
    return out


def _tombs_puxar():
    """Une as lapides do Drive no cache local."""
    _tombs_carregar()
    remoto = {}
    sujo = False
    try:
        p = _run(["cat", _base(_TOMBS_REMOTE)], check=False, timeout=60)
        if p.returncode == 0:
            txt = p.stdout.decode("utf-8", "replace")
            remoto = _parse_tombs(txt)
            t = txt.strip()
            if t and not t.endswith("}"):
                sujo = True  # objeto truncado/concatenado no Drive = precisa regravar
    except Exception:
        return False
    mudou = sujo
    for k, v in remoto.items():
        if v > _TOMBS.get(k, 0):
            _TOMBS[k] = v
            mudou = True
    for k in _TOMBS:
        if k not in remoto:
            mudou = True
    if mudou:
        _tombs_salvar()
    return mudou


def _tombs_publicar():
    """Le do Drive, une e regrava (last-writer-wins sem perder lapide de ninguem)."""
    _tombs_puxar()
    if _TOMBS != _TOMB_PUB_OK[0]:
        _tombs_publica_bruto()


def _tombs_purgar_locais():
    """Tira do espelho local todo id lapidado ANTES do sync, e garante que o id
    lapidado NAO existe mais no Drive (a lapide SEMPRE vence quem tentou
    re-sober). O sync so faz download: sem o arquivo no Drive ele nunca volta
    pro espelho."""
    if not _TOMBS:
        return 0
    n = 0
    for mid in list(_TOMBS):
        try:
            f = _CHAT_DIR / f"{mid}.json"
            if f.exists():
                f.unlink()
                n += 1
        except Exception:
            pass
    try:
        p = _run(["lsjson", _base(CHAT), "--files-only"], check=False, timeout=120)
        if p.returncode != 0:
            return n
        vivos = json.loads(p.stdout.decode("utf-8", "replace") or "[]")
    except Exception:
        return n
    for it in vivos:
        nome = it.get("Name") or ""
        if not nome.endswith(".json"):
            continue
        mid = nome[:-5]
        if mid in _TOMBS:
            try:
                _run(["deletefile", f"{_base(CHAT)}/{mid}.json"], check=False, timeout=120)
                n += 1
            except Exception:
                pass
    return n


def _tombs_confere_locais():
    """RODADA DEPOIS do sync. So descarta lapide se o arquivo no espelho for de
    mensagem CRIADA DEPOIS da lapide (id re-usado de verdade). Mensagem antiga
    que "voltou" e ressureicao: a lapide continua e o proximo _tombs_purgar_locais
    a tira de novo do Drive."""
    if not _TOMBS or not _CHAT_DIR.exists():
        return 0
    n = 0
    for k in list(_TOMBS):
        try:
            f = _CHAT_DIR / f"{k}.json"
            if not f.exists():
                continue
            msg = json.loads(f.read_text())
            ts_ms = msg.get("ts")
            if not isinstance(ts_ms, (int, float)):
                continue
            if ts_ms / 1000.0 > _TOMBS.get(k, 0) + 5:
                _TOMBS.pop(k, None)
                _TOMBS_VISTO.pop(k, None)
                n += 1
        except Exception:
            continue
    if n:
        _tombs_salvar()
    return n


def _tombs_publica_bruto():
    try:
        _run(["copyto", str(_TOMB_CACHE), _base(_TOMBS_REMOTE)], check=False, timeout=120)
        _TOMB_PUB_OK[0] = dict(_TOMBS)
    except Exception:
        return
    _tombs_dedupe()


def _tombs_dedupe():
    """O Drive ACEITA nomes duplicados. Se as duas maquinas gravam o registro no
    mesmo instante, o `copyto` cria mais um -> o `cat` concatena e o registro vira
    lixo (json quebrado, lapide perdida). Mantem a mais nova."""
    try:
        p = _run(["lsjson", REMOTE, "--files-only", "--include", "/" + _TOMBS_REMOTE], check=False, timeout=120)
        if p.returncode != 0:
            return
        if len(json.loads(p.stdout.decode() or "[]")) <= 1:
            return
        _run(["dedupe", REMOTE, "--include", "/" + _TOMBS_REMOTE, "--dedupe-mode", "newest"], check=False, timeout=300)
    except Exception:
        pass


def _sync_tombs():
    """Antes do sync: lapides que o outro lado nao sabe ainda. Depois do sync,
    o chamador roda _tombs_confere_locais() para lapide que nao grudou.
    Tudo dentro de try: a lapide e uma protecao, nunca pode derrubar o sync."""
    try:
        _tombs_puxar()
        _CICLO[0] += 1
        return _tombs_purgar_locais()
    except Exception:
        return 0


def _tombs_filtrar(msgs):
    """Rede de seguranca da leitura: nunca mostra msg cujo id esta lapidado
    (a janela entre o sync e o purge). Id re-usado de verdade (ts mais novo que a
    lapide) passa."""
    if not _TOMBS:
        return msgs
    out = []
    for m in msgs:
        mid = str(m.get("id") or "")
        tomb = _TOMBS.get(mid)
        if tomb is not None:
            ts = m.get("ts")
            if not isinstance(ts, (int, float)) or ts / 1000.0 <= tomb + 5:
                continue
        out.append(m)
    return out


def _start_refresher():
    global _REFRESHER_STARTED
    if _REFRESHER_STARTED:
        return
    _REFRESHER_STARTED = True
    _sync_chat()
    _sync_tasks()
    threading.Thread(target=_loop, daemon=True).start()


def _loop():
    while True:
        _flush_pending()  # sobe os nossos; nao depende do sync
        _sync_chat()      # so o processo que pegar o flock sincroniza
        time.sleep(1)
        _sync_tasks()
        _aaplica_pendentes_locais()  # garante que nada nosso sumiu do espelho
        time.sleep(1)
        _sync_finance()
        _aaplica_fpend_locais()
        time.sleep(max(2, _REFRESHER_INTERVAL - 2))


_start_refresher()


def _ler_cache_tasks():
    try:
        d = json.loads(_TASKS_CACHE.read_text())
        if isinstance(d, dict) and "tasks" in d:
            return {t["id"]: t for t in d["tasks"]}
    except Exception:
        pass
    return {}


def _salvar_cache_tasks(tasks_dict):
    tmp = _TASKS_CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"tasks": list(tasks_dict.values())}, ensure_ascii=False))
    tmp.replace(_TASKS_CACHE)


# Tasks segunda o mesmo padrao do chat: UM ARQUIVO POR TASK em gdrive:SPOOK/tasks/<id>.json.
# O arquivo unico tasks.json (antigo) causava read-modify-write com perda entre
# android(SAF) x servidor/relay x overlay. Pendentes em arquivo compartilhado com
# flock (mesma ideia do chat) — idempotente por id.
_TASKS_PEND_FILE = Path("/tmp/spook_tasks_pending.json")
_TASKS_PEND_LOCK = "/tmp/spook_tasks_pending.lock"
_TPEND = {}

# Finance: per-file JSON in gdrive:SPOOK/finance/<id>.json (same pattern as tasks)
_FINANCE_PEND_FILE = Path("/tmp/spook_finance_pending.json")
_FINANCE_PEND_LOCK = "/tmp/spook_finance_pending.lock"
_FPEND = {}


def _tpend_lock():
    fd = open(_TASKS_PEND_LOCK, "a+")
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _tcarregar():
    try:
        arr = json.loads(_TASKS_PEND_FILE.read_text())
        return {t["id"]: t for t in arr if isinstance(t, dict) and t.get("id")} if isinstance(arr, list) else {}
    except Exception:
        return {}


def _tsalvar():
    try:
        tmp = _TASKS_PEND_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(list(_TPEND.values()), ensure_ascii=False))
        tmp.replace(_TASKS_PEND_FILE)
    except Exception:
        pass


def _tpin(record):
    fd = _tpend_lock()
    try:
        _TPEND.update(_tcarregar())
        _TPEND[record["id"]] = record
        _tsalvar()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


def _flush_tpending():
    """Sobe TODAS as operacoes de task pendentes (create/update/delete) de
    qualquer processo. Idempotente por id."""
    fd = _tpend_lock()
    try:
        _TPEND.update(_tcarregar())
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
    for tid in list(_TPEND):
        rec = _TPEND.get(tid)
        if not rec:
            continue
        fdu = _upload_lock()
        try:
            _TPEND.update(_tcarregar())
            rec = _TPEND.get(tid)
            if not rec:
                continue
            if rec.get("__del"):
                if _remoto_tem(f"{TASKS}/{tid}.json"):
                    _run(["deletefile", _base(f"{TASKS}/{tid}.json")], check=False)
                alvo = False
            else:
                _gravar_task_local(rec)
                if not _remoto_tem(f"{TASKS}/{tid}.json"):
                    _run(["copyto", str(_TASKS_DIR / f"{tid}.json"), _base(f"{TASKS}/{tid}.json")], check=False)
                alvo = True
            # so da como confirmado quando a LISTA do Drive refletir a mudanca;
            # enquanto isso o pendente continua vivo (pin + merge no listar)
            if _remoto_tem(f"{TASKS}/{tid}.json") != alvo:
                continue
            fd = _tpend_lock()
            try:
                _TPEND.update(_tcarregar())
                _TPEND.pop(tid, None)
                _tsalvar()
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                fd.close()
        except Exception:
            continue
        finally:
            fcntl.flock(fdu, fcntl.LOCK_UN)
            fdu.close()


def _gravar_task_local(task):
    if not task or not task.get("id"):
        return
    _TASKS_DIR.mkdir(parents=True, exist_ok=True)
    f = _TASKS_DIR / f"{task['id']}.json"
    tmp = f.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(task, ensure_ascii=False))
        tmp.replace(f)
    except Exception:
        pass


def _aaplica_tpend_locais():
    """Re-escreve no espelho local os .json pendentes (protege contra o prune do
    rclone sync de outro processo antes do upload confirmar)."""
    fd = _tpend_lock()
    try:
        _TPEND.update(_tcarregar())
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
    for rec in _TPEND.values():
        if not rec.get("__del"):
            _gravar_task_local(rec)


def _migra_tasks_json():
    """Converte a antiga lista unica tasks/tasks.json em N arquivos por-task
    (roda uma unica vez, quando o espelho ainda tem o arquivo antigo)."""
    f = _TASKS_DIR / TASKS_FILE
    if not f.exists():
        return
    try:
        arr = json.loads(f.read_text()).get("tasks", [])
    except Exception:
        return
    ok = False
    for t in arr if isinstance(arr, list) else []:
        if not t.get("id"):
            continue
        _gravar_task_local(t)
        try:
            _run(["copyto", str(_TASKS_DIR / f"{t['id']}.json"), _base(f"{TASKS}/{t['id']}.json")], check=False)
            ok = True
        except Exception:
            pass
    if ok:
        _run(["deletefile", _base(f"{TASKS}/{TASKS_FILE}")], check=False)
        try:
            f.unlink(missing_ok=True)
        except Exception:
            pass


def _merge_tasks(local: dict, remote: dict) -> dict:
    merged = {**local}
    for tid, t in remote.items():
        if tid and (tid not in merged or t.get("ts", 0) > merged[tid].get("ts", 0)):
            merged[tid] = t
    return merged


def _sorted_tasks(merged: dict):
    return sorted(merged.values(), key=lambda t: (t.get("feito", False), -t.get("ts", 0)))


# ============================== FINANCE ==============================
# Per-file JSON em gdrive:SPOOK/finance/<id>.json (padrao igual tasks)
# Cada arquivo = uma despesa com suas parcelas (installments).
# LWW por timestamp (em), merge simples, tombstone para delete.

def _fpend_lock():
    fd = open(_FINANCE_PEND_LOCK, "a+")
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _fcarregar():
    try:
        arr = json.loads(_FINANCE_PEND_FILE.read_text())
        return {f["id"]: f for f in arr if isinstance(f, dict) and f.get("id")} if isinstance(arr, list) else {}
    except Exception:
        return {}


def _fsalvar():
    try:
        tmp = _FINANCE_PEND_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(list(_FPEND.values()), ensure_ascii=False))
        tmp.replace(_FINANCE_PEND_FILE)
    except Exception:
        pass


def _fpin(record):
    fd = _fpend_lock()
    try:
        _FPEND.update(_fcarregar())
        _FPEND[record["id"]] = record
        _fsalvar()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


def _flush_fpending():
    """Sobe TODAS as operacoes de finance pendentes (create/update/delete) de
    qualquer processo. Idempotente por id."""
    fd = _fpend_lock()
    try:
        _FPEND.update(_fcarregar())
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
    for fid in list(_FPEND):
        rec = _FPEND.get(fid)
        if not rec:
            continue
        fdu = _upload_lock()
        try:
            _FPEND.update(_fcarregar())
            rec = _FPEND.get(fid)
            if not rec:
                continue
            if rec.get("__del"):
                if _remoto_tem(f"{FINANCE}/{fid}.json"):
                    _run(["deletefile", _base(f"{FINANCE}/{fid}.json")], check=False)
                alvo = False
            else:
                _gravar_finance_local(rec)
                if not _remoto_tem(f"{FINANCE}/{fid}.json"):
                    _run(["copyto", str(_FINANCE_DIR / f"{fid}.json"), _base(f"{FINANCE}/{fid}.json")], check=False)
                alvo = True
            if _remoto_tem(f"{FINANCE}/{fid}.json") != alvo:
                continue
            fd = _fpend_lock()
            try:
                _FPEND.update(_fcarregar())
                _FPEND.pop(fid, None)
                _fsalvar()
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                fd.close()
        except Exception:
            continue
        finally:
            fcntl.flock(fdu, fcntl.LOCK_UN)
            fdu.close()


def _gravar_finance_local(expense):
    if not expense or not expense.get("id"):
        return
    _FINANCE_DIR.mkdir(parents=True, exist_ok=True)
    f = _FINANCE_DIR / f"{expense['id']}.json"
    tmp = f.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(expense, ensure_ascii=False))
        tmp.replace(f)
    except Exception:
        pass


def _aaplica_fpend_locais():
    """Re-escreve no espelho local os .json pendentes (protege contra o prune do
    rclone sync de outro processo antes do upload confirmar)."""
    fd = _fpend_lock()
    try:
        _FPEND.update(_fcarregar())
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
    for rec in _FPEND.values():
        if not rec.get("__del"):
            _gravar_finance_local(rec)


def _sync_finance():
    with threading.Lock():
        fd = _acquire_sync_lock()
        if fd is None:
            return False
        try:
            _flush_fpending()
            os.makedirs(_FINANCE_DIR, exist_ok=True)
            _run(["sync", "--fast-list", "--update", _base(FINANCE), str(_FINANCE_DIR)], check=False, timeout=120)
            _aaplica_fpend_locais()
            return True
        except Exception:
            return False
        finally:
            _release_sync_lock(fd)


def _ler_cache_finance():
    try:
        d = json.loads(_FINANCE_CACHE.read_text())
        if isinstance(d, dict) and "expenses" in d:
            return {e["id"]: e for e in d["expenses"]}
    except Exception:
        pass
    return {}


def _salvar_cache_finance(expenses_dict):
    tmp = _FINANCE_CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"expenses": list(expenses_dict.values())}, ensure_ascii=False))
    tmp.replace(_FINANCE_CACHE)


def _merge_finance(local: dict, remote: dict) -> dict:
    merged = {**local}
    for fid, f in remote.items():
        if fid and (fid not in merged or f.get("em", 0) > merged[fid].get("em", 0)):
            merged[fid] = f
    return merged


def _sorted_expenses(merged: dict):
    return sorted(merged.values(), key=lambda e: (-e.get("em", 0)))


def listar_expenses():
    """Leitura instantanea do espelho local (mesmo principio do chat/tasks)."""
    locais = {}
    if _FINANCE_DIR.exists():
        for f in _FINANCE_DIR.glob("*.json"):
            try:
                e = json.loads(f.read_text())
                if e.get("id"):
                    locais[e["id"]] = e
            except Exception:
                continue
    # cobre o que ainda esta pendente (upload nao confirmado)
    fd = _fpend_lock()
    try:
        pend = _fcarregar()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
    for fid, rec in pend.items():
        if not rec.get("__del") and rec.get("id"):
            locais[fid] = rec
    _salvar_cache_finance(locais)
    return _sorted_expenses(locais)


def criar_expense(expense_data):
    """Cria uma nova despesa com parcelas."""
    nova = {
        "id": str(uuid.uuid4()),
        "em": int(time.time() * 1000),
        "origem": ORIGEM,
        **expense_data,
    }
    with threading.Lock():
        merged = _ler_cache_finance()
        merged[nova["id"]] = nova
        _salvar_cache_finance(merged)
        _gravar_finance_local(nova)
        _fpin(nova)
    return nova, _sorted_expenses(merged)


def atualizar_expense(expense_id, updates):
    """Atualiza uma despesa existente."""
    with threading.Lock():
        merged = _ler_cache_finance()
        if expense_id in merged:
            merged[expense_id] = {**merged[expense_id], **updates, "em": int(time.time() * 1000)}
        else:
            f = _FINANCE_DIR / f"{expense_id}.json"
            try:
                e = json.loads(f.read_text())
                merged[expense_id] = {**e, **updates, "em": int(time.time() * 1000)}
            except Exception:
                return _sorted_expenses(merged)
        _salvar_cache_finance(merged)
        _gravar_finance_local(merged[expense_id])
        _fpin(merged[expense_id])
    return _sorted_expenses(merged)


def apagar_expense(expense_id):
    """Apaga uma despesa (tombstone)."""
    with threading.Lock():
        merged = _ler_cache_finance()
        merged.pop(expense_id, None)
        _salvar_cache_finance(merged)
        try:
            (_FINANCE_DIR / f"{expense_id}.json").unlink(missing_ok=True)
        except Exception:
            pass
        _fpin({"id": expense_id, "__del": True})
    return _sorted_expenses(merged)


def atualizar_parcela(expense_id, parcela_index, updates):
    """Atualiza uma parcela específica de uma despesa."""
    with threading.Lock():
        merged = _ler_cache_finance()
        if expense_id in merged:
            expense = merged[expense_id]
            parcelas = expense.get("parcelas", [])
            if 0 <= parcela_index < len(parcelas):
                parcelas[parcela_index] = {**parcelas[parcela_index], **updates}
                expense["parcelas"] = parcelas
                expense["em"] = int(time.time() * 1000)
                _salvar_cache_finance(merged)
                _gravar_finance_local(expense)
                _fpin(expense)
            else:
                return _sorted_expenses(merged)
        else:
            f = _FINANCE_DIR / f"{expense_id}.json"
            try:
                e = json.loads(f.read_text())
                parcelas = e.get("parcelas", [])
                if 0 <= parcela_index < len(parcelas):
                    parcelas[parcela_index] = {**parcelas[parcela_index], **updates}
                    e["parcelas"] = parcelas
                    e["em"] = int(time.time() * 1000)
                    merged[expense_id] = e
                    _salvar_cache_finance(merged)
                    _gravar_finance_local(e)
                    _fpin(e)
                else:
                    return _sorted_expenses(merged)
            except Exception:
                pass
    return _sorted_expenses(merged)


def listar_parcelas_por_mes(ano, mes):
    """Retorna todas as parcelas (não pagas) de um mês específico, agrupadas por dia."""
    expenses = listar_expenses()
    resultado = {}
    for exp in expenses:
        if exp.get("removido"):
            continue
        for i, p in enumerate(exp.get("parcelas", [])):
            if p.get("paga"):
                continue
            due = p.get("dueDate") or p.get("due_date")
            if not due:
                continue
            import datetime
            dt = datetime.datetime.fromtimestamp(due / 1000)
            if dt.year == ano and dt.month == mes:
                dia = dt.day
                if dia not in resultado:
                    resultado[dia] = []
                resultado[dia].append({
                    "expense_id": exp["id"],
                    "expense_title": exp.get("title") or exp.get("titulo") or "Despesa",
                    "parcela_index": i,
                    "valor": p.get("valor", 0),
                    "due_date": due,
                    "paga": p.get("paga", False),
                })
    return resultado


def _base(rel):
    return f"{REMOTE}/{rel}"


def _run(args, input_bytes=None, check=True, timeout=120):
    p = subprocess.run(
        ["rclone", *args],
        input=input_bytes,
        capture_output=True,
        timeout=timeout,
    )
    if check and p.returncode != 0:
        raise RuntimeError(p.stderr.decode(errors="replace").strip() or f"rclone {' '.join(args)}")
    return p


def _remoto_tem(rel, timeout=40):
    """Confirma se um caminho existe no Drive (lsf de arquivo unico e barato).

    A listagem do Google Drive so reflete um upload alguns SEGUNDOS depois de o
    objeto existir (eventual consistency). Usado no flush: so damos o upload como
    confirmado quando a LISTA do Drive o ve — senao o rclone sync pull seguinte
    (que ve listagem velha) podaria o espelho local e a task sumia da view.
    Retorna True/False determinístico; em erro retorna False (tenta de novo)."""
    try:
        p = _run(["lsf", "--format", "p", _base(rel)], check=False, timeout=timeout)
        return p.returncode == 0 and bool(p.stdout.decode(errors="replace").strip())
    except Exception:
        return False


def _ler_chat_local():
    """Lê todas as msgs de disco (espelho local), em memória. Inventário do rclone fica de fora."""
    msgs = []
    if _CHAT_DIR.exists():
        for f in _CHAT_DIR.iterdir():
            if not f.name.endswith(".json"):
                continue
            try:
                msg = json.loads(f.read_text())
                if isinstance(msg, dict) and msg.get("id"):
                    msg["_arquivo"] = str(f)
                    msgs.append(msg)
            except Exception:
                continue
    return sorted(msgs, key=lambda m: m.get("ts", 0))


def _escrever_json(caminho, msg):
    _run(["rcat", _base(caminho)], input_bytes=json.dumps(msg).encode())


def _bg(fn):
    threading.Thread(target=fn, daemon=True).start()


def _upload_texto(msg):
    try:
        _escrever_json(f"{CHAT}/{msg['id']}.json", msg)
    except Exception:
        pass


def _upload_arquivo(p, destino, msg):
    try:
        _run(["copyto", str(p), _base(destino)], timeout=900)
        _escrever_json(f"{CHAT}/{msg['id']}.json", msg)
    except Exception:
        pass


def listar_mensagens():
    # Leitura instantanea do espelho local. O sync fica a cargo da thread de
    # background; o sincronismo inicial aconteceu no _start_refresher().
    _tombs_carregar()
    return _tombs_filtrar(_ler_chat_local())


def apagar_mensagem(msg_id):
    # lapide ANTES de tudo: e o registro que o outro lado respeita
    _tombs_carregar()
    marcar_apagado(msg_id)
    # local-first: remove do espelho ja; deletefile roda em background
    _delete_local(msg_id)
    fd = _pend_lock()
    try:
        _PENDING.update(_carregar_pending())
        _PENDING.pop(msg_id, None)
        _salvar_pending()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
    _bg(lambda: _delete_remoto(f"{msg_id}.json", _ler_msg_local(msg_id)))
    return True


def _ler_msg_local(msg_id):
    try:
        f = _CHAT_DIR / f"{msg_id}.json"
        return json.loads(f.read_text())
    except Exception:
        return {}


def _delete_local(msg_id):
    try:
        (_CHAT_DIR / f"{msg_id}.json").unlink(missing_ok=True)
    except Exception:
        pass


def _delete_remoto(nome, msg):
    try:
        _run(["deletefile", f"{_base(CHAT)}/{nome}"], check=False)
    except Exception:
        pass
    arq = msg.get("arquivo", "")
    if arq:
        try:
            _run(["deletefile", f"{_base(MEDIA)}/{arq.rsplit('/', 1)[-1]}"], check=False)
        except Exception:
            pass


def enviar_texto(texto, origem=ORIGEM):
    msg = {
        "id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "from": origem,
        "tipo": "texto",
        "texto": texto.strip(),
        "arquivo": "",
    }
    _pin(msg)
    _gravar_local(msg)
    _bg(_flush_pending)
    return msg


def enviar_texto_id(texto, origem=ORIGEM, msg_id=None, tipo="texto", arquivo=""):
    msg = {
        "id": msg_id or str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "from": origem,
        "tipo": tipo,
        "texto": texto.strip(),
        "arquivo": arquivo or "",
    }
    _pin(msg)
    _gravar_local(msg)
    _bg(_flush_pending)
    return msg


def enviar_arquivo(caminho, tipo="arquivo", caption="", msg_id=None):
    p = Path(caminho)
    destino = f"{MEDIA}/{int(time.time()*1000)}-{p.name}"
    msg = {
        "id": msg_id or str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "from": ORIGEM,
        "tipo": tipo,
        "texto": caption,
        "arquivo": destino,
    }
    _pin(msg, _tipo_arquivo=tipo, _src=str(p))
    _gravar_local(msg)
    # Garante disponibilidade imediata no espelho de midia local para a rota /media/
    os.makedirs(_MEDIA_DIR, exist_ok=True)
    try:
        ( _MEDIA_DIR / destino.split("/")[-1] ).write_bytes(p.read_bytes())
    except Exception:
        pass
    return msg


def enviar_midia_server(msg_id, texto, origem, tipo, arquivo_nome, caminho_bytes):
    """POST /upload do relay: persiste o json do chat e agenda o upload do
    arquivo p/ Drive (mesmo fluxo de persistencia das msgs, sem SAF). O arquivo
    ja esta salvo no espelho local de midia (/tmp/spook_media_local)."""
    msg = {
        "id": msg_id or str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "from": origem,
        "tipo": tipo,
        "texto": texto.strip(),
        "arquivo": f"{MEDIA}/{arquivo_nome}",
    }
    _pin(msg, _tipo_arquivo=tipo, _src=caminho_bytes)
    _gravar_local(msg)
    _bg(_flush_pending)
    return msg


def id_arquivo(nome):
    p = _run(["lsf", "--files-only", "--format", "pi", _base(MEDIA)], check=False, timeout=40)
    if p.returncode != 0:
        return None
    for linha in p.stdout.decode().splitlines():
        caminho, sep, arquivo_id = linha.partition(";")
        if sep and caminho == nome and arquivo_id:
            return arquivo_id
    return None


def baixar_bytes(rel):
    p = _run(["cat", _base(rel)], check=False)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode(errors="replace").strip())
    return p.stdout


_MEDIA_DIR = Path(os.environ.get("SPOOK_MEDIA_DIR", "/tmp/spook_media_local"))


# Modo local: quando SPOOK_DADOS é explicitado (relay/overlay), o remote rclone
# é um type=local e precisa dos subdirs chat/tasks/media existindo — o rcat do
# rclone NÃO cria o diretório de destino automaticamente.
if os.environ.get("SPOOK_DADOS"):
    for sub in (CHAT, TASKS, MEDIA):
        try:
            (_DADOS / sub).mkdir(parents=True, exist_ok=True)
        except Exception:
            pass


def baixar_midia(nome):
    """Baixa/obtem midia (media/<nome>) p/ cache local e retorna o path.
    Idempotente. Prioridade: cache local -> origem local de pendente compartilhado
    (local-first ainda nao publicado) -> remoto."""
    seguro = Path(nome.rsplit("/", 1)[-1]).name
    destino = _MEDIA_DIR / seguro
    if destino.exists() and destino.stat().st_size > 0:
        return str(destino)
    os.makedirs(_MEDIA_DIR, exist_ok=True)
    # 1) origem local de um pendente (msg criada aqui, upload ainda nao subiu)
    fd = _pend_lock()
    try:
        _PENDING.update(_carregar_pending())
        for item in _PENDING.values():
            src = item.get("_src")
            arq = item.get("arquivo") or ""
            if src and Path(arq).name == seguro and Path(src).exists():
                destino.write_bytes(Path(src).read_bytes())
                return str(destino)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
    # 2) remoto
    tmp = destino.with_suffix(destino.suffix + ".part")
    try:
        _run(["copyto", _base(f"{MEDIA}/{seguro}"), str(tmp)], timeout=900)
        tmp.replace(destino)
        return str(destino) if destino.exists() else None
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def listar_tasks():
    # Leitura instantanea do espelho (mesmo principio do chat): o sync fica por
    # conta da thread de background (_loop). Forcar sync aqui fazia cada poll
    # (a cada 2s) rodar um rclone sync completo + sofrer prune da listagem velha.
    locais = {}
    if _TASKS_DIR.exists():
        for f in _TASKS_DIR.glob("*.json"):
            if f.name == TASKS_FILE:
                continue
            try:
                t = json.loads(f.read_text())
                if t.get("id"):
                    locais[t["id"]] = t
            except Exception:
                continue
    # cobre o que ainda esta pendente (upload nao confirmado): nao deve sumir da view
    fd = _tpend_lock()
    try:
        pend = _tcarregar()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
    for tid, rec in pend.items():
        if not rec.get("__del") and rec.get("id"):
            locais[tid] = rec
    # cache so espelha o presente (nao deixa task apagada ressuscitar)
    _salvar_cache_tasks(locais)
    return _sorted_tasks(locais)


def salvar_tasks(tasks):
    with _TASKS_LOCK:
        for t in tasks:
            if not t.get("id"):
                continue
            _gravar_task_local(t)
            _tpin(t)


def criar_task(titulo, due_date=0):
    nova = {
        "id": str(uuid.uuid4()),
        "titulo": titulo.strip(),
        "feito": False,
        "criado_por": ORIGEM,
        "ts": int(time.time() * 1000),
    }
    if due_date:
        nova["due_date"] = due_date
    with _TASKS_LOCK:
        merged = _ler_cache_tasks()
        merged[nova["id"]] = nova
        _salvar_cache_tasks(merged)
        _gravar_task_local(nova)
        _tpin(nova)
    return nova, _sorted_tasks(merged)


def marcar_task(task_id, feito, due_date=None):
    agora = int(time.time() * 1000)
    with _TASKS_LOCK:
        merged = _ler_cache_tasks()
        if task_id in merged:
            update = {"feito": feito, "ts": agora}
            if due_date is not None:
                if due_date:
                    update["due_date"] = due_date
                elif "due_date" in merged[task_id]:
                    # mantém o due_date existente se não for passado None explícito
                    pass
            merged[task_id] = {**merged[task_id], **update}
        else:
            f = _TASKS_DIR / f"{task_id}.json"
            try:
                t = json.loads(f.read_text())
                update = {"feito": feito, "ts": agora}
                if due_date is not None:
                    if due_date:
                        update["due_date"] = due_date
                merged[task_id] = {**t, **update}
            except Exception:
                return _sorted_tasks(merged)
        _salvar_cache_tasks(merged)
        _gravar_task_local(merged[task_id])
        _tpin(merged[task_id])
    return _sorted_tasks(merged)


def atualizar_task_due_date(task_id, due_date):
    """Atualiza apenas a data de vencimento de uma task."""
    with _TASKS_LOCK:
        merged = _ler_cache_tasks()
        if task_id in merged:
            if due_date:
                merged[task_id] = {**merged[task_id], "due_date": due_date, "ts": int(time.time() * 1000)}
            else:
                task = merged[task_id]
                task.pop("due_date", None)
                task["ts"] = int(time.time() * 1000)
                merged[task_id] = task
            _salvar_cache_tasks(merged)
            _gravar_task_local(merged[task_id])
            _tpin(merged[task_id])
        else:
            f = _TASKS_DIR / f"{task_id}.json"
            try:
                t = json.loads(f.read_text())
                if due_date:
                    t = {**t, "due_date": due_date, "ts": int(time.time() * 1000)}
                else:
                    t.pop("due_date", None)
                    t["ts"] = int(time.time() * 1000)
                merged[task_id] = t
                _salvar_cache_tasks(merged)
                _gravar_task_local(t)
                _tpin(t)
            except Exception:
                pass
    return _sorted_tasks(merged)


def apagar_task(task_id):
    with _TASKS_LOCK:
        merged = _ler_cache_tasks()
        merged.pop(task_id, None)
        _salvar_cache_tasks(merged)
        try:
            (_TASKS_DIR / f"{task_id}.json").unlink(missing_ok=True)
        except Exception:
            pass
        _tpin({"id": task_id, "__del": True})
    return _sorted_tasks(merged)


def listar_spook():
    p = _run(["lsjson", "--files-only", _base("")], check=False)
    if p.returncode != 0 or not p.stdout.strip():
        return []
    return [i.get("Name") for i in json.loads(p.stdout) if not i.get("IsDir")]


def enviar_spook(caminho):
    p = Path(caminho)
    _run(["copyto", str(p), _base(p.name)])
    return p.name