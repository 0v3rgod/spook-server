#!/usr/bin/env python3

import datetime
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request
import uuid
from pathlib import Path

import gi
import socketio

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango

import sys
BASE = Path.home() / "pentestlink-servidor"
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import hub_store
from spook_leitor import SpookLeitorManager
from spook_pad import SpookPadManager

BATERIA_CELULAR = Path("/tmp/bateria_celular.txt")
CELULAR_STATUS = Path("/tmp/celular_status.json")
CELULAR_STATUS_PY = BASE / "celular_status.py"
API_BASE = "http://127.0.0.1:5000"
# Hub: Oracle Relay (sempre online, independente do notebook).
HUB_URL = "http://100.70.88.101:5000"
SOCKET_URL = HUB_URL
MIDIA_OVERLAY_DIR = Path("/tmp/spook_media_overlay")
ICON_ROUND = Path.home() / ".local/share/icons/spook_round.png"


ICON = ICON_ROUND if ICON_ROUND.exists() else Path.home() / ".local/share/icons/spook.png"
AI_URL = "http://100.70.88.101:5000/ia"

CSS = """
window.spook {
  background: #0B0A12; color: #F4F1FF;
  font-family: "Roboto", "Noto Sans", sans-serif;
  font-size: 14px;
}
.spook-root { background: #0B0A12; padding: 16px; }
.spook-neon {
  background-image: linear-gradient(to right, #FF4D9A, #B44AFF 38%, #5CE1E6 72%, #9CFF57 90%, rgba(156, 255, 87, 0));
  border-radius: 2px;
}
.spook-title { font-size: 20px; font-weight: 700; color: #FF4D9A; }
.spook-sub { font-size: 12px; color: #B8B4C8; }
.spook-status {
  font-family: "JetBrains Mono", "JetBrainsMono NF", monospace;
  font-size: 11px; font-weight: 700;
}
.spook-card, .spook-rich-card {
  background: #16141F; border-radius: 18px; border: 1px solid #3A3648;
  padding: 16px 18px;
}
.spook-card-title { font-size: 12px; font-weight: 700; color: #5CE1E6; }
.spook-metric {
  font-family: "JetBrains Mono", "JetBrainsMono NF", monospace;
  font-size: 12px; font-weight: 500; color: #F4F1FF;
}
.spook-note { font-size: 11px; color: #B8B4C8; }
.spook-loc { font-size: 13px; color: #F4F1FF; }
.spook-ok { color: #5CE1E6; }
.spook-warn { color: #FF7A45; }
.spook-err { color: #FF5C6C; }
.spook-card-android { border: 1px solid rgba(255, 77, 154, 0.35); }
.spook-card-arch { border: 1px solid rgba(92, 225, 230, 0.32); }

.spook-msi {
  font-family: "Material Symbols Rounded";
  font-size: 17px;
  color: #5CE1E6;
  background-color: #1E1C2A;
  border: 1px solid #3A3648;
  border-radius: 10px;
  padding: 5px;
  min-width: 20px; min-height: 20px;
}
.spook-card-android .spook-msi {
  color: #FF4D9A; background-color: rgba(255, 77, 154, 0.14);
  border-color: rgba(255, 77, 154, 0.35);
}
.spook-card-arch .spook-msi {
  color: #5CE1E6; background-color: rgba(92, 225, 230, 0.12);
  border-color: rgba(92, 225, 230, 0.32);
}
.spook-msi-android {
  color: #FF4D9A; background-color: rgba(255, 77, 154, 0.14);
  border-color: rgba(255, 77, 154, 0.32);
}
.spook-card-android .spook-msi.spook-msi-arch {
  color: #5CE1E6; background-color: rgba(92, 225, 230, 0.12);
  border-color: rgba(92, 225, 230, 0.32);
}

.spook-gal-image { border-left: 4px solid #FF7A45; }
.spook-gal-video { border-left: 4px solid #B44AFF; }
.spook-gal-audio { border-left: 4px solid #5CE1E6; }
.spook-gal-file  { border-left: 4px solid #9AA4B2; }
.spook-gal-badge { font-size: 10px; font-weight: 700; border-radius: 8px; padding: 1px 7px; }
.spook-gal-badge-all   { color: #FF4D9A; background-color: rgba(255,77,154,0.22); }
.spook-gal-badge-image { color: #FF7A45; background-color: rgba(255,122,69,0.16); }
.spook-gal-badge-video { color: #B44AFF; background-color: rgba(180,74,255,0.16); }
.spook-gal-badge-audio { color: #5CE1E6; background-color: rgba(92,225,230,0.16); }
.spook-gal-badge-file  { color: #9AA4B2; background-color: rgba(154,164,178,0.16); }
button.spook-gal-filter {
  background-image: none; background-color: transparent; box-shadow: none;
  color: #8A8F98; border: 1px solid #3A3648; border-radius: 50px;
  padding: 4px 12px; font-size: 11px; font-weight: 600;
  min-height: 0; min-width: 0; transition: all 140ms ease;
}
button.spook-gal-filter:hover { color: #F4F1FF; border-color: #6E6A81; }
button.spook-gal-filter-all-on   { color: #FF4D9A; border-color: #FF4D9A; background-color: rgba(255,77,154,0.18); }
button.spook-gal-filter-image-on { color: #FF7A45; border-color: #FF7A45; background-color: rgba(255,122,69,0.18); }
button.spook-gal-filter-video-on { color: #B44AFF; border-color: #B44AFF; background-color: rgba(180,74,255,0.18); }
button.spook-gal-filter-audio-on { color: #5CE1E6; border-color: #5CE1E6; background-color: rgba(92,225,230,0.18); }
button.spook-gal-filter-file-on  { color: #9AA4B2; border-color: #9AA4B2; background-color: rgba(154,164,178,0.18); }

.spook-msg-arch {
  background: #16141F; border: 1px solid rgba(92, 225, 230, 0.45);
  border-radius: 18px; padding: 14px 16px; margin: 4px 0;
}
.spook-msg-phone {
  background: #211726; border: 1px solid rgba(255, 77, 154, 0.5);
  border-radius: 18px; padding: 14px 16px; margin: 4px 0;
}
.spook-msg-h { font-size: 11px; font-weight: 700; }
.spook-msg-h-arch { color: #5CE1E6; }
.spook-msg-h-phone { color: #FF4D9A; }
.spook-msg-txt { font-size: 14px; color: #FFFFFF; font-weight: 500; }

button.spook-btn {
  background: #1E1C2A; color: #F4F1FF; border-radius: 22px;
  border: 1px solid #3A3648; padding: 7px 16px; font-size: 12px;
  font-weight: 600; transition: all 140ms ease;
}
button.spook-btn:hover { background: #2A2736; border-color: #6E6A81; }
button.spook-btn:active { background: #16141F; }
button.spook-btn:focus { outline: 2px solid #FF4D9A; outline-offset: 2px; }
button.spook-btn.spook-msi-btn { padding: 6px 11px; }
button.spook-accent {
  background: #FF4D9A; color: #0B0A12; border: none; font-weight: 700;
  transition: all 140ms ease;
}
button.spook-accent:hover { background: #FF6BA8; box-shadow: 0 0 14px 0 rgba(255, 77, 154, 0.45); }
button.spook-ok-btn { background: #5CE1E6; color: #0B0A12; border: none; font-weight: 700; }
button.spook-warn-btn { background: #FF7A45; color: #0B0A12; border: none; font-weight: 700; }
button.spook-err-btn { background: #FF5C6C; color: #0B0A12; border: none; font-weight: 700; }
button.spook-tab {
  background: transparent; background-image: none; color: #8A8F98;
  padding: 6px 16px; font-size: 11px; font-weight: 700;
  border: 1px solid #3A3648; border-radius: 999px;
  box-shadow: none; outline: none; transition: all 150ms ease;
}
button.spook-tab:hover {
  background: transparent; background-image: none; color: #F4F1FF;
  border-color: #6E6A81; box-shadow: none; outline: none;
}
button.spook-tab-on {
  background: rgba(255, 77, 154, 0.14); background-image: none; color: #FF4D9A;
  padding: 6px 16px; font-size: 11px; font-weight: 700;
  border: 1px solid rgba(255, 77, 154, 0.55); border-radius: 999px;
  box-shadow: none; outline: none;
}
.spook-input {
  background: #16141F; color: #F4F1FF; border: 1px solid #3A3648;
  border-radius: 22px; padding: 9px 16px; font-size: 13px;
  transition: all 140ms ease;
}
.spook-input:focus {
  border-color: #FF4D9A; box-shadow: 0 0 0 3px rgba(255, 77, 154, 0.15);
}
.spook-scan-card {
  background: #16141F; border-radius: 18px; border: 1px solid #3A3648;
  padding: 10px 12px; transition: all 140ms ease;
}
.spook-scan-card:hover { background: #1E1C2A; border-color: #FF4D9A; }
.spook-scan-section {
  font-family: "JetBrains Mono", "JetBrainsMono NF", monospace;
  font-size: 11px; font-weight: 700; color: #5CE1E6;
  padding: 10px 0 6px 0;
}
.spook-cmd-card {
  background: #16141F; border-radius: 18px; border: 1px solid #3A3648;
  padding: 10px; min-width: 100px; min-height: 70px;
  transition: all 140ms ease;
}
.spook-cmd-card:hover { border-color: #FF4D9A; background: #1E1C2A; }
.spook-msi-btn { font-family: "Material Symbols Rounded"; font-size: 16px; color: #5CE1E6; }
.spook-msi-btn.spook-gal-ico-image { color: #FF7A45; }
.spook-msi-btn.spook-gal-ico-video { color: #B44AFF; }
.spook-msi-btn.spook-gal-ico-audio { color: #5CE1E6; }
.spook-msi-btn.spook-gal-ico-file  { color: #9AA4B2; }
.spook-msi-copy { color: #FF4D9A; }
.spook-msi-del { color: #FF5C6C; }
.spook-msi-send { color: #FF4D9A; }
.spook-msi-inline { font-family: "Material Symbols Rounded"; }
.spook-btn-text { font-size: 12px; font-weight: 600; }
.spook-btn-text-sm { font-size: 11px; font-weight: 600; }
.spook-cmd-danger { border-color: #FF5C6C; }

notebook.spook-tabs > stack { background: #0B0A12; }
notebook.spook-tabs > stack > * { background: transparent; }
scrolledwindow, scrolledwindow > viewport { background: transparent; }
notebook.spook-tabs > header {
  background: #0B0A12;
  border-bottom: 1px solid #2A2736;
}
notebook.spook-tabs > header > tabs > tab {
  background: transparent; background-image: none; color: #B8B4C8;
  padding: 10px 20px; font-size: 12px; font-weight: 700; border: none;
  box-shadow: none; transition: all 150ms ease;
}
notebook.spook-tabs > header > tabs > tab:hover {
  background: transparent; background-image: none; color: #F4F1FF;
  box-shadow: none;
}
notebook.spook-tabs > header > tabs > tab:checked {
  background: transparent; background-image: none; color: #FF4D9A;
  border-bottom: 2px solid #FF4D9A; box-shadow: none; outline: none;
}
scrollbar { background-color: transparent; border: none; }
scrollbar slider {
  background-color: #3A3648; border-radius: 8px;
  min-width: 6px; min-height: 6px; margin: 2px;
}
scrollbar slider:hover { background-color: #FF4D9A; }
scrollbar:disabled slider { background-color: #2A2736; }
"""


def ler(caminho):
    try:
        return Path(caminho).read_text().strip()
    except OSError:
        return None


def hora(ts):
    try:
        return datetime.datetime.fromtimestamp(int(ts) / 1000).strftime("%d/%m %H:%M")
    except Exception:
        return ""


def _fmt_bytes(b):
    try:
        b = float(b)
    except (TypeError, ValueError):
        return "--"
    if b >= 1024 ** 3:
        return f"{b / 1024 ** 3:.1f} GB"
    if b >= 1024 ** 2:
        return f"{b / 1024 ** 2:.1f} MB"
    if b >= 1024:
        return f"{b / 1024:.0f} KB"
    return f"{b:.0f} B"


