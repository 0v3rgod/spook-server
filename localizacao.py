#!/usr/bin/env python3
import datetime
import fcntl
import json
import os
import re
import subprocess
import tempfile
import urllib.request

LOCALIZACAO_FILE = "localizacao.json"
REMOTE = "gdrive:SPOOK"
GEO_API = "https://ipinfo.io/json"
MAPA_TEMPLATE = "https://www.google.com/maps?q={lat},{lon}"
LOCK_FILE = "/tmp/spook_localizacao.lock"


def _ler():
    try:
        p = subprocess.run(
            ["rclone", "cat", f"{REMOTE}/{LOCALIZACAO_FILE}"],
            capture_output=True, timeout=60,
        )
        if p.returncode != 0:
            return {}
        bruto = p.stdout.decode("utf-8", errors="replace") or "{}"
        try:
            return json.loads(bruto)
        except Exception:
            # O antigo rclone rcat enfileirava escritas no MESMO arquivo,
            # deixando 2+ JSONs concatenados. Recupera o último objeto válido.
            return _ultimo_objeto_valido(bruto) or {}
    except Exception:
        return {}


def _ultimo_objeto_valido(bruto):
    """Extrai o último JSON object top-level válido de um texto com JSONs concatenados."""
    ultimo = None
    inicio = None
    profundidade = 0
    em_string = False
    escape = False
    for i, ch in enumerate(bruto):
        if em_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                em_string = False
            continue
        if ch == '"':
            em_string = True
        elif ch == "{":
            profundidade += 1
            if inicio is None:
                inicio = i
        elif ch == "}":
            profundidade -= 1
            if profundidade == 0 and inicio is not None:
                try:
                    ultimo = json.loads(bruto[inicio:i + 1])
                except Exception:
                    pass
                inicio = None
    return ultimo


def _escrever(dados):
    # rclone copyto (com temp local) sobrescreve o arquivo remoto;
    # rclone rcat anexava às vezes, corrompendo o JSON com 2 objetos.
    lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        bloco = json.dumps(dados, ensure_ascii=False)
        fd, temp = tempfile.mkstemp(dir="/tmp", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(bloco)
            subprocess.run(
                ["rclone", "copyto", temp, f"{REMOTE}/{LOCALIZACAO_FILE}"],
                capture_output=True, timeout=60,
            )
        finally:
            try:
                os.unlink(temp)
            except Exception:
                pass
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def hora_dict(ts=None):
    ts = ts or datetime.datetime.now().timestamp()
    return datetime.datetime.fromtimestamp(ts).strftime("%d/%m %H:%M")


def ip_geoloc():
    try:
        with urllib.request.urlopen(GEO_API, timeout=10) as r:
            d = json.loads(r.read().decode())
        loc = d.get("loc", "")
        lat, lon = loc.split(",") if loc else ("", "")
        return {
            "ip": d.get("ip"),
            "cidade": d.get("city"),
            "regiao": d.get("region"),
            "pais": d.get("country"),
            "org": d.get("org"),
            "lat": lat,
            "lon": lon,
        }
    except Exception:
        return {}


def atualizar_arch():
    dados = _ler()
    geo = ip_geoloc()
    if geo.get("lat") and geo.get("lon"):
        dados["arch"] = {
            "lat": geo.get("lat"),
            "lon": geo.get("lon"),
            "cidade": geo.get("cidade"),
            "regiao": geo.get("regiao"),
            "pais": geo.get("pais"),
            "org": geo.get("org"),
            "ip": geo.get("ip"),
            "hora": hora_dict(),
        }
        _escrever(dados)
    return dados


def atualizar_celular(payload):
    lat = payload.get("lat")
    lon = payload.get("lon")
    if lat is None or lon is None:
        return _ler()
    dados = _ler()
    dados["celular"] = {
        "lat": lat,
        "lon": lon,
        "precisao": payload.get("precisao"),
        "provedor": payload.get("provedor"),
        "hora": hora_dict(),
    }
    _escrever(dados)
    return dados


def _cidade(lat, lon):
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&accept-language=pt&zoom=10"
        req = urllib.request.Request(url, headers={"User-Agent": "SPOOK/1.0 (pentestlink)"})
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read().decode())
        addr = d.get("address", {}) or {}
        return addr.get("city") or addr.get("town") or addr.get("village") or addr.get("municipality") or ""
    except Exception:
        return ""


def estado():
    dados = _ler()
    cel = dados.get("celular") or {}
    # O GPS do celular (precisão ~14m) é o canônico; o do Arch vem de
    # geolocalização por IP e pode ficar a dezenas de km de distância.
    if (
        cel.get("lat") is not None
        and cel.get("lon") is not None
        and not cel.get("cidade")
    ):
        cidade = _cidade(cel["lat"], cel["lon"])
        if cidade:
            dados["celular"]["cidade"] = cidade
            _escrever(dados)
    return dados


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--arch":
        print(json.dumps(atualizar_arch(), ensure_ascii=False))
    else:
        print(json.dumps(estado(), ensure_ascii=False))
