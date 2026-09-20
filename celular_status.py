#!/usr/bin/env python3

import json
import re
import subprocess
import sys
from pathlib import Path

SAIDA = Path("/tmp/celular_status.json")


def adb(*args, timeout=12):
    cmd = ["adb"]
    ser = serial()
    if ser:
        cmd += ["-s", ser]
    cmd += list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception:
        return ""


def serial():
    out = subprocess.run(
        ["adb", "devices"], capture_output=True, text=True, timeout=5
    ).stdout
    for linha in out.splitlines()[1:]:
        if "device" in linha and "offline" not in linha and "unauthorized" not in linha:
            return linha.split()[0]
    return ""


def coletar():
    status = {
        "online": False,
        "bateria": None,
        "carregando": True,
        "ram_pct": None,
        "ram_usado_mb": None,
        "ram_total_mb": None,
        "disco_pct": None,
        "disco_usado_gb": None,
        "disco_total_gb": None,
        "load1": None,
        "cpu_pct": None,
        "temp_c": None,
    }
    if not serial():
        return status
    script = (
        "dumpsys battery | grep -E 'level|status|AC powered|USB powered|temperature'; "
        "cat /proc/loadavg; "
        "cat /proc/meminfo | grep -E 'MemTotal|MemAvailable'; "
        "df -h /data 2>/dev/null | tail -1; "
        "top -bn1 2>/dev/null | head -4"
    )
    saida = adb("shell", script)
    if not saida:
        return status
    status["online"] = True

    m = re.search(r"level:\s*(\d+)", saida)
    m_status = re.search(r"status:\s*(\d+)", saida)
    ac = re.search(r"AC powered:\s*(\w+)", saida)
    usb = re.search(r"USB powered:\s*(\w+)", saida)
    m_temp = re.search(r"temperature:\s*(\d+)", saida)
    if m:
        status["bateria"] = int(m.group(1))
    if m_status:
        code = int(m.group(1))
        powered = (ac and ac.group(1) == "true") or (usb and usb.group(1) == "true")
        status["carregando"] = powered or code == 2
    if m_temp:
        status["temp_c"] = round(int(m_temp.group(1)) / 10, 1)

    m_load = re.search(r"([\d.]+)\s+([\d.]+)\s+([\d.]+)", saida)
    if m_load:
        status["load1"] = m_load.group(1)

    m_total = re.search(r"MemTotal:\s*(\d+)", saida)
    m_avail = re.search(r"MemAvailable:\s*(\d+)", saida)
    if m_total:
        total = int(m_total.group(1))
        avail = int(m_avail.group(1)) if m_avail else 0
        status["ram_total_mb"] = total // 1024
        status["ram_usado_mb"] = (total - avail) // 1024
        status["ram_pct"] = int((total - avail) * 100 / total)

    for linha in saida.splitlines():
        if "%" in linha and "/data" in linha and linha.strip().startswith("/"):
            partes = linha.split()
            if len(partes) >= 5:
                try:
                    status["disco_total_gb"] = int(float(partes[1][:-1]))
                    status["disco_usado_gb"] = int(float(partes[2][:-1]))
                    status["disco_pct"] = int(partes[4].replace("%", ""))
                except ValueError:
                    pass
            break

    m_cpu_total = re.search(r"(\d+)%cpu", saida)
    m_cpu_idle = re.search(r"([\d.]+)%idle", saida)
    if m_cpu_total and m_cpu_idle:
        total = float(m_cpu_total.group(1))
        idle = float(m_cpu_idle.group(1))
        if total > 0:
            status["cpu_pct"] = int(100 * min(1.0, (total - idle) / total))
    return status


def tocar():
    if not serial():
        return False
    saida = adb(
        "shell", "cmd vibrator_manager synced oneshot -B 3000"
    )
    return "error" not in saida.lower()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--ring":
        sys.exit(0 if tocar() else 1)
    SAIDA.write_text(json.dumps(coletar(), ensure_ascii=False))