def executar(cmd):
    try:
        if isinstance(cmd, list):
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["sh", "-c", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def executar_output(cmd, timeout=30):
    try:
        if isinstance(cmd, list):
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        else:
            r = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, timeout=timeout)
        return r.stdout + r.stderr
    except Exception as e:
        return str(e)[:200]


_CPU_PREV = [0, 0]


def metricas_arch():
    bat = ler("/sys/class/power_supply/BAT1/capacity") or ler("/sys/class/power_supply/BAT0/capacity") or "--"
    bat_status = ler("/sys/class/power_supply/BAT1/status") or ler("/sys/class/power_supply/BAT0/status") or ""
    mem = {}
    try:
        for linha in Path("/proc/meminfo").read_text().splitlines():
            parts = linha.replace(":", " ").split()
            if len(parts) >= 2:
                mem[parts[0]] = int(parts[1])
        total = mem.get("MemTotal", 1)
        avail = mem.get("MemAvailable", 0)
        ram_pct = int((total - avail) * 100 / total)
        ram_used = (total - avail) // 1024
        ram_total = total // 1024
    except OSError:
        ram_pct, ram_used, ram_total = 0, 0, 0
    try:
        load = Path("/proc/loadavg").read_text().split()[0]
    except OSError:
        load = "--"
    try:
        temps = []
        for p in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
            try:
                t = int(p.read_text().strip()) // 1000
                temps.append(t)
            except (ValueError, OSError):
                pass
        cpu_temp = max(temps) if temps else 0
    except OSError:
        cpu_temp = 0
    try:
        disk = shutil.disk_usage("/")
        disk_pct = int(disk.used * 100 / disk.total)
        disk_used = disk.used // (1024 * 1024 * 1024)
        disk_total = disk.total // (1024 * 1024 * 1024)
    except OSError:
        disk_pct, disk_used, disk_total = 0, 0, 0
    try:
        linhas = Path("/proc/stat").read_text().splitlines()
        campos = linhas[0].split()[1:]
        total_t = sum(int(c) for c in campos)
        idle_t = int(campos[3]) + int(campos[4]) if len(campos) > 4 else int(campos[3])
        prev = _CPU_PREV
        if prev[0] > 0:
            d_total = total_t - prev[0]
            d_idle = idle_t - prev[1]
            cpu_pct = int(100 * (1 - (d_idle / d_total))) if d_total > 0 else 0
        else:
            cpu_pct = 0
        _CPU_PREV[0] = total_t
        _CPU_PREV[1] = idle_t
    except (OSError, ValueError, IndexError):
        cpu_pct = 0
    return bat, bat_status, ram_pct, ram_used, ram_total, load, cpu_temp, cpu_pct, disk_pct, disk_used, disk_total


PERFIS_SCAN = [
    {"nome": "LIVE HOST", "desc": "Ping sweep — checks if hosts respond.", "cmd": "nmap -sn {alvo}", "secao": "NETWORK", "alvo": True},
    {"nome": "NETWORK SCANNER", "desc": "Discovers hosts on an authorized CIDR.", "cmd": "nmap -sn {alvo}", "secao": "NETWORK", "alvo": True},
    {"nome": "COMMON PORTS", "desc": "Top 100 most used TCP ports.", "cmd": "nmap -sT --top-ports 100 {alvo}", "secao": "NETWORK", "alvo": True},
    {"nome": "FULL PORTS", "desc": "All 65535 TCP ports, faster min-rate.", "cmd": "nmap -p- --min-rate 3000 {alvo}", "secao": "NETWORK", "alvo": True},
    {"nome": "SERVICES", "desc": "Identifies services on specified ports.", "cmd": "nmap -sT -sV -p {portas} {alvo}", "secao": "NETWORK", "alvo": True, "portas": True},
    {"nome": "OS DETECTION", "desc": "Service fingerprint; OS limited without root.", "cmd": "nmap -sT -sV --osscan-limit {alvo}", "secao": "NETWORK", "alvo": True},
    {"nome": "COMPLETE SCAN", "desc": "Default scripts + version detection.", "cmd": "nmap -sC -sV {alvo}", "secao": "NETWORK", "alvo": True},
    {"nome": "VULN SCAN", "desc": "Nmap vulnerability scripts against the target.", "cmd": "nmap --script vuln {alvo}", "secao": "NETWORK", "alvo": True},
    {"nome": "VULN TARGETED", "desc": "Vuln scripts on 80,443,445,3389.", "cmd": "nmap -sV --script vuln -p80,443,445,3389 {alvo}", "secao": "NETWORK", "alvo": True},
    {"nome": "BANNER GRAB", "desc": "Grabs the service banner with netcat.", "cmd": "nc -nv {alvo} {portas}", "secao": "NETWORK", "alvo": True, "portas": True},

    {"nome": "HTTP HEADERS", "desc": "Status code and headers.", "cmd": "curl -I -L --max-redirs 3 --connect-timeout 10 --max-time 20 {alvo}", "secao": "WEB", "alvo": True},
    {"nome": "HTTP RESPONSE", "desc": "First 200KB of the page.", "cmd": "curl -L --max-redirs 3 --connect-timeout 10 --max-time 20 --range 0-204799 {alvo}", "secao": "WEB", "alvo": True},
    {"nome": "NIKTO", "desc": "Web server vulnerability scan.", "cmd": "nikto -h {alvo}", "secao": "WEB", "alvo": True, "tool": "nikto"},
    {"nome": "GOBUSTER DIRS", "desc": "Brute-forces common web directories.", "cmd": "gobuster dir -u {alvo} -w /usr/share/wordlists/common.txt", "secao": "WEB", "alvo": True, "tool": "gobuster"},

    {"nome": "DNS RECORDS", "desc": "Queries DNS records.", "cmd": "nslookup {alvo}", "secao": "ENUM", "alvo": True},
    {"nome": "SUBDOMAINS", "desc": "Brute-forces DNS subdomains.", "cmd": "gobuster dns -d {alvo} -w /usr/share/wordlists/subdomains.txt", "secao": "ENUM", "alvo": True, "tool": "gobuster"},
    {"nome": "SMB ENUM", "desc": "Enumerates SMB shares and users.", "cmd": "nmap --script smb-enum-shares,smb-enum-users -p445 {alvo}", "secao": "ENUM", "alvo": True},
    {"nome": "FTP ANON", "desc": "Checks anonymous FTP login.", "cmd": "nmap --script ftp-anon -p21 {alvo}", "secao": "ENUM", "alvo": True},
    {"nome": "WHOIS", "desc": "Queries domain registration.", "cmd": "whois {alvo}", "secao": "ENUM", "alvo": True},
    {"nome": "TRACEROUTE", "desc": "Network route to the target.", "cmd": "traceroute {alvo}", "secao": "ENUM", "alvo": True},

    {"nome": "MY NETWORK", "desc": "Local network interfaces and hostname.", "cmd": "ip -br addr; echo; hostname", "secao": "LOCAL", "alvo": False},
    {"nome": "LOCAL ENUM", "desc": "System identity, kernel, uptime, processes.", "cmd": "whoami; id; uname -a; echo; uptime; echo; ps aux --sort=-%cpu | head -15", "secao": "LOCAL", "alvo": False},
    {"nome": "SUID FIND", "desc": "Finds SUID files.", "cmd": 'find / -xdev -perm -4000 -type f -print 2>/dev/null | head -30', "secao": "LOCAL", "alvo": False},
    {"nome": "TCP CONNECT TEST", "desc": "Tests TCP connection to host:port.", "cmd": "nc -vz -w 5 {alvo} {portas}", "secao": "LOCAL", "alvo": True, "portas": True},

    {"nome": "AUTO PENTEST", "desc": "Full automated recon: discover, ports, services, HTTP, enum.", "cmd": "echo 'Run recon.sh manually from terminal'", "secao": "AUTO", "alvo": True},
]

MEDIA_COMMANDS = [
    ("PREV", "⏮", ["playerctl", "previous"]),
    ("PLAY", "⏯", ["playerctl", "play-pause"]),
    ("NEXT", "⏭", ["playerctl", "next"]),
    ("VOL-", "🔉", ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "5%-"]),
    ("VOL+", "🔊", ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "5%+"]),
]

NETWORK_COMMANDS = [
    ("INTERFACES", "🌐", "ip -br addr"),
    ("ROUTES", "🛣", "ip route"),
    ("DNS", "🔍", "resolvectl status 2>/dev/null || cat /etc/resolv.conf"),
    ("PORTS", "🔌", "ss -tlnp"),
    ("TAILSCALE", "🦎", "tailscale status 2>/dev/null || echo 'tailscale not running'"),
    ("PUBLIC IP", "🌍", "curl -s --max-time 5 https://ifconfig.me || echo 'offline'"),
]

SYSTEM_COMMANDS = [
    ("LOCK", "🔒", "loginctl lock-session", False),
    ("SCREENSHOT", "📸", "grim -g '$(slurp)' /tmp/spook-screenshot.png 2>/dev/null && notify-send 'Screenshot saved' || echo 'grim not installed'", False),
    ("RELOAD WM", "🔄", "hyprctl reload", False),
    ("SUSPEND", "💤", "systemctl suspend", True),
    ("REBOOT", "↻", "systemctl reboot", True),
    ("SHUTDOWN", "⏻", "systemctl poweroff", True),
]


class SpookOverlay(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="com.taylor.spook.overlay")
        self._chat_msgs = {}   # id -> msg (cache vivo; socket + espelho reconciliam)
        self._tasks_cache = {}   # id -> task (socket do relay; o PAD desenha)
        self._chat_last_merge = ""
        self._chat_last_draw = ""
        self._socket_online = False
        self._presenca_celular = False  # celular com socket ativo no servidor (via rede, nao adb USB)
        self.arch_status_label = Gtk.Label(xalign=0)
        self.arch_metrics_label = Gtk.Label(xalign=0)
        self.arch_bat_label = Gtk.Label(xalign=0)
        self.arch_ram_label = Gtk.Label(xalign=0)
        self.arch_disk_label = Gtk.Label(xalign=0)
        self.arch_cpu_label = Gtk.Label(xalign=0)
        self.arch_temp_label = Gtk.Label(xalign=0)
        self.arch_load_label = Gtk.Label(xalign=0)
        self.android_online_label = Gtk.Label(xalign=0)
        self.android_bat_label = Gtk.Label(xalign=0)
        self.android_ram_label = Gtk.Label(xalign=0)
        self.android_disk_label = Gtk.Label(xalign=0)
        self.android_cpu_label = Gtk.Label(xalign=0)
        self.android_temp_label = Gtk.Label(xalign=0)
        self.android_load_label = Gtk.Label(xalign=0)
        self.stop_threads = False
        self._enviando_arquivo = False
        self.local_label = Gtk.Label(xalign=0, wrap=True)
        self.local_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.home_local_label = Gtk.Label(xalign=0, wrap=True)
        self.home_local_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.home_arch_label = Gtk.Label(xalign=0, wrap=True)
        self.home_arch_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.home_arch_label.add_css_class("spook-loc")
        self.home_cel_label = Gtk.Label(xalign=0, wrap=True)
        self.home_cel_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.home_cel_label.add_css_class("spook-loc")
        self._dados_local = {}
        self.home_map_arch = Gtk.Button(label="OPEN ARCH MAPS ↗")
        self.home_map_arch.add_css_class("spook-btn")
        self.home_map_arch.connect("clicked", lambda *_: self._abrir_mapa("arch"))
        self.home_map_cel = Gtk.Button(label="OPEN ANDROID MAPS ↗")
        self.home_map_cel.add_css_class("spook-btn")
        self.home_map_cel.connect("clicked", lambda *_: self._abrir_mapa("celular"))
        self.home_map_refresh = Gtk.Button(label="REFRESH")
        self.home_map_refresh.add_css_class("spook-btn")
        self.home_map_refresh.connect("clicked", lambda *_: (self._atualizar_local(), self._bg(self._update_local_arch)))
        self.chat_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.chat_status_label = Gtk.Label(xalign=1, label="")
        self.chat_status_label.add_css_class("spook-note")
        self.chat_entry = None
        self.scan_output_label = Gtk.Label(xalign=0, wrap=True)
        self.scan_output_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.cmd_output_label = Gtk.Label(xalign=0, wrap=True)
        self.cmd_output_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.ai_chat_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.ai_entry = None
        self.ai_loading = False
        self.ai_session_id = ""
        self.status_label = None
        self.hub_sub_tabs = None
        self.hub_pages = None
        self.janela = None

    def do_activate(self):
        if self.janela is not None:
            self.janela.present()
            return
        display = Gdk.Display.get_default()
        css = Gtk.CssProvider()
        css.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_display(
            display, css, Gtk.STYLE_PROVIDER_PRIORITY_USER + 10
        )

        janela = Gtk.ApplicationWindow(application=self)
        janela.set_title("SPOOK")
        janela.set_default_size(640, 800)
        janela.set_resizable(True)
        janela.add_css_class("spook")
        if ICON.exists():
            try:
                pix = GdkPixbuf.Pixbuf.new_from_file_at_size(str(ICON), 64, 64)
                janela.set_icon(Gdk.Texture.new_for_pixbuf(pix))
            except Exception:
                janela.set_icon_name("spook")
        self.janela = janela

        raiz = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        raiz.add_css_class("spook-root")
        raiz.set_hexpand(True)
        raiz.set_vexpand(True)

        cabeca = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        if ICON.exists():
            try:
                img = Gtk.Image.new_from_file(str(ICON))
                img.set_pixel_size(24)
                cabeca.append(img)
            except Exception:
                pass
        titulo = Gtk.Label(label="SPOOK", xalign=0)
        titulo.add_css_class("spook-title")
        titulo.set_hexpand(True)
        cabeca.append(titulo)
        status = Gtk.Label(label="SPOOK // OFFLINE", xalign=1)
        status.add_css_class("spook-sub")
        status.add_css_class("spook-status")
        self.status_label = status
        cabeca.append(status)
        raiz.append(cabeca)

        fio = Gtk.Box()
        fio.add_css_class("spook-neon")
        fio.set_size_request(-1, 2)
        raiz.append(fio)

        # tornar a janela arrastável clicando no cabeçalho
        # (GTK4 não tem gtk_window_begin_move; arraste manual via janela.move)
        drag_origem = {}

        def _on_drag_begin(gesture, x, y):
            drag_origem.update(px=self.janela.get_position())

        def _on_drag_update(gesture, dx, dy):
            self.janela.move(int(drag_origem["px"][0] + dx), int(drag_origem["px"][1] + dy))

        gesto_arraste = Gtk.GestureDrag()
        gesto_arraste.set_button(Gdk.BUTTON_PRIMARY)
        gesto_arraste.connect("drag-begin", _on_drag_begin)
        gesto_arraste.connect("drag-update", _on_drag_update)
        cabeca.add_controller(gesto_arraste)

        tabs = Gtk.Notebook()
        tabs.add_css_class("spook-tabs")
        tabs.set_vexpand(True)

        # Abas com ícones SVG (estilo Android bottom nav)
        def _aba_icone_texto(icone_nome, texto):
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            img = self._icone_svg(icone_nome, 16)
            box.append(img)
            lbl = Gtk.Label(label=texto)
            box.append(lbl)
            return box

        tabs.append_page(self._pagina_home(), _aba_icone_texto("notebook", "HOME"))
        pagina_hub = self._pagina_hub()
        self._pagina_hub_widget = pagina_hub
        tabs.append_page(pagina_hub, _aba_icone_texto("chat", "CHAT"))
        self.leitor_mgr = SpookLeitorManager(self)
        pagina_leitor = self.leitor_mgr.criar_pagina()
        tabs.append_page(pagina_leitor, _aba_icone_texto("book", "READER"))
        # PAD: notas cross-device (robô: relay -> gdrive:SPOOK/pad)
        self.pad_mgr = SpookPadManager(self)
        self.pad_mgr.task_listar_cb = self._tarefas_pedir
        self.pad_mgr.task_add_cb = self._tarefa_adicionar
        self.pad_mgr.task_toggle_cb = self._tarefa_marcar
        self.pad_mgr.task_del_cb = self._tarefa_apagar
        pagina_pad = self.pad_mgr.criar_pagina()
        tabs.append_page(pagina_pad, _aba_icone_texto("note", "PAD"))
        self._pagina_leitor = pagina_leitor
        self._pagina_pad = pagina_pad
        self._tabs = tabs
        tabs.connect("switch-page", self._ao_trocar_aba)
        raiz.append(tabs)
        self._init_socketio()

        janela.set_child(raiz)
        janela.present()
        self._atualizar()
        GLib.timeout_add_seconds(2, self._atualizar)
        GLib.timeout_add_seconds(6, self._merge_chat_espelho)
        teclado = Gtk.EventControllerKey()
        teclado.connect("key-pressed", self._tecla)
        janela.add_controller(teclado)

    def _ao_trocar_aba(self, _nb, pagina, _indice):
        # entrar na aba PAD ja' mostra o estado mais novo (o poll de 20 s cobre o resto)
        try:
            if pagina is self._pagina_pad:
                self.pad_mgr.sincronizar()
                self.pad_mgr.pedir_tarefas()
        except Exception:
            pass
        # o chat abre sempre na mensagem mais nova
        try:
            if pagina is getattr(self, "_pagina_hub_widget", None):
                self._scroll_chat_fim(force=True)
        except Exception:
            pass

    def _tecla(self, _ctrl, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self.janela.close()
            return True
        return False

    def _card(self, titulo, filhos):
        caixa = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        caixa.add_css_class("spook-card")
        rotulo = Gtk.Label(label=titulo, xalign=0)
        rotulo.add_css_class("spook-card-title")
        caixa.append(rotulo)
        for filho in filhos:
            caixa.append(filho)
        return caixa

    def _scroll(self, filho):
        s = Gtk.ScrolledWindow()
        s.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        s.set_vexpand(True)
        s.set_hexpand(True)
        s.set_child(filho)
        return s

    def _clear(self, box):
        while (c := box.get_first_child()):
            box.remove(c)

    def _bg(self, fn):
        threading.Thread(target=lambda: self._safe(fn), daemon=True).start()

    def _safe(self, fn):
        try:
            fn()
        except Exception as e:
            msg = str(e)[:80]
            GLib.idle_add(lambda m=msg: self.arch_status_label.set_text(m))

    def _baixar_midia_http(self, nome):
        try:
            destino = MIDIA_OVERLAY_DIR / nome
            if destino.exists() and destino.stat().st_size > 0:
                return str(destino)
            destino.parent.mkdir(parents=True, exist_ok=True)
            url = SOCKET_URL + "/media/" + urllib.request.quote(nome)
            token = None
            arquivo_token = Path.home() / ".pentestlink" / "token_sessao.txt"
            if arquivo_token.exists():
                token = arquivo_token.read_text().strip() or None
            cab = {"Authorization": f"Bearer {token}"} if token else {}
            req = urllib.request.Request(url, headers=cab)
            tmp = destino.with_suffix(destino.suffix + ".dl")
            with urllib.request.urlopen(req, timeout=60) as r, open(str(tmp), "wb") as f:
                shutil.copyfileobj(r, f)
            tmp.rename(destino)
            if destino.exists() and destino.stat().st_size > 0:
                return str(destino)
        except Exception:
            pass
        return None

    def _abrir_midia_baixada(self, nome):
        path = self._baixar_midia_http(nome) or hub_store.baixar_midia(nome)
        if path:
            executar(["gio", "open", path])

    def _escolher_arquivo(self, callback):
        dialog = Gtk.FileDialog()
        dialog.open(self.janela, None, lambda d, res: self._abrir_escolha(d, res, callback))

    def _abrir_escolha(self, dialog, res, callback):
        try:
            f = dialog.open_finish(res)
            if f:
                self._bg(lambda: callback(f.get_path()))
        except Exception:
            pass

    def _show_output_dialog(self, titulo, texto):
        win = Gtk.Window(title=titulo, transient_for=self.janela, modal=True)
        win.set_default_size(600, 400)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("spook-root")
        lab = Gtk.Label(label=texto, xalign=0, wrap=True)
        lab.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        scroll = Gtk.ScrolledWindow()
        scroll.set_child(lab)
        scroll.set_vexpand(True)
        box.append(scroll)
        btn = Gtk.Button(label="CLOSE")
        btn.add_css_class("spook-btn")
        btn.add_css_class("spook-accent")
        btn.connect("clicked", lambda _: win.close())
        box.append(btn)
        win.set_child(box)
        win.present()

    def _ask_confirm(self, titulo, msg, on_yes):
        win = Gtk.Window(title="CONFIRM", transient_for=self.janela, modal=True)
        win.set_default_size(350, 150)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.add_css_class("spook-root")
        lab = Gtk.Label(label=msg, wrap=True)
        box.append(lab)
        row = Gtk.Box(spacing=8)
        yes = Gtk.Button(label="EXECUTE")
        yes.add_css_class("spook-err-btn")
        no = Gtk.Button(label="CANCEL")
        no.add_css_class("spook-btn")
        yes.connect("clicked", lambda _: (win.close(), on_yes()))
        no.connect("clicked", lambda _: win.close())
        row.append(yes)
        row.append(no)
        box.append(row)
        win.set_child(box)
        win.present()

    # ── HOME ──

    def _btn_icone(self, glyph, texto, horizontal=False, texto_css="spook-btn-text"):
        btn = Gtk.Button()
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL if horizontal else Gtk.Orientation.VERTICAL,
            spacing=3,
        )
        if glyph:
            ic = Gtk.Label(label=glyph)
            ic.add_css_class("spook-msi-btn")
            box.append(ic)
        tx = Gtk.Label(label=texto)
        tx.add_css_class(texto_css)
        box.append(tx)
        btn.set_child(box)
        return btn

    def _icone_svg(self, nome, tamanho=18):
        """Carrega um ícone SVG dos assets SPOOK (para abas de navegação)."""
        path = Path.home() / ".local/share/icons/spook" / f"{nome}.svg"
        try:
            img = Gtk.Image.new_from_file(str(path))
            img.set_pixel_size(tamanho)
            return img
        except Exception:
            return Gtk.Label(label="")

    def _icone_metric(self, chave, tamanho=18):
        """Cria um label com ícone Material Symbols para métricas (como no app Android)."""
        ic = Gtk.Label(label=self._MSI.get(chave, "\ue322"))
        ic.add_css_class("spook-msi")
        return ic

    # Ícones de métricas usam Material Symbols (igual ao app Android)
    # Ícones de navegação (abas) usam SVGs customizados da SPOOK
    _MSI = {
        "bat": "\ue1a5",
        "ram": "\ue322",
        "disk": "\uf3da",
        "cpu": "\ue30d",
        "temp": "\ue1ff",
        "load": "\ue9e4",
        "loc": "\ue0c8",  # Material Symbols "place" (pin)
        "server": "\ue2bd",
        "hub": "\ue875",
        "status": "\ue63e",
        "report": "\ue873",
        "media": "\ue413",
    }

    _NAV_ICONS = {
        "home": "notebook",    # HOME = notebook (notebook+celular no app)
        "chat": "chat",        # CHAT = nuvem de conversa
        "reader": "book",      # READER = livro
        "pad": "note",         # PAD = nota
        "galeria": "media",    # GALERIA = mídia
        "ghost": "ghost",      # SCANS = fantasminha
        "robot": "robot",      # IA = robozinho
    }

    def _rich_card(self, titulo, status_label, linhas, acento_css=""):
        caixa = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        caixa.add_css_class("spook-rich-card")
        if acento_css:
            caixa.add_css_class(acento_css)
        rotulo = Gtk.Label(label=titulo, xalign=0)
        rotulo.add_css_class("spook-card-title")
        caixa.append(rotulo)
        caixa.append(status_label)
        for chave, texto_widget in linhas:
            item = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            ic = self._icone_metric(chave)
            if isinstance(texto_widget, str):
                tx = Gtk.Label(label=texto_widget, xalign=0)
                tx.add_css_class("spook-metric")
                tx.set_hexpand(True)
                item.append(ic)
                item.append(tx)
            else:
                item.append(ic)
                texto_widget.add_css_class("spook-metric")
                item.append(texto_widget)
            item.set_hexpand(True)
            caixa.append(item)
        return caixa

    def _pagina_home(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        colunas = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        android_card = self._rich_card(
            "ANDROID",
            self.android_online_label,
            [
                ("bat", self.android_bat_label),
                ("ram", self.android_ram_label),
                ("disk", self.android_disk_label),
                ("cpu", self.android_cpu_label),
                ("temp", self.android_temp_label),
                ("load", self.android_load_label),
            ],
            acento_css="spook-card-android",
        )
        android_card.set_hexpand(True)
        arch_card = self._rich_card(
            "ARCH",
            self.arch_status_label,
            [
                ("bat", self.arch_bat_label),
                ("ram", self.arch_ram_label),
                ("disk", self.arch_disk_label),
                ("cpu", self.arch_cpu_label),
                ("temp", self.arch_temp_label),
                ("load", self.arch_load_label),
            ],
            acento_css="spook-card-arch",
        )
        arch_card.set_hexpand(True)
        colunas.append(android_card)
        colunas.append(arch_card)
        box.append(colunas)
        box.append(self._card_oracle_relay())
        box.append(self._card_local())
        return self._scroll(box)

    def _card_oracle_relay(self):
        """Card de status do Oracle Relay (sempre online)."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("spook-card")
        card.add_css_class("spook-card-arch")

        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ic = self._icone_metric("hub")
        ic.add_css_class("spook-msi-arch")
        hdr.append(ic)
        titulo = Gtk.Label(label="ORACLE RELAY", xalign=0)
        titulo.add_css_class("spook-card-title")
        titulo.add_css_class("spook-msg-h-arch")
        hdr.append(titulo)
        card.append(hdr)

        self.relay_hub_label = Gtk.Label(label="Oracle VM (100.70.88.101:5000)", xalign=0)
        self.relay_status_label = Gtk.Label(label="Checking...", xalign=0)
        self.relay_reports_label = Gtk.Label(label="—", xalign=0)
        self.relay_media_label = Gtk.Label(label="—", xalign=0)

        linhas = [
            ("hub", "Relay", self.relay_hub_label),
            ("status", "Status", self.relay_status_label),
            ("report", "Reports", self.relay_reports_label),
            ("media", "Media", self.relay_media_label),
        ]
        grade = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        grade.set_margin_top(8)
        for chave, nome, lbl_valor in linhas:
            linha = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            ic = self._icone_metric(chave)
            linha.append(ic)
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            nm = Gtk.Label(label=nome, xalign=0)
            nm.add_css_class("spook-note")
            lbl_valor.add_css_class("spook-loc")
            col.append(nm)
            col.append(lbl_valor)
            linha.append(col)
            grade.append(linha)

        card.append(grade)
        self._bg(self._atualizar_oracle_relay)
        GLib.timeout_add_seconds(30, self._refrescar_status_relay)
        return card

    def _refrescar_status_relay(self):
        """Status do Oracle Relay: Online/Offline + connected clients via /api/connected."""
        def _fazer():
            try:
                import urllib.request, json, ssl
                tok = None
                tf = Path.home() / ".pentestlink" / "token_sessao.txt"
                if tf.exists():
                    tok = tf.read_text().strip() or None
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                req = urllib.request.Request(f"{SOCKET_URL}/healthz")
                if tok:
                    req.add_header("Authorization", f"Bearer {tok}")
                with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
                    if r.status != 200:
                        cod = r.status
                        GLib.idle_add(lambda c=cod: self.relay_status_label.set_text(f"HTTP {c}"))
                        return
                # Check connected clients
                android_online = False
                try:
                    ireq = urllib.request.Request(f"{SOCKET_URL}/api/connected")
                    if tok:
                        ireq.add_header("Authorization", f"Bearer {tok}")
                    with urllib.request.urlopen(ireq, timeout=8, context=ctx) as ri:
                        conn = json.loads(ri.read().decode())
                    for info in conn.get("clients", {}).values():
                        if info.get("device") == "android":
                            android_online = True
                            break
                except Exception:
                    pass
                texto = "Online"
                if android_online:
                    texto += " · Android"
                GLib.idle_add(lambda t=texto: self.relay_status_label.set_text(t))
                GLib.idle_add(lambda: self._atualizar_status(android_online))
            except Exception:
                GLib.idle_add(lambda: self.relay_status_label.set_text("Offline"))
        self._bg(_fazer)
        return True

    def _atualizar_oracle_relay(self):
        """Queries /media and /relatorios on the Oracle Relay."""
        self._refrescar_status_relay()

        def _fazer():
            try:
                tok = None
                tf = Path.home() / ".pentestlink" / "token_sessao.txt"
                if tf.exists():
                    tok = tf.read_text().strip() or None
                url = f"{SOCKET_URL}/relatorios"
                req = urllib.request.Request(url)
                if tok:
                    req.add_header("Authorization", f"Bearer {tok}")
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
                    if r.status == 200:
                        data = json.loads(r.read().decode())
                        qtd = len(data.get("relatorios", []))
                        GLib.idle_add(lambda: self.relay_reports_label.set_text(f"{qtd} files"))
            except Exception:
                GLib.idle_add(lambda: self.relay_reports_label.set_text("—"))

            try:
                tok = None
                tf = Path.home() / ".pentestlink" / "token_sessao.txt"
                if tf.exists():
                    tok = tf.read_text().strip() or None
                url = f"{SOCKET_URL}/media"
                req = urllib.request.Request(url)
                if tok:
                    req.add_header("Authorization", f"Bearer {tok}")
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
                    if r.status == 200:
                        data = json.loads(r.read().decode())
                        qtd = len(data.get("items", []))
                        GLib.idle_add(lambda: self.relay_media_label.set_text(f"{qtd} files"))
            except Exception:
                GLib.idle_add(lambda: self.relay_media_label.set_text("—"))
        self._bg(_fazer)

    # ── SCANS ──

    def _pagina_scans(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        hdr = Gtk.Box(spacing=8)
        lbl = Gtk.Label(label="Local Scans", xalign=0)
        lbl.add_css_class("spook-title")
        lbl.set_hexpand(True)
        hdr.append(lbl)
        box.append(hdr)
        desc = Gtk.Label(label="Execute pentest commands directly on Arch.", xalign=0)
        desc.add_css_class("spook-note")
        box.append(desc)
        scan_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        last_secao = ""
        for perfil in PERFIS_SCAN:
            secao = perfil["secao"]
            if secao != last_secao:
                last_secao = secao
                sec_lbl = Gtk.Label(label=f"[ {secao} ]", xalign=0)
                sec_lbl.add_css_class("spook-scan-section")
                scan_list.append(sec_lbl)
            card_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            card_box.add_css_class("spook-scan-card")
            nome_lbl = Gtk.Label(label=perfil["nome"], xalign=0)
            nome_lbl.add_css_class("spook-ok")
            card_box.append(nome_lbl)
            desc_lbl = Gtk.Label(label=perfil["desc"], xalign=0, wrap=True)
            desc_lbl.add_css_class("spook-note")
            card_box.append(desc_lbl)
            cmd_lbl = Gtk.Label(label=perfil["cmd"], xalign=0)
            cmd_lbl.add_css_class("spook-note")
            card_box.append(cmd_lbl)
            evt = Gtk.GestureClick()
            evt.connect("pressed", lambda _c, _n, _x, _y, __p=perfil: self._abrir_scan_dialog(__p))
            card_box.add_controller(evt)
            scan_list.append(card_box)
        box.append(scan_list)
        sep = Gtk.Separator()
        box.append(sep)
        out_lbl = Gtk.Label(label="OUTPUT", xalign=0)
        out_lbl.add_css_class("spook-card-title")
        box.append(out_lbl)
        self.scan_output_label.set_text("Select a scan above.")
        box.append(self.scan_output_label)
        return self._scroll(box)

    def _abrir_scan_dialog(self, perfil):
        win = Gtk.Window(title=perfil["nome"], transient_for=self.janela, modal=True)
        win.set_default_size(500, 300)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.add_css_class("spook-root")
        vbox.append(Gtk.Label(label=perfil["desc"], xalign=0, wrap=True))
        cmd_preview = Gtk.Label(label=perfil["cmd"], xalign=0)
        cmd_preview.add_css_class("spook-note")
        vbox.append(cmd_preview)
        target_entry = None
        ports_entry = None
        if perfil.get("alvo"):
            target_entry = Gtk.Entry()
            target_entry.add_css_class("spook-input")
            target_entry.set_placeholder_text("Target (IP/domain/URL)")
            vbox.append(target_entry)
        if perfil.get("portas"):
            ports_entry = Gtk.Entry()
            ports_entry.add_css_class("spook-input")
            ports_entry.set_placeholder_text("Ports (e.g. 22,80,443)")
            vbox.append(ports_entry)
        row = Gtk.Box(spacing=8)
        run_btn = Gtk.Button(label="EXECUTE")
        run_btn.add_css_class("spook-ok-btn")
        cancel_btn = Gtk.Button(label="CANCEL")
        cancel_btn.add_css_class("spook-btn")

        def do_run():
            alvo = target_entry.get_text().strip() if target_entry else ""
            portas = ports_entry.get_text().strip() if ports_entry else ""
            cmd = perfil["cmd"]
            if "{alvo}" in cmd:
                cmd = cmd.replace("{alvo}", alvo)
            elif alvo:
                cmd += f" {alvo}"
            if "{portas}" in cmd:
                cmd = cmd.replace("{portas}", portas)
            elif portas:
                cmd += f" -p {portas}"
            win.close()
            self.scan_output_label.set_text(f"> {cmd}\nRunning...")
            self._bg(lambda: self._exec_scan(cmd))

        run_btn.connect("clicked", lambda _: do_run())
        cancel_btn.connect("clicked", lambda _: win.close())
        row.append(run_btn)
        row.append(cancel_btn)
        vbox.append(row)
        win.set_child(vbox)
        win.present()

    def _exec_scan(self, cmd):
        resultado = executar_output(cmd, timeout=120)
        GLib.idle_add(lambda: self.scan_output_label.set_text(f"$ {cmd}\n\n{resultado}"))

    # ── COMMANDS ──

    def _pagina_commands(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        lb = Gtk.Label(label="Commands", xalign=0)
        lb.add_css_class("spook-title")
        box.append(lb)
        lb = Gtk.Label(label="Execute system commands on Arch.", xalign=0)
        lb.add_css_class("spook-note")
        box.append(lb)

        media_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        media_card.add_css_class("spook-card")
        lb = Gtk.Label(label="MEDIA PLAYER", xalign=0)
        lb.add_css_class("spook-card-title")
        media_card.append(lb)
        media_row = Gtk.Box(spacing=6)
        for label, glyph, cmd in MEDIA_COMMANDS:
            btn = self._btn_icone(glyph, label)
            btn.add_css_class("spook-btn")
            btn.connect("clicked", lambda _, c=cmd: executar(c))
            media_row.append(btn)
        media_card.append(media_row)
        box.append(media_card)

        net_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        net_card.add_css_class("spook-card")
        lb = Gtk.Label(label="NETWORK INFO", xalign=0)
        lb.add_css_class("spook-card-title")
        net_card.append(lb)
        net_grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for label, glyph, cmd in NETWORK_COMMANDS:
            row = Gtk.Box(spacing=6)
            btn = self._btn_icone(glyph, label, horizontal=True)
            btn.add_css_class("spook-btn")
            btn.set_size_request(140, -1)

            def make_net_cb(c=cmd, l=label):
                def cb(_):
                    r = executar_output(c, timeout=15)
                    self._show_output_dialog(l, r)
                return cb

            btn.connect("clicked", make_net_cb())
            row.append(btn)
            net_grid.append(row)
        net_card.append(net_grid)
        box.append(net_card)

        sys_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        sys_card.add_css_class("spook-card")
        lb = Gtk.Label(label="SYSTEM", xalign=0)
        lb.add_css_class("spook-card-title")
        sys_card.append(lb)
        sys_grid = Gtk.Box(spacing=8)
        for label, glyph, cmd, danger in SYSTEM_COMMANDS:
            btn = self._btn_icone(glyph, label, texto_css="spook-btn-text-sm")
            btn.add_css_class("spook-cmd-card")
            btn.set_hexpand(True)

            def make_sys_cb(c=cmd, l=label, d=danger):
                def cb(_):
                    if d:
                        self._ask_confirm(l, f"Execute {l}?", lambda: (executar(c), self.cmd_output_label.set_text(f"> {l} sent")))
                    else:
                        executar(c)
                        self.cmd_output_label.set_text(f"> {l} sent")
                return cb

            btn.connect("clicked", make_sys_cb())
            sys_grid.append(btn)
        sys_card.append(sys_grid)
        box.append(sys_card)

        self.cmd_output_label.set_text("")
        box.append(self.cmd_output_label)
        return self._scroll(box)

    # ── HUB (Chat / Tasks / AI) ──

    def _pagina_hub(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        tab_bar = Gtk.Box(spacing=8)
        self.hub_sub_tabs = {}
        self.hub_pages = Gtk.Stack()
        self.hub_pages.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        pages = [
            ("chat", "CHAT", "chat"),
            ("galeria", "GALERIA", "media"),
        ]
        for key, label, icon_name in pages:
            btn = Gtk.Button()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            img = self._icone_svg(icon_name, 16)
            box.append(img)
            lbl = Gtk.Label(label=label)
            box.append(lbl)
            btn.set_child(box)
            btn.add_css_class("spook-tab" if key != "chat" else "spook-tab-on")
            self.hub_sub_tabs[key] = btn
            tab_bar.append(btn)

        self.hub_pages.add_named(self._hub_chat_page(), "chat")
        self.hub_pages.add_named(self._hub_galeria_page(), "galeria")

        def switch_tab(key):
            def cb(_):
                for k, b in self.hub_sub_tabs.items():
                    b.remove_css_class("spook-tab-on")
                    b.add_css_class("spook-tab" if k != key else "spook-tab-on")
                self.hub_pages.set_visible_child_name(key)
            return cb

        for key in pages:
            self.hub_sub_tabs[key[0]].connect("clicked", switch_tab(key[0]))

        vbox.append(tab_bar)
        vbox.append(self.hub_pages)
        return vbox

    def _hub_chat_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        hdr = Gtk.Box(spacing=8)
        titulo = Gtk.Label(label="Arch ↔ Phone", xalign=0)
        titulo.add_css_class("spook-card-title")
        titulo.set_hexpand(True)
        hdr.append(titulo)
        hdr.append(self.chat_status_label)
        box.append(hdr)
        chat_scroll = Gtk.ScrolledWindow()
        chat_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        chat_scroll.set_vexpand(True)
        chat_scroll.set_child(self.chat_box)
        box.append(chat_scroll)
        self.chat_scroll = chat_scroll
        input_row = Gtk.Box(spacing=4)
        self.chat_entry = Gtk.Entry()
        self.chat_entry.add_css_class("spook-input")
        self.chat_entry.set_hexpand(True)
        self.chat_entry.set_placeholder_text("Message...")
        self.chat_entry.connect("activate", lambda e: self._enviar_chat(e))
        file_btn = Gtk.Button(label="\ue226")
        file_btn.add_css_class("spook-btn")
        file_btn.add_css_class("spook-msi-btn")
        file_btn.set_tooltip_text("Attach file")
        file_btn.connect("clicked", lambda *_: self._escolher_arquivo(self._upload_chat))
        send_btn = Gtk.Button(label="\ue163")
        send_btn.add_css_class("spook-btn")
        send_btn.add_css_class("spook-msi-btn")
        send_btn.add_css_class("spook-msi-send")
        send_btn.set_tooltip_text("Send")
        send_btn.connect("clicked", lambda *_: self._enviar_chat(self.chat_entry))
        input_row.append(file_btn)
        input_row.append(self.chat_entry)
        input_row.append(send_btn)
        box.append(input_row)
        return box

    def _hub_galeria_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.galeria_items = []
        self.galeria_filtro = "all"
        self.galeria_filter_btns = {}
        hdr = Gtk.Box(spacing=8)
        lb = Gtk.Label(label="GALLERY", xalign=0)
        lb.add_css_class("spook-card-title")
        hdr.append(lb)
        self.galeria_count_label = Gtk.Label(label="0", xalign=0)
        self.galeria_count_label.add_css_class("spook-gal-badge")
        self.galeria_count_label.add_css_class("spook-gal-badge-all")
        hdr.append(self.galeria_count_label)
        refresh_btn = Gtk.Button(label="REFRESH")
        refresh_btn.add_css_class("spook-btn")
        refresh_btn.connect("clicked", lambda *_: self._carregar_galeria())
        hdr.append(refresh_btn)
        box.append(hdr)

        filtros = [
            ("all", "TODOS"), ("image", "IMG"), ("video", "VÍDEO"),
            ("audio", "ÁUDIO"), ("file", "ARQ"),
        ]
        filtro_row = Gtk.Box(spacing=6)
        for chave, rotulo in filtros:
            b = Gtk.Button(label=rotulo)
            b.add_css_class("spook-gal-filter")
            b.add_css_class(f"spook-gal-filter-{chave}")
            b.connect("clicked", lambda _b, c=chave: self._filtrar_galeria(c))
            self.galeria_filter_btns[chave] = b
            filtro_row.append(b)
        box.append(filtro_row)
        self._atualizar_botoes_filtro()

        self.galeria_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        galeria_scroll = Gtk.ScrolledWindow()
        galeria_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        galeria_scroll.set_vexpand(True)
        galeria_scroll.set_child(self.galeria_box)
        box.append(galeria_scroll)

        self._carregar_galeria()
        return box

    def _carregar_galeria(self):
        def _fazer():
            try:
                import urllib.request, json, ssl
                tok = None
                tf = Path.home() / ".pentestlink" / "token_sessao.txt"
                if tf.exists():
                    tok = tf.read_text().strip() or None
                url = f"{SOCKET_URL}/media"
                req = urllib.request.Request(url)
                if tok:
                    req.add_header("Authorization", f"Bearer {tok}")
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
                    if r.status == 200:
                        data = json.loads(r.read().decode())
                        items = data.get("items", [])
                        GLib.idle_add(lambda: self._popular_galeria(items))
                    else:
                        GLib.idle_add(lambda: self._popular_galeria([]))
            except Exception:
                GLib.idle_add(lambda: self._popular_galeria([]))
        self._bg(_fazer)

    def _popular_galeria(self, items):
        self.galeria_items = items
        if hasattr(self, "galeria_count_label"):
            self.galeria_count_label.set_text(str(len(items)))
        self._render_galeria()

    def _filtrar_galeria(self, chave):
        self.galeria_filtro = chave
        self._atualizar_botoes_filtro()
        self._render_galeria()

    def _atualizar_botoes_filtro(self):
        for chave, btn in getattr(self, "galeria_filter_btns", {}).items():
            cls = f"spook-gal-filter-{chave}-on"
            if chave == getattr(self, "galeria_filtro", "all"):
                btn.add_css_class(cls)
            else:
                btn.remove_css_class(cls)

    def _render_galeria(self):
        self._clear(self.galeria_box)
        items = getattr(self, "galeria_items", [])
        filtro = getattr(self, "galeria_filtro", "all")
        _icone = {"image": "\ue3f4", "video": "\ue04d", "audio": "\ue04f", "file": "\ue8e7"}
        _rotulo = {"image": "IMG", "video": "VÍDEO", "audio": "ÁUDIO", "file": "ARQ"}
        visiveis = 0
        for item in items:
            nome = item.get("name", "")
            bytes_ = item.get("bytes", 0)
            ext = nome.rsplit(".", 1)[-1].lower() if "." in nome else ""
            tipo = "file"
            if ext in ("png","jpg","jpeg","webp","gif","bmp","heic","heif","avif"):
                tipo = "image"
            elif ext in ("mp4","webm","mkv","mov","m4v"):
                tipo = "video"
            elif ext in ("mp3","m4a","ogg","wav","aac","opus"):
                tipo = "audio"
            if filtro != "all" and tipo != filtro:
                continue
            visiveis += 1
            card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            card.add_css_class("spook-card")
            card.add_css_class(f"spook-gal-{tipo}")
            card.set_margin_top(4)
            card.set_margin_bottom(4)
            ic = Gtk.Label(label=_icone[tipo])
            ic.add_css_class("spook-msi-btn")
            ic.add_css_class(f"spook-gal-ico-{tipo}")
            card.append(ic)
            info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            info.set_hexpand(True)
            lbl_nome = Gtk.Label(label=nome, xalign=0)
            lbl_nome.add_css_class("spook-msg-txt")
            lbl_nome.set_ellipsize(Pango.EllipsizeMode.END)
            info.append(lbl_nome)
            meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            badge = Gtk.Label(label=_rotulo[tipo], xalign=0)
            badge.add_css_class("spook-gal-badge")
            badge.add_css_class(f"spook-gal-badge-{tipo}")
            meta.append(badge)
            lbl_sz = Gtk.Label(label=_fmt_bytes(bytes_), xalign=0)
            lbl_sz.add_css_class("spook-note")
            meta.append(lbl_sz)
            info.append(meta)
            card.append(info)
            btn = Gtk.Button(label="\u2197")
            btn.add_css_class("spook-btn")
            btn.add_css_class("spook-msi-btn")
            btn.connect("clicked", lambda _, n=nome, t=tipo: self._abrir_galeria_item(n, t))
            card.append(btn)
            self.galeria_box.append(card)
        if visiveis == 0:
            lbl = Gtk.Label(
                label="No files in the gallery" if not items else "No files in this category",
                xalign=0,
            )
            lbl.add_css_class("spook-note")
            self.galeria_box.append(lbl)

    def _abrir_galeria_item(self, nome, tipo):
        def _fazer():
            path = self._baixar_midia_http(nome) or hub_store.baixar_midia(nome)
            if path:
                executar(["gio", "open", path])
        self._bg(_fazer)

    def _hub_ai_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        hdr = Gtk.Box(spacing=8)
        lb = Gtk.Label(label="SPOOK AI", xalign=0)
        lb.add_css_class("spook-card-title")
        hdr.append(lb)
        clear_btn = Gtk.Button(label="CLEAR")
        clear_btn.add_css_class("spook-err-btn")
        clear_btn.connect("clicked", lambda *_: (self._clear(self.ai_chat_box), setattr(self, 'ai_messages', [])))
        hdr.append(clear_btn)
        box.append(hdr)
        self.ai_messages = []
        ai_scroll = Gtk.ScrolledWindow()
        ai_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        ai_scroll.set_vexpand(True)
        ai_scroll.set_child(self.ai_chat_box)
        box.append(ai_scroll)
        input_row = Gtk.Box(spacing=4)
        self.ai_entry = Gtk.Entry()
        self.ai_entry.add_css_class("spook-input")
        self.ai_entry.set_hexpand(True)
        self.ai_entry.set_placeholder_text("Ask SPOOK...")
        self.ai_entry.connect("activate", lambda e: self._enviar_ai(e))
        send_btn = Gtk.Button(label="SEND")
        send_btn.add_css_class("spook-accent")
        send_btn.connect("clicked", lambda *_: self._enviar_ai(self.ai_entry))
        input_row.append(self.ai_entry)
        input_row.append(send_btn)
        box.append(input_row)
        return box

    def _enviar_chat(self, entry):
        texto = entry.get_text().strip()
        if not texto:
            return
        entry.set_text("")
        if self._socket_online:
            self.sio.emit("chat_enviar", {"id": str(uuid.uuid4()), "texto": texto, "from": "arch"})
        else:
            self._bg(lambda: (hub_store.enviar_texto(texto), self._refresh_chat()))

    def _upload_chat(self, path):
        if getattr(self, "_enviando_arquivo", False):
            self._flash_chat("ENVIANDO... AGUARDE")
            return
        self._enviando_arquivo = True
        nome = path.rsplit("/", 1)[-1]
        GLib.idle_add(lambda: self._flash_chat(f"SENDING {nome}..."))

        def _env():
            try:
                if self._socket_online:
                    self._upload_relay(path, nome)
                else:
                    hub_store.enviar_arquivo(path, tipo="arquivo")
            finally:
                self._enviando_arquivo = False
                GLib.idle_add(lambda: (self._flash_chat(f"FILE SENT ✓ {nome}"), self._refresh_chat()))

        self._bg(_env)

    def _upload_relay(self, path, nome):
        token = None
        try:
            arquivo_token = Path.home() / ".pentestlink" / "token_sessao.txt"
            if arquivo_token.exists():
                token = arquivo_token.read_text().strip() or None
        except Exception:
            token = None
        boundary = uuid.uuid4().hex
        with open(path, "rb") as f:
            dados = f.read()
        corpo = b""
        corpo += f'--{boundary}\r\nContent-Disposition: form-data; name="id"\r\n\r\n{str(uuid.uuid4())}\r\n'.encode()
        corpo += f'--{boundary}\r\nContent-Disposition: form-data; name="from"\r\n\r\narch\r\n'.encode()
        corpo += f'--{boundary}\r\nContent-Disposition: form-data; name="tipo"\r\n\r\narquivo\r\n'.encode()
        corpo += f'--{boundary}\r\nContent-Disposition: form-data; name="texto"\r\n\r\n\r\n'.encode()
        corpo += f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{nome}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
        corpo += dados
        corpo += f'\r\n--{boundary}--\r\n'.encode()
        req = urllib.request.Request(
            SOCKET_URL + "/upload",
            data=corpo,
            method="POST",
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Authorization": f"Bearer {token}" if token else token or "",
            },
        )
        with urllib.request.urlopen(req, timeout=90) as r:
            r.read()

    def _refresh_chat(self, fonte_espelho=None, force_scroll=False):
        # Reconciliar o cache vivo com o espelho local (fonte de verdade p/ msgs
        # que chegaram enquanto estava offline). Nao apaga o que o socket ja viu.
        if not self._socket_online:
            if fonte_espelho is None:
                fonte_espelho = hub_store.listar_mensagens()
            for m in fonte_espelho:
                if m.get("id") and m["id"] not in self._chat_msgs:
                    self._chat_msgs[m["id"]] = m
        ordem = sorted(self._chat_msgs.values(), key=lambda m: m.get("ts", 0))
        ids_pendentes = hub_store.pendentes_ids()
        self._chat_last_merge = "p:{}".format(len(ordem))
        GLib.idle_add(lambda: self._fill_chat(ordem, ids_pendentes, force_scroll))

    def _merge_chat_espelho(self):
        """Reconciliação periódica espelho -> cache vivo. Quando o socket do relay
        está online, o cache vivo É a fonte de verdade (chat_lista/chat_nova/chat_apagada)
        — reconciliação com o espelho local (Drive) só roda offline, senão removeria
        msgs válidas que o relay mandou e o mirror local (morto) não possui."""
        if self._socket_online:
            return True
        try:
            ids_pend = hub_store.pendentes_ids()
            espelho = {m["id"]: m for m in hub_store.listar_mensagens() if m.get("id")}
            mudou = False
            agora = time.time()
            for mid, m in espelho.items():
                if mid not in self._chat_msgs:
                    self._chat_msgs[mid] = m
                    mudou = True
            for mid in list(self._chat_msgs.keys()):
                if mid in espelho:
                    continue
                if mid in ids_pend:
                    continue
                ts = (self._chat_msgs[mid].get("ts") or 0) / 1000
                if agora - ts < 30:
                    continue
                del self._chat_msgs[mid]
                mudou = True
            if mudou:
                fonte = sorted(self._chat_msgs.values(), key=lambda m: m.get("ts", 0))
                GLib.idle_add(lambda f=fonte: self._fill_chat(f, ids_pend))
        except Exception:
            pass
        return True

    @staticmethod
    def _tint_button(btn, classe):
        """GTK4: CSS `color` no nó do Button NÃO tinge o label interno (o tema
        o pinta). Aplica a classe também no child (Gtk.Label) — só assim as cores
        do app (copiar rosa / lixeira vermelha) aparecem de verdade."""
        child = btn.get_child()
        if child is not None:
            child.add_css_class(classe)

    def _rodape_msg(self, msg, pend=False):
        """Rodape da mensagem estilo Android: icones copiar/lixeira DENTRO da
        bolha, alinhados a direita. Ficam 16px longe da borda => nunca debaixo
        da scrollbar (o botao antigo ficava no fim da linha, sob o scrollbar,
        e o clique rolava a conversa em vez de apagar)."""
        texto = msg.get("texto", "")
        rodape = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        rodape.set_halign(Gtk.Align.END)
        if pend:
            p = Gtk.Label(label="\u23f3")
            p.add_css_class("spook-note")
            p.set_tooltip_text("sending...")
            rodape.append(p)
        if texto:
            copy_btn = Gtk.Button(label="\ue14d")
            copy_btn.add_css_class("spook-btn")
            copy_btn.add_css_class("spook-msi-btn")
            copy_btn.add_css_class("spook-msi-copy")
            self._tint_button(copy_btn, "spook-msi-copy")
            copy_btn.set_tooltip_text("Copy text")
            copy_btn.connect("clicked", lambda _, t=texto: self._copiar_texto(t) if t else None)
            rodape.append(copy_btn)
        del_btn = Gtk.Button(label="\ue92e")
        del_btn.add_css_class("spook-btn")
        del_btn.add_css_class("spook-msi-btn")
        del_btn.add_css_class("spook-msi-del")
        self._tint_button(del_btn, "spook-msi-del")
        del_btn.set_tooltip_text("Delete")
        del_btn.connect("clicked", lambda _, m=msg: self._bg(lambda: self._apagar_msg_thread(m)))
        rodape.append(del_btn)
        return rodape

    def _render_msg(self, msg, pend=False):
        """Roda fora da thread principal? NAO: chamado do idle. Monta o widget de 1 msg."""
        from_raw = (msg.get('from') or msg.get('autor') or '').lower()
        quem = 'POCO' if from_raw == 'android' else 'ARCH' if from_raw == 'arch' else (msg.get('from') or msg.get('autor') or '?').upper()
        texto = msg.get("texto", "")
        arq = msg.get("arquivo", "")
        ts = hora(msg.get("ts", 0))
        tipo = (msg.get("tipo") or "texto").lower()
        ext = (arq or "").rsplit(".", 1)[-1].lower() if arq else ""

        bolha = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        if quem == 'ARCH':
            bolha.add_css_class("spook-msg-arch")
        elif quem == 'POCO':
            bolha.add_css_class("spook-msg-phone")
        else:
            bolha.add_css_class("spook-msg-arch")
        bolha.set_hexpand(True)
        cab = Gtk.Label(label=f"{quem} · {ts}", xalign=0)
        cab.add_css_class("spook-note")
        cab.add_css_class("spook-msg-h")
        if quem == 'ARCH':
            cab.add_css_class("spook-msg-h-arch")
        elif quem == 'POCO':
            cab.add_css_class("spook-msg-h-phone")
        else:
            cab.add_css_class("spook-msg-h-arch")
        bolha.append(cab)
        if texto:
            lab = Gtk.Label(label=texto, xalign=0, wrap=True)
            lab.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            lab.add_css_class("spook-msg-txt")
            bolha.append(lab)
        if arq:
            nome = arq.rsplit("/", 1)[-1]
            t_audio = tipo in ("audio", "audio_record", "sound")
            t_video = tipo in ("video", "video_record")
            t_imagem = tipo in ("image", "imagem", "foto", "photo", "picture", "imagens")
            if t_audio or t_video or ext in ("mp3", "m4a", "ogg", "opus", "wav", "aac", "webm", "mp4", "mkv", "mov"):
                sub = "audio" if t_audio else "video" if t_video else ("audio" if ext in ("mp3", "m4a", "ogg", "opus", "wav", "aac") else "video")
                bolha.append(self._player_midia(nome, sub))
            elif t_imagem or ext in ("png", "jpg", "jpeg", "gif", "webp", "bmp", "heic", "heif", "avif"):
                bolha.append(self._imagem_midia(nome))
            else:
                bot = Gtk.Button(label=f"\u2197 {nome}")
                bot.add_css_class("spook-btn")
                bot.add_css_class("spook-btn-text-sm")
                bot.connect("clicked", lambda *_a: self._bg(lambda: self._abrir_midia_baixada(nome)))
                bolha.append(bot)
        bolha.append(self._rodape_msg(msg, pend))
        return bolha

    def _player_midia(self, nome, tipo):
        caixa = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        caixa.add_css_class("spook-card")
        carg = Gtk.Label(label="download...", xalign=0)
        carg.add_css_class("spook-note")
        caixa.append(carg)

        def _carregar():
            path = self._baixar_midia_http(nome) or hub_store.baixar_midia(nome)
            if not path:
                GLib.idle_add(lambda: (carg.set_text("media unavailable"), False)[0] if False else carg.set_text("media unavailable"))
                return
            GLib.idle_add(lambda: self._monta_player(caixa, path, tipo, carg, nome))

        self._bg(_carregar)
        return caixa

    def _monta_player(self, caixa, path, tipo, carg, nome):
        try:
            mfile = Gtk.MediaFile.new_for_filename(path)
            if tipo == "video":
                video = Gtk.Video()
                video.set_media_stream(mfile)
                video.set_size_request(440, 280)
                caixa.append(video)
            controles = Gtk.MediaControls()
            controles.set_media_stream(mfile)
            controles.set_size_request(440, 40)
            caixa.append(controles)
            abrir = Gtk.Button(label="\u2197 Open externally")
            abrir.add_css_class("spook-loc")
            abrir.connect("clicked", lambda *_a: executar(["gio", "open", path]))
            caixa.append(abrir)
            caixa.remove(carg)
        except Exception as e:
            carg.set_text(f"player: {e}")

    def _imagem_midia(self, nome):
        caixa = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        caixa.add_css_class("spook-card")
        carg = Gtk.Label(label="download...", xalign=0)
        carg.add_css_class("spook-note")
        caixa.append(carg)

        def _abrir():
            path = self._baixar_midia_http(nome) or hub_store.baixar_midia(nome)
            if path:
                executar(["gio", "open", path])

        evt = Gtk.GestureClick()
        evt.connect("pressed", lambda *_a: self._bg(_abrir))
        caixa.add_controller(evt)
        caixa.set_tooltip_text("Open image")
        caixa.set_cursor_from_name("pointer")

        def _carregar():
            path = self._baixar_midia_http(nome) or hub_store.baixar_midia(nome)
            if not path:
                GLib.idle_add(lambda: carg.set_text("media unavailable"))
                return
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 440, 440, True, GdkPixbuf.InterpType.BILINEAR)
                img = Gtk.Image.new_from_pixbuf(pb)
                GLib.idle_add(lambda: (caixa.remove(carg), caixa.append(img)))
            except Exception:
                GLib.idle_add(lambda: carg.set_text("image unavailable"))

        self._bg(_carregar)
        return caixa

    def _nome_app(self, pacote):
        """com.spotify.music -> MUSIC (o pacote cru no popup fica feio)."""
        try:
            return str(pacote or "celular").split(".")[-1].upper()
        except Exception:
            return "PHONE"

    def _mostrar_notificacao(self, app, titulo, texto):
        """Popup da notificacao do celular, no mesmo visual do app Android."""
        try:
            popup = Gtk.Window()
            popup.set_title("SPOOK")
            popup.set_default_size(340, 118)
            popup.set_resizable(False)
            popup.add_css_class("spook")
            popup.set_transient_for(self.janela)
            popup.set_hide_on_close(True)

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            box.add_css_class("spook-root")
            box.set_margin_top(14)
            box.set_margin_bottom(14)
            box.set_margin_start(16)
            box.set_margin_end(16)

            hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            icone = Gtk.Label(label="\ue7f4")          # sino dos Material Symbols
            icone.add_css_class("spook-msi")
            icone.add_css_class("spook-msi-btn")
            hdr.append(icone)

            lbl_app = Gtk.Label(label=self._nome_app(app), xalign=0)
            lbl_app.add_css_class("spook-card-title")
            lbl_app.set_hexpand(True)
            hdr.append(lbl_app)

            btn_fechar = Gtk.Button(label="\ue92e")
            btn_fechar.add_css_class("spook-btn")
            btn_fechar.add_css_class("spook-msi-btn")
            btn_fechar.add_css_class("spook-msi-del")
            btn_fechar.connect("clicked", lambda _: popup.close())
            hdr.append(btn_fechar)
            box.append(hdr)

            if titulo:
                lbl_tit = Gtk.Label(label=titulo, xalign=0, wrap=True)
                lbl_tit.add_css_class("spook-msg-txt")
                lbl_tit.set_max_width_chars(36)
                box.append(lbl_tit)

            if texto:
                lbl_txt = Gtk.Label(label=texto, xalign=0, wrap=True)
                lbl_txt.add_css_class("spook-note")
                lbl_txt.set_max_width_chars(36)
                box.append(lbl_txt)

            popup.set_child(box)
            popup.present()

            # canto superior direito, empilhando cada notificacao nova
            self._popups_abertos = [p for p in getattr(self, "_popups_abertos", []) if p.get_visible()]
            fila = len(self._popups_abertos)
            self._popups_abertos.append(popup)
            GLib.idle_add(self._posicionar_popup, popup, fila)
            GLib.timeout_add_seconds(8, lambda: (popup.close(), False)[1])
        except Exception as e:
            print(f"[OVERLAY] popup falhou: {e}", flush=True)

    def _posicionar_popup(self, popup, fila):
        try:
            display = self.janela.get_display()
            monitor = display.get_monitors().get_item(0)
            g = monitor.get_geometry()
            popup.set_position(Gtk.WindowPosition.NONE)
            popup.move(g.x + g.width - 372, g.y + 48 + fila * 132)
        except Exception:
            pass
        return False

    def _fill_chat(self, msgs, ids_pendentes=(), force_scroll=False):
        # evita redraw desnecessario (era isso q fazia a tela "mexer" e re-baixar midia)
        fp = "{}|{}".format(
            "|".join("{}:{}:{}".format(m.get("id", ""), m.get("ts", 0), (m.get("texto") or "")[:24]) for m in msgs[-60:]),
            "|".join(sorted(ids_pendentes)),
        )
        # Se force_scroll=True, sempre redesenha e scrolla (nova mensagem remota)
        if not force_scroll and fp == self._chat_last_draw:
            return
        self._chat_last_draw = fp
        self._clear(self.chat_box)
        for msg in msgs[-60:]:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            row.append(self._render_msg(msg, msg.get("id") in ids_pendentes))
            self.chat_box.append(row)
        self._scroll_chat_fim(force=force_scroll or bool(ids_pendentes))

    def _scroll_chat_fim(self, force=False):
        s = getattr(self, "chat_scroll", None)
        if s is None:
            return
        v0 = s.get_vadjustment()
        if v0 is None:
            return
        # Decide AGORA, com a geometria ANTES da mensagem nova entrar. Se isso
        # for calculado no _aplicar (depois do relayout) o "perto do fim" ja'
        # virou falso para qualquer mensagem maior que 60 px -> o chat ficava
        # parado na mensagem velha.
        perto_do_fim = v0.get_value() >= v0.get_upper() - v0.get_page_size() - 60

        def _aplicar():
            v = s.get_vadjustment()
            if v is None:
                return False
            if force or perto_do_fim:
                v.set_value(v.get_upper())
            return False

        # A altura nova do conteudo so existe DEPOIS que o GTK realoca os
        # widgets. Setar o adjustment na mesma hora usava o "upper" antigo e
        # nao rolava nada (o chat do overlay ficava parado na mensagem velha).
        GLib.idle_add(_aplicar)
        GLib.timeout_add(60, _aplicar)
        GLib.timeout_add(250, _aplicar)
        if force:
            GLib.timeout_add(600, _aplicar)
            GLib.timeout_add(1200, _aplicar)

    def _copiar_texto(self, texto):
        disp = Gdk.Display.get_default()
        if disp is not None:
            try:
                disp.get_clipboard().set_text(texto)
                self._flash_chat("COPIED ✓")
                return
            except Exception:
                pass
        def _fallback():
            ok = False
            try:
                p = subprocess.run(["wl-copy"], input=texto.encode(), capture_output=True, timeout=5)
                ok = p.returncode == 0
            except Exception:
                pass
            GLib.idle_add(lambda: self._flash_chat("COPIED ✓" if ok else "COPY FAILED"))
        self._bg(_fallback)

    def _apagar_msg_thread(self, m):
        def _apagar():
            if self._socket_online:
                self.sio.emit("chat_apagar", {"id": m.get("id", "")})
                ok = True
            else:
                ok = hub_store.apagar_mensagem(m.get("id", ""))
            GLib.idle_add(lambda: (self._refresh_chat(), self._flash_chat("DELETE ✓" if ok else "DELETE FAILED")))
        self._bg(_apagar)

    def _init_socketio(self):
        try:
            import socketio
            token = None
            try:
                arquivo_token = Path.home() / ".pentestlink" / "token_sessao.txt"
                if arquivo_token.exists():
                    token = arquivo_token.read_text().strip() or None
            except Exception:
                token = None
            self.sio = socketio.Client(reconnection=True, reconnection_attempts=10, reconnection_delay=2)
            auth = {"token": token, "device": "overlay"} if token else None
            self.sio.connect(SOCKET_URL, auth=auth, wait_timeout=10)

            @self.sio.on("chat_nova")
            def on_chat_nova(msg):
                if isinstance(msg, dict) and msg.get("id"):
                    self._chat_msgs[msg["id"]] = msg
                # Nova mensagem remota recebida: sempre rola pro fim (igual ao app Android)
                self._bg(lambda: self._refresh_chat(force_scroll=True))

            @self.sio.on("chat_lista")
            def on_chat_lista(d):
                msgs = (d or {}).get("mensagens", [])
                for m in msgs:
                    if isinstance(m, dict) and m.get("id"):
                        self._chat_msgs[m["id"]] = m
                self._bg(self._refresh_chat)

            @self.sio.on("chat_apagada")
            def on_chat_apagada(msg):
                if isinstance(msg, dict) and msg.get("id"):
                    self._chat_msgs.pop(msg["id"], None)
                self._bg(self._refresh_chat)

            @self.sio.on("task_lista")
            def on_task_lista(d):
                tasks = (d or {}).get("tasks", [])
                self._tasks_cache = {
                    t.get("id"): t for t in tasks
                    if isinstance(t, dict) and t.get("id")
                }
                self.pad_mgr.aplicar_tarefas(list(self._tasks_cache.values()))

            @self.sio.on("task_nova")
            def on_task_nova(msg):
                if isinstance(msg, dict) and msg.get("id"):
                    self._tasks_cache[msg["id"]] = msg
                self.pad_mgr.aplicar_tarefas(list(self._tasks_cache.values()))

            @self.sio.on("task_atualizada")
            def on_task_atualizada(msg):
                if isinstance(msg, dict) and msg.get("id"):
                    self._tasks_cache[msg["id"]] = msg
                self.pad_mgr.aplicar_tarefas(list(self._tasks_cache.values()))

            @self.sio.on("task_apagada")
            def on_task_apagada(msg):
                if isinstance(msg, dict) and msg.get("id"):
                    self._tasks_cache.pop(msg["id"], None)
                self.pad_mgr.aplicar_tarefas(list(self._tasks_cache.values()))

            @self.sio.on("notificacao_nova")
            def on_notificacao_nova(dados):
                if isinstance(dados, dict):
                    app = dados.get("app", "celular")
                    titulo = dados.get("titulo", "")
                    texto = dados.get("texto", "")
                    print(f"[OVERLAY] notificacao_nova: {app} | {titulo} | {texto}", flush=True)
                    # Entrega somente ao centro nativo de notificações do ii.
                    try:
                        subprocess.Popen(
                            ["notify-send", "-a", "spook", "-u", "normal",
                             "-h", "string:x-canonical-private-synchronous:false",
                             self._nome_app(app), (titulo or "") + (
                                 "\n" + texto if texto else "")],
                            env=dict(os.environ),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        )
                    except Exception as e:
                        print(f"[OVERLAY] notify-send falhou: {e}", flush=True)

            @self.sio.on("celular_presenca")
            def on_celular_presenca(dados):
                self._presenca_celular = bool((dados or {}).get("online"))
                GLib.idle_add(lambda: self._atualizar_status(self._presenca_celular))

            self._socket_online = True
            self._bg(self._puxar_lists)
        except Exception:
            self.sio = None
            self._socket_online = False

    def _atualizar_status(self, celular_online=False):
        """Cabecalho no mesmo espirito do app: SPOOK // <estado> em mono."""
        if self.status_label is None:
            return
        if not self._socket_online:
            texto, cor = "SPOOK // OFFLINE", "spook-err"
        elif celular_online or self._presenca_celular:
            texto, cor = "SPOOK // ONLINE", "spook-ok"
        else:
            texto, cor = "SPOOK // PHONE OFF", "spook-warn"
        self.status_label.set_text(texto)
        for classe in ("spook-ok", "spook-warn", "spook-err"):
            self.status_label.remove_css_class(classe)
        self.status_label.add_css_class(cor)

    def _flash_chat(self, msg):
        self.chat_status_label.set_text(msg)
        GLib.timeout_add_seconds(3, lambda: (self.chat_status_label.set_text(""), False))

    def _puxar_lists(self):
        try:
            if self.sio and getattr(self.sio, "connected", False):
                self.sio.emit("chat_listar", {})
                self.sio.emit("task_listar", {})
        except Exception:
            pass

    # ── TAREFAS (dentro do PAD) ──
    def _tarefas_pedir(self):
        try:
            if self.sio and getattr(self.sio, "connected", False):
                self.sio.emit("task_listar", {})
                return
        except Exception:
            pass
        self._tarefas_do_hub()

    def _tarefas_do_hub(self):
        def _ler():
            try:
                tasks = hub_store.listar_tasks()
            except Exception:
                tasks = []
            GLib.idle_add(lambda: (self.pad_mgr.aplicar_tarefas(tasks), False)[1])
        self._bg(_ler)

    def _tarefa_adicionar(self, titulo):
        try:
            if self.sio and getattr(self.sio, "connected", False):
                self.sio.emit("task_nova", {"id": str(uuid.uuid4()), "titulo": titulo})
                return
        except Exception:
            pass
        self._bg(lambda: (hub_store.criar_task(titulo), self._tarefas_do_hub()))

    def _tarefa_marcar(self, task_id, feito):
        try:
            if self.sio and getattr(self.sio, "connected", False):
                self.sio.emit("task_atualizada", {"id": task_id, "feito": bool(feito)})
                return
        except Exception:
            pass
        self._bg(lambda: (hub_store.marcar_task(task_id, bool(feito)), self._tarefas_do_hub()))

    def _tarefa_apagar(self, task_id):
        try:
            if self.sio and getattr(self.sio, "connected", False):
                self.sio.emit("task_apagada", {"id": task_id})
                return
        except Exception:
            pass
        self._bg(lambda: (hub_store.apagar_task(task_id), self._tarefas_do_hub()))

    def _enviar_ai(self, entry):
        texto = entry.get_text().strip()
        if not texto or self.ai_loading:
            return
        entry.set_text("")
        self.ai_messages.append({"role": "user", "text": texto})
        self._fill_ai()
        self.ai_loading = True
        self._bg(lambda: self._send_ai(texto))

    def _send_ai(self, pergunta):
        try:
            import socketio
            sio = socketio.Client(timeout=60, reconnection=False)
            result = [False, ""]

            @sio.on("chat_ia_resposta", namespace="/ia")
            def on_resp(data):
                result[0] = data.get("ok", False)
                result[1] = data.get("resposta", "")
                if data.get("sessao_id"):
                    self.ai_session_id = data["sessao_id"]
                sio.disconnect()

            sio.connect(AI_URL, wait_timeout=10)
            envio = {"pergunta": pergunta}
            if self.ai_session_id:
                envio["sessao_id"] = self.ai_session_id
            sio.emit("chat_ia_perguntar", envio, namespace="/ia")
            sio.wait(timeout=60)
            self.ai_messages.append({"role": "ai", "text": result[1] if result[0] else f"Error: {result[1]}"})
        except ImportError:
            import urllib.request
            req = urllib.request.Request(AI_URL.replace("socket.io", ""), data=b"{}")
            self.ai_messages.append({"role": "ai", "text": "Install python-socketio for AI: pip install python-socketio[client]"})
        except Exception as e:
            self.ai_messages.append({"role": "ai", "text": f"Error: {str(e)[:100]}"})
        finally:
            self.ai_loading = False
            GLib.idle_add(self._fill_ai)

    def _fill_ai(self):
        self._clear(self.ai_chat_box)
        for msg in self.ai_messages:
            is_user = msg["role"] == "user"
            line = f"{'YOU' if is_user else 'SPOOK'}\n{msg['text']}"
            lab = Gtk.Label(label=line, xalign=0, wrap=True)
            lab.add_css_class("spook-msg-phone" if is_user else "spook-msg-arch")
            self.ai_chat_box.append(lab)
        if self.ai_loading:
            lab = Gtk.Label(label="SPOOK is thinking...", xalign=0)
            lab.add_css_class("spook-ok")
            self.ai_chat_box.append(lab)

    # ── NOTES ──

    # ── LOCATION ──

    def _pagina_local(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        hdr = Gtk.Box(spacing=8)
        lbl = Gtk.Label(label="DEVICE LOCATION", xalign=0)
        lbl.add_css_class("spook-title")
        lbl.set_hexpand(True)
        hdr.append(lbl)
        refresh = Gtk.Button(label="REFRESH")
        refresh.add_css_class("spook-btn")
        refresh.connect("clicked", lambda *_: (self._atualizar_local(), self._bg(self._update_local_arch)))
        hdr.append(refresh)
        box.append(hdr)
        desc = Gtk.Label(
            label="Location shared via Drive. Send live GPS from the phone (SPOOK app) ",
            xalign=0, wrap=True,
        )
        desc.add_css_class("spook-note")
        box.append(desc)
        box.append(self.local_label)
        return self._scroll(box)

    def _atualizar_local(self):
        # Lê do Drive (rclone, rede) em thread de fundo; renderiza na main via idle.
        def _ler_e_render():
            try:
                import localizacao
                dados = localizacao.estado()
            except Exception:
                dados = {}
            GLib.idle_add(lambda: self._montar_local(dados))
        self._bg(_ler_e_render)

    def _montar_local(self, dados):
        self._dados_local = dados
        arq = dados.get("arch") or {}
        cel = dados.get("celular") or {}

        # Phone GPS (precisão ~14m) é o canônico. Arch e celular ficam juntos,
        # então a posição mostrada vem do GPS do celular, não da geolocalização
        # por IP do Arch (que pode errar dezenas de km).
        if cel.get("lat") is not None and cel.get("lon") is not None:
            cidade = cel.get("cidade") or arq.get("cidade") or ""
            self.home_arch_label.set_text(
                f"ARCH   {cidade}\n"
                f"       {cel.get('lat')},{cel.get('lon')}   [{cel.get('hora', '')}]"
            )
            self.home_map_arch.set_sensitive(True)
        elif arq.get("lat") and arq.get("lon"):
            self.home_arch_label.set_text(
                f"ARCH   {arq.get('cidade', '')} · {arq.get('regiao', '')} · {arq.get('pais', '')}\n"
                f"       {arq.get('lat')},{arq.get('lon')}   [{arq.get('hora', '')}]"
            )
            self.home_map_arch.set_sensitive(True)
        else:
            self.home_arch_label.set_text("ARCH   no location yet — press REFRESH")
            self.home_map_arch.set_sensitive(False)

        if cel.get("lat") is not None:
            self.home_cel_label.set_text(
                f"ANDROID  {cel.get('lat')},{cel.get('lon')}"
                f"  [precisão {cel.get('precisao', '--')} m · {cel.get('provedor', '')}]"
                f"\n       [{cel.get('hora', '')}]"
            )
            self.home_map_cel.set_sensitive(True)
        else:
            self.home_cel_label.set_text("ANDROID  no GPS yet — open the SPOOK app on the phone")
            self.home_map_cel.set_sensitive(False)

        self.local_label.set_text(
            f"{self.home_arch_label.get_text()}\n{self.home_cel_label.get_text()}"
        )

    def _abrir_mapa(self, chave):
        dados = self._dados_local or {}
        cel = dados.get("celular") or {}
        d = dados.get(chave) or {}
        lat, lon = d.get("lat"), d.get("lon")
        # Arch e celular ficam juntos: o GPS do celular é o canônico.
        if chave == "arch" and cel.get("lat") is not None and cel.get("lon") is not None:
            lat, lon = cel.get("lat"), cel.get("lon")
        if lat is None or lon is None:
            return
        executar(["xdg-open", f"https://www.google.com/maps?q={lat},{lon}"])

    def _card_local(self):
        caixa = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        caixa.add_css_class("spook-rich-card")
        caixa.add_css_class("spook-card-android")
        rotulo = Gtk.Label(label="LOCATION", xalign=0)
        rotulo.add_css_class("spook-card-title")
        caixa.append(rotulo)

        # pin do Arch (ciano) + pin do Android (pink): dizem de quem e a posicao
        row_arch = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ic_arch = self._icone_metric("loc")
        ic_arch.add_css_class("spook-msi-arch")
        row_arch.append(ic_arch)
        self.home_arch_label.set_hexpand(True)
        row_arch.append(self.home_arch_label)
        caixa.append(row_arch)

        row_cel = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ic_cel = self._icone_metric("loc")
        ic_cel.add_css_class("spook-msi-android")
        row_cel.append(ic_cel)
        self.home_cel_label.set_hexpand(True)
        row_cel.append(self.home_cel_label)
        caixa.append(row_cel)
        botoes = Gtk.Box(spacing=6)
        botoes.append(self.home_map_arch)
        botoes.append(self.home_map_cel)
        botoes.append(self.home_map_refresh)
        caixa.append(botoes)
        return caixa

    def _update_local_arch(self):
        try:
            import localizacao
            localizacao.atualizar_arch()
        except Exception:
            pass
        GLib.idle_add(self._atualizar_local)

    # ── REFRESH ──

    def _atualizar(self):
        bat, bat_status, ram_pct, ram_used, ram_total, load, cpu_temp, cpu_pct, disk_pct, disk_used, disk_total = metricas_arch()
        bat_color = "spook-ok" if int(bat) >= 20 else "spook-warn" if int(bat) >= 10 else "spook-err"
        def _esc(s):
            return GLib.markup_escape_text(str(s))

        self.arch_status_label.set_markup(
            f'ONLINE  <span font_family="Material Symbols Rounded">\ue1a5</span> {_esc(bat)}% {_esc(str(bat_status).upper())}'
        )
        self.arch_status_label.remove_css_class("spook-metric")
        self.arch_status_label.add_css_class("spook-metric")
        self.arch_status_label.add_css_class(bat_color)
        self.arch_bat_label.set_text(f"{bat}% {str(bat_status).upper()}")
        self.arch_ram_label.set_text(f"RAM {ram_pct}% ({ram_used}/{ram_total} MB)")
        self.arch_disk_label.set_text(f"DISK {disk_pct}% ({disk_used}/{disk_total} GB)")
        self.arch_cpu_label.set_text(f"CPU {cpu_pct}%")
        self.arch_temp_label.set_text(f"TEMP {cpu_temp}°C")
        self.arch_load_label.set_text(f"LOAD {load}")
        self.arch_metrics_label.set_text(
            f"RAM {ram_pct}% ({ram_used}/{ram_total} MB)  DISK {disk_pct}% ({disk_used}/{disk_total} GB)\n"
            f"LOAD {load}  CPU TEMP {cpu_temp}°C"
        )
        self.arch_metrics_label.add_css_class("spook-metric")

        cel = {}
        try:
            cel = json.loads(CELULAR_STATUS.read_text(encoding="utf-8"))
        except OSError:
            pass
        except json.JSONDecodeError:
            pass
        online = bool(cel.get("online")) or self._presenca_celular
        bat_cel = cel.get("bateria")
        self.android_online_label.set_text("● ONLINE" if online else "○ OFFLINE")
        self.android_online_label.remove_css_class("spook-ok")
        self.android_online_label.remove_css_class("spook-err")
        self.android_online_label.add_css_class("spook-ok" if online else "spook-err")
        self.android_online_label.add_css_class("spook-metric")
        estado = "CHARGING" if cel.get("carregando") else "DISCHARGING"

        def _v(x):
            return "--" if x is None else x

        self.android_bat_label.set_text(
            f"{bat_cel}% {estado}" if bat_cel is not None else "BATTERY --"
        )
        self.android_ram_label.set_text(
            f"RAM {_v(cel.get('ram_pct'))}% "
            f"({_v(cel.get('ram_usado_mb'))}/{_v(cel.get('ram_total_mb'))} MB)"
        )
        self.android_disk_label.set_text(
            f"DISK {_v(cel.get('disco_pct'))}% "
            f"({_v(cel.get('disco_usado_gb'))}/{_v(cel.get('disco_total_gb'))} GB)"
        )
        self.android_cpu_label.set_text(f"CPU {_v(cel.get('cpu_pct'))}%")
        self.android_temp_label.set_text(f"TEMP {_v(cel.get('temp_c'))}°C")
        self.android_load_label.set_text(f"LOAD {_v(cel.get('load1'))}")
        self._atualizar_status(online)

        enviando = getattr(self, "_enviando_arquivo", False)
        if not enviando:
            self._bg(lambda: self._coletar_celular_periodico())
            self._bg(self._refresh_chat)
            self._bg(self._atualizar_local_periodico)
        return True

    def _atualizar_local_periodico(self):
        if self.stop_threads or getattr(self, "_local_atualizando", False):
            return
        agora = time.time()
        if agora - getattr(self, "_local_ultimo", 0) < 15:
            return
        self._local_ultimo = agora
        self._local_atualizando = True
        try:
            import localizacao
            dados = localizacao.estado()
            GLib.idle_add(lambda: self._render_local(dados))
        except Exception:
            pass
        finally:
            self._local_atualizando = False

    def _render_local(self, dados):
        try:
            self._montar_local(dados)
        except Exception:
            pass

    def _coletar_celular_periodico(self):
        if self.stop_threads or getattr(self, "_celular_coletando", False) or not CELULAR_STATUS_PY.exists():
            return
        self._celular_coletando = True
        try:
            executar_output(["python3", str(CELULAR_STATUS_PY)], timeout=15)
        finally:
            self._celular_coletando = False


if __name__ == "__main__":
    SpookOverlay().run(None)
