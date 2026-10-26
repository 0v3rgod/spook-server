from flask import Flask, request, send_file
from flask_socketio import SocketIO, emit, disconnect
from pareamento import (
    gerar_ou_carregar_chaves,
    calcular_codigo_confirmacao,
    fingerprint_pem,
    gerar_token_sessao,
    obter_token_sessao,
    salvar_chave_celular,
    obter_chave_celular,
    marcar_pareamento_confirmado,
    pareamento_esta_confirmado,
    revogar_pareamento,
)
from cryptography.hazmat.primitives import serialization
from pathlib import Path
import psutil
import pyperclip
import threading
import time
import subprocess
import os
import concurrent.futures

def _ydotool_socket() -> str:
    """Descobre onde o ydotoald realmente criou o socket.

    Nesta maquina o servico roda com --socket-path=/tmp/.ydotool_socket, mas o
    padrao do ydotool e $XDG_RUNTIME_DIR/.ydotool_socket. Se apontarmos para o
    lugar errado o ydotool NAO reclama: sai com rc=0 e nao move nada — foi por
    isso que o touchpad/teclado do app "nao funcionava".
    """
    candidatos = [
        os.environ.get("YDOTOOL_SOCKET"),
        "/tmp/.ydotool_socket",
        f"{os.environ.get('XDG_RUNTIME_DIR', '/run/user/1000')}/.ydotool_socket",
        os.path.expanduser("~/.ydotool_socket"),
    ]
    for c in candidatos:
        try:
            if c and os.path.exists(c):
                return c
        except Exception:
            continue
    return "/tmp/.ydotool_socket"


YENV = os.environ.copy()
YENV["YDOTOOL_SOCKET"] = _ydotool_socket()

# Thread pool para comandos de input (fire-and-forget) — evita bloquear Socket.IO
_input_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="spook-input")

import ssl
import base64
import pty
import json
import re
import select
import fcntl
import struct
import termios
import sys
import socket
import collections
import requests
import uuid
import hub_store
from dotenv import load_dotenv

load_dotenv()

try:
    from servidor_bluetooth import CAMINHO_PERFIL, UUID_PENTESTLINK, PerfilPentestLink
except Exception:
    CAMINHO_PERFIL = UUID_PENTESTLINK = PerfilPentestLink = None

app = Flask(__name__)
# Permitir buffers maiores para media e logs detalhados para debug
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading", ping_timeout=60, ping_interval=25, max_http_buffer_size=25 * 1024 * 1024)

chave_privada = gerar_ou_carregar_chaves()
chave_publica_pem = chave_privada.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo
).decode("utf-8")

bateria_celular = {"nivel": None}
# Presença do CELULAR real no servidor: qualquer cliente que conecta a partir de
# um IP que NAO e o proprio Arch (overlay/TUI ficam em 127.0.0.1 ou na tailscale
# do Arch) = o app do celular. Nao depende do streaming de bateria/GPS.
_REMOTO_IP_NAO_CEL = {"127.0.0.1", "100.78.69.110"}
_remoto_sids = set()


def _client_remoto():
    ip = str(request.environ.get("REMOTE_ADDR", ""))
    return ip not in _REMOTO_IP_NAO_CEL
ignorar_proxima_leitura_clipboard = {"valor": False}

PASTA_RECEBIDOS = Path.home() / "pentestlink-servidor" / "recebidos"
PASTA_COMPARTILHADOS = Path.home() / "pentestlink-servidor" / "compartilhados"
PASTA_RELATORIOS = Path.home() / "pentestlink-servidor" / "relatorios"
RELATORIOS_DRIVE = "relatorios"
ARQUIVO_NOTIFICACOES = Path.home() / "pentestlink-servidor" / "notificacoes.jsonl"
ARQUIVO_CONFIGURACAO = Path.home() / "pentestlink-servidor" / ".env"
ARQUIVO_SPOOK_II_COMANDO = Path("/tmp/spook-ii-command.json")
ARQUIVO_SPOOK_II_ESTADO = Path("/tmp/spook-ii-state.json")
ARQUIVO_BRIGHT_PROTECT = Path.home() / ".local" / "state" / "pentestlink" / "bright_protect"
ARQUIVO_BRIGHT_PROTECT.parent.mkdir(parents=True, exist_ok=True)
PASTA_RECEBIDOS.mkdir(parents=True, exist_ok=True)
PASTA_COMPARTILHADOS.mkdir(parents=True, exist_ok=True)
PASTA_RELATORIOS.mkdir(parents=True, exist_ok=True)
PASTA_RELATORIOS.chmod(0o700)


def carregar_configuracao():
    configuracao = {}
    if ARQUIVO_CONFIGURACAO.exists():
        for linha in ARQUIVO_CONFIGURACAO.read_text(encoding="utf-8").splitlines():
            if "=" in linha and not linha.startswith("#"):
                chave, valor = linha.split("=", 1)
                configuracao[chave] = valor
    for chave in ("GROQ_API_KEY", "GROQ_MODEL", "FILEBROWSER_URL", "FILEBROWSER_USERNAME", "FILEBROWSER_PASSWORD"):
        valor = os.environ.get(chave)
        if valor:
            configuracao[chave] = valor
    return configuracao


CONFIGURACAO = carregar_configuracao()
# O "relay" de hoje e' o hub do Redmi (app SPOOK SERVER, porta 5000). O nome
# antigo (Oracle Relay) ficou nos prints e confundia: nao ha mais Oracle no
# caminho do chat/tasks.
HUB_URL = "http://100.79.55.68:5000"
relay_bridge_sio = None
FILEBROWSER_URL = CONFIGURACAO.get("FILEBROWSER_URL", "").rstrip("/")
FILEBROWSER_USERNAME = CONFIGURACAO.get("FILEBROWSER_USERNAME", "")
FILEBROWSER_PASSWORD = CONFIGURACAO.get("FILEBROWSER_PASSWORD", "")
GROQ_API_KEY = CONFIGURACAO.get("GROQ_API_KEY", "")
GROQ_MODEL = CONFIGURACAO.get("GROQ_MODEL", "openai/gpt-oss-20b")
MODO_CLOUD = os.environ.get("RENDER") or os.environ.get("RAILWAY") or os.environ.get("FLY_APP_NAME") or False
filebrowser_sessao = requests.Session()
filebrowser_token = None
filebrowser_lock = threading.Lock()


def nome_arquivo_seguro(nome):
    nome_seguro = os.path.basename(nome)
    if not nome_seguro or nome_seguro in {".", ".."}:
        raise ValueError("Nome de arquivo inválido")
    return nome_seguro


def token_filebrowser():
    global filebrowser_token
    if not FILEBROWSER_URL or not FILEBROWSER_USERNAME or not FILEBROWSER_PASSWORD:
        raise RuntimeError("File Browser não configurado")
    with filebrowser_lock:
        if not filebrowser_token:
            resposta = filebrowser_sessao.post(
                f"{FILEBROWSER_URL}/api/login",
                json={"username": FILEBROWSER_USERNAME, "password": FILEBROWSER_PASSWORD},
                timeout=15,
            )
            resposta.raise_for_status()
            filebrowser_token = resposta.text.strip()
        return filebrowser_token


def requisicao_filebrowser(metodo, caminho, **kwargs):
    global filebrowser_token
    for tentativa in range(2):
        token = token_filebrowser()
        cabecalhos = {"X-Auth": token}
        cabecalhos.update(kwargs.pop("headers", {}))
        resposta = filebrowser_sessao.request(
            metodo,
            f"{FILEBROWSER_URL}/api/resources/{caminho}",
            headers=cabecalhos,
            timeout=60,
            **kwargs,
        )
        if resposta.status_code != 401 or tentativa:
            return resposta
        with filebrowser_lock:
            filebrowser_token = None
    return resposta

sessao_pty = {"fd": None, "pid": None}

CATALOGO_SCANS = {
    "NETWORK": [
        ("LIVE HOST", "Ping sweep — verifica se hosts respondem."),
        ("NETWORK SCANNER", "Descobre hosts em um CIDR autorizado."),
        ("COMMON PORTS", "Top 100 portas TCP mais usadas."),
        ("FAST NMAP GATEWAY", "Portas comuns do gateway informado."),
        ("FULL PORTS", "Todas as 65535 portas TCP, min-rate mais rápido."),
        ("SERVICES", "Identifica serviços nas portas informadas."),
        ("OS DETECTION", "Fingerprint de serviço; OS limitado sem root."),
        ("COMPLETE SCAN", "Scripts padrão + detecção de versão."),
        ("VULN SCAN", "Scripts de vulnerabilidade do nmap contra o alvo."),
        ("VULN TARGETED", "Scripts vuln em 80,443,445,3389 (portas substituíveis)."),
        ("BANNER GRAB", "Captura o banner do serviço com netcat."),
    ],
    "WEB": [
        ("HTTP HEADERS", "Status e headers de uma URL autorizada."),
        ("HTTP RESPONSE", "Primeiros 200 KB da página para inspeção local."),
        ("WHATWEB", "Identifica tecnologias web."),
        ("GOBUSTER DIRS", "Força diretórios web comuns."),
        ("NIKTO", "Scan de vulnerabilidades de servidor web."),
    ],
    "ENUM": [
        ("DNS RECORDS", "Consulta registros DNS do domínio."),
        ("DNS ANY", "Consulta ANY contra um servidor DNS."),
        ("SUBDOMAINS", "Força subdomínios DNS."),
        ("SMB ENUM", "Enumera shares e usuários SMB."),
        ("FTP ANON", "Verifica login FTP anônimo."),
        ("SSL/TLS INFO", "Certificado e protocolo TLS do host."),
        ("WHOIS", "Consulta registro do domínio."),
        ("TRACEROUTE", "Rota de rede até o alvo."),
    ],
    "LOCAL": [
        ("MY NETWORK", "Detalhes Wi-Fi visíveis ao Termux e hostname."),
        ("LOCAL ENUM", "Identidade Termux, build Android, processos e bateria."),
        ("SUID FIND", "Arquivos SUID acessíveis pelo Termux."),
        ("CAPABILITIES", "Capabilities de arquivos acessíveis pelo Termux."),
        ("TERMUX SERVICES", "Serviços gerenciados pelo usuário no Termux."),
        ("WIFI INFO", "Conexão Wi-Fi ativa visível ao Termux."),
        ("TCP CONNECT TEST", "Testa host e porta TCP autorizados."),
        ("LOG ANALYZER", "Últimas linhas de log Android visíveis ao Termux."),
    ],
    "CRACK": [
        ("HASH ID", "Identifica o tipo do hash."),
        ("JOHN", "Quebra hashes com John the Ripper."),
        ("JOHN SHOW", "Mostra resultados já quebrados pelo John."),
        ("HASHCAT MD5", "Quebra MD5 (-m 0) com wordlist."),
    ],
    "AUTO": [
        ("AUTO PENTEST", "recon.sh: descoberta → portas → serviços → HTTP → enum. Relatório no File Browser."),
    ],
}

CATALOGO_COMANDOS = {
    "sistema": [
        {"nome": "Lock Screen", "comando": "loginctl lock-session", "icone": "lock", "confirmar": False},
        {"nome": "Suspend", "comando": "systemctl suspend", "icone": "sleep", "confirmar": True},
        {"nome": "Shutdown", "comando": "systemctl poweroff", "icone": "power", "confirmar": True},
        {"nome": "Reboot", "comando": "systemctl reboot", "icone": "reboot", "confirmar": True},
        {"nome": "Reload WM", "comando": "hyprctl reload", "icone": "refresh", "confirmar": False},
        {"nome": "Airplane Mode", "comando": "nmcli radio all off", "icone": "plane", "confirmar": False},
        {"nome": "Radio On", "comando": "nmcli radio all on", "icone": "wifi", "confirmar": False},
    ],
    "captura": [
        {"nome": "Screenshot", "comando": "screenshot", "icone": "camera", "confirmar": False},
    ],
    "diagnostics": [
        {"nome": "Network Interfaces", "comando": "diagnostico_interfaces", "icone": "wifi", "confirmar": False},
        {"nome": "Local Routes", "comando": "diagnostico_rotas", "icone": "route", "confirmar": False},
        {"nome": "DNS Configuration", "comando": "diagnostico_dns", "icone": "dns", "confirmar": False},
        {"nome": "Listening Ports", "comando": "diagnostico_portas_locais", "icone": "terminal", "confirmar": False},
        {"nome": "Tailscale Status", "comando": "diagnostico_tailscale", "icone": "wifi", "confirmar": False},
    ],
    "info": [
        {"nome": "Public IP", "comando": "ip_publico", "icone": "globe", "confirmar": False},
    ],
    "midia": [
        {"nome": "Previous Track", "comando": "media_prev", "icone": "skip_prev", "confirmar": False},
        {"nome": "Play/Pause", "comando": "media_toggle", "icone": "play", "confirmar": False},
        {"nome": "Next Track", "comando": "media_next", "icone": "skip_next", "confirmar": False},
        {"nome": "Volume Down", "comando": "volume_down", "icone": "volume_down", "confirmar": False},
        {"nome": "Volume Up", "comando": "volume_up", "icone": "volume_up", "confirmar": False},
    ],
    "controles": [
        {"nome": "Bluetooth", "comando": "bluetooth_toggle", "icone": "bluetooth", "confirmar": False},
        {"nome": "Notifications", "comando": "notifications_toggle", "icone": "notifications", "confirmar": False},
        {"nome": "Keep Awake", "comando": "keep_awake_toggle", "icone": "coffee", "confirmar": False},
    ],
    "tela": [
        {"nome": "Brightness Down", "comando": "brightness_down", "icone": "brightness_low", "confirmar": False},
        {"nome": "Brightness Up", "comando": "brightness_up", "icone": "brightness_high", "confirmar": False},
        {"nome": "Night Light", "comando": "night_light_toggle", "icone": "nightlight", "confirmar": False},
        {"nome": "Brightness Guard", "comando": "brightness_protection_toggle", "icone": "shield", "confirmar": False},
        {"nome": "Dark Mode", "comando": "dark_mode_toggle", "icone": "contrast", "confirmar": False},
        {"nome": "Game Mode", "comando": "game_mode_toggle", "icone": "sports_esports", "confirmar": False},
    ],
    "input": [
        {"nome": "Remote Control", "comando": "remote_input", "icone": "keyboard", "confirmar": False},
    ],
}

def executar_no_redmi(comando):
    return subprocess.run(
        ["ssh", "celular", comando],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    ).stdout.strip()


def coletar_info_arch():
    """Retorna info do próprio Arch (notebook), sem depender do celular."""
    try:
        import platform, psutil, socket, subprocess
        hostname = platform.node()
        # IP local (não loopback)
        ips = []
        for iface, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                    ips.append(addr.address)
        ip_local = ips[0] if ips else "N/A"
        # Disco
        disco = subprocess.run(["df", "-h", "/"], capture_output=True, text=True).stdout.strip().split("\n")[-1]
        # Memória
        ram = psutil.virtual_memory()
        return {
            "arch": {
                "hostname": hostname,
                "ip_local": ip_local,
                "cpus": psutil.cpu_count(),
                "ram_total_gb": round(ram.total / (1024**3), 1),
                "ram_usado_gb": round(ram.used / (1024**3), 1),
                "ram_percent": ram.percent,
                "disco": disco,
                "kernel": platform.release(),
                "sistema": platform.system(),
            }
        }
    except Exception as e:
        return {"arch": {"erro": str(e)}}

def coletar_info_servidor():
    try:
        bateria = json.loads(executar_no_redmi("termux-battery-status"))
        return {
            "servidor": {
                "bateria": f"{bateria.get('percentage', 'N/A')}%",
                "disco": executar_no_redmi("df -h / | tail -n 1"),
                "memoria": executar_no_redmi("free -m | sed -n '2p'"),
            }
        }
    except Exception as e:
        return {"servidor": {"bateria": "Unavailable", "disco": "Unavailable", "memoria": str(e)}}


def coletar_info_celular():
    return {
        "celular": {
            "hostname": "Poco X7",
            "disco": "/data storage via Termux",
            "memoria": "Unavailable without Termux API",
            "cpus": "8",
            "uptime": "Unavailable",
            "kernel": "Android 14",
        }
    }


@app.route("/info_sistema")
def info_sistema():
    info = coletar_info_servidor()
    info.update(coletar_info_celular())
    return info


@socketio.on("info_sistema")
def emitir_info_sistema(dados=None):
    info = coletar_info_servidor()
    info.update(coletar_info_celular())
    emit("info_sistema", info)


def montar_instrucoes_ia():
    linhas_scans = []
    for secao, itens in CATALOGO_SCANS.items():
        linhas_scans.append(f"Scans / {secao}:")
        for nome, descricao in itens:
            linhas_scans.append(f"- {nome}: {descricao}")
    linhas_notebook = []
    for categoria, itens in CATALOGO_COMANDOS.items():
        linhas_notebook.append(f"Notebook / {categoria}:")
        for item in itens:
            linhas_notebook.append(f"- {item['nome']}")
    catalogo = "\n".join(linhas_scans + [""] + linhas_notebook)
    return (
        "Você é SPOOK, assistente geral do app SPOOK. Responda em português, de forma clara, objetiva e útil. "
        "Você pode ajudar com qualquer assunto: tecnologia, pentest, redes, Linux, programação, e também com "
        "perguntas gerais do dia a dia. Nunca execute ações, comandos ou mudanças no sistema; apenas explique e sugira. "
        "Quando o usuário pedir para executar um scan ou comando do próprio app, escolha entre os itens abaixo e "
        "diga em qual aba usar: Scans ou Notebook.\n\n"
        "Catálogo de scans e comandos do app (referência):\n"
        f"{catalogo}\n\n"
        "Wordlists no Termux (celular):\n"
        "- ~/wordlists/rockyou.txt  (senhas populares)\n"
        "- ~/wordlists/common.txt   (nomes de arquivos/diretórios web)\n"
        "- ~/wordlists/raft-small-words.txt  (dirs/arquivos comuns)\n"
        "- ~/wordlists/subdomains-top1million-5000.txt  (subdomínios)\n"
        "Use como: hashcat -m 0 hash.txt ~/wordlists/rockyou.txt\n"
        "Exemplos de hash para testes:\n"
        "- MD5: 5f4dcc3b5aa765d61d8327deb882cf99  (password)\n"
        "- SHA1: 5baa61e4c9b93f3f0682250b6cf8331b7ee68fd8  (password)\n"
        "- SHA256: ef92b778bafe771e89245b89ecbc08a44a4e1dd1cdd2b54fb852e5f7c5b6f7f4  (test)\n"
        "Scans CRACK usam: HASH ID, JOHN, JOHN SHOW, HASHCAT MD5."
    )


@socketio.on("connect", namespace="/ia")
def conectar_ia():
    if not GROQ_API_KEY:
        return False


@socketio.on("chat_ia_perguntar", namespace="/ia")
def responder_chat_ia(dados):
    pergunta = str((dados or {}).get("pergunta", "")).strip()
    sessao_id = str((dados or {}).get("sessao_id", "")).strip() or request.sid
    if not GROQ_API_KEY:
        emit("chat_ia_resposta", {"ok": False, "resposta": "IA não configurada. Adicione GROQ_API_KEY no servidor."})
        return
    if not pergunta:
        emit("chat_ia_resposta", {"ok": False, "resposta": "Envie uma pergunta."})
        return
    if len(pergunta) > 4000:
        emit("chat_ia_resposta", {"ok": False, "resposta": "Pergunta muito longa (máximo: 4000 caracteres)."})
        return
    try:
        resposta = requests.post(
            "https://api.groq.com/openai/v1/responses",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": GROQ_MODEL,
                "instructions": montar_instrucoes_ia(),
                "input": pergunta,
                "reasoning": {"effort": "low"},
                "max_output_tokens": 1024,
            },
            timeout=45,
        )
        resposta.raise_for_status()
        dados_resposta = resposta.json()
        conteudo = "".join(
            parte["text"]
            for item in dados_resposta.get("output", [])
            for parte in item.get("content", [])
            if parte.get("type") == "output_text"
        ).strip()
        if not conteudo:
            raise ValueError("Resposta vazia da IA")
        emit("chat_ia_resposta", {"ok": True, "resposta": conteudo, "sessao_id": sessao_id})
    except requests.RequestException as erro:
        emit("chat_ia_resposta", {"ok": False, "resposta": f"Falha na IA: {erro}"})
    except (KeyError, IndexError, TypeError, ValueError):
        emit("chat_ia_resposta", {"ok": False, "resposta": "Resposta inválida da IA."})


def salvar_relatorio(nome, titulo, resultado):
    instante = time.strftime("%Y-%m-%d %H:%M:%S %z")
    arquivo = PASTA_RELATORIOS / f"{nome}_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    # cabecalho no mesmo formato do agente do Termux, pra pasta unica no Drive.
    # o titulo pode vir multi-linha ("perfil alvo\n$ comando"): separa em
    # PROFILE/COMMAND em vez de deixar a 2a linha invadir o cabecalho.
    linhas_titulo = [l.strip() for l in str(titulo or "").splitlines() if l.strip()]
    perfil = linhas_titulo[0] if linhas_titulo else nome
    comando = next((l.lstrip("$ ").strip() for l in linhas_titulo[1:] if l.strip().startswith("$")), "")
    corpo = str(resultado or "").rstrip()
    cabecalho = "\n".join([
        "=" * 68,
        "  SPOOK  //  SCAN REPORT",
        "=" * 68,
        "  PROFILE  : %s" % perfil,
        "  SOURCE   : SPOOK-IA (servidor Arch)",
        "  HOST     : %s" % socket.gethostname(),
        "  DATE     : %s" % instante,
    ] + (["  COMMAND  : %s" % comando] if comando else []) + [
        "-" * 68,
        "",
    ])
    rodape = "\n".join(["", "=" * 68, "  END OF REPORT  //  SPOOK", "=" * 68, ""])
    arquivo.write_text(cabecalho + corpo + rodape, encoding="utf-8")
    arquivo.chmod(0o600)
    try:
        subprocess.run(["rclone", "copyto", str(arquivo), f"gdrive:SPOOK/{RELATORIOS_DRIVE}/{arquivo.name}"], timeout=60, capture_output=True)
    except Exception:
        pass
    return arquivo


def executar_diagnostico(comando):
    acoes = {
        "diagnostico_interfaces": ("Interfaces de rede", ["ip", "-brief", "address"]),
        "diagnostico_rotas": ("Tabela de rotas", ["ip", "route", "show"]),
        "diagnostico_dns": ("Configuração DNS", ["resolvectl", "status"]),        "diagnostico_portas_locais": ("Portas locais em escuta", ["ss", "-tulpen"]),
        "diagnostico_tailscale": ("Status Tailscale", ["tailscale", "status"]),
    }
    titulo, argv = acoes[comando]
    resultado = subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False)
    # resolvectl nao existe/nao roda em sistemas sem systemd-resolved (Arch puro):
    # cai para o arquivo, que e a fonte real de DNS da maquina.
    if comando == "diagnostico_dns" and resultado.returncode != 0:
        fallback = subprocess.run(["cat", "/etc/resolv.conf"], capture_output=True,
                                  text=True, timeout=10, check=False)
        if fallback.stdout.strip():
            argv = ["cat", "/etc/resolv.conf"]
            resultado = fallback
    saida = resultado.stdout or resultado.stderr or "Nenhuma saída disponível."
    relatorio = salvar_relatorio(comando, titulo, saida)
    return relatorio.name, resultado.returncode, saida


def _binario_disponivel(*nomes):
    """Primeiro executavel encontrado no PATH entre os nomes informados."""
    for nome in nomes:
        for pasta in os.environ.get("PATH", "").split(os.pathsep):
            caminho = os.path.join(pasta, nome)
            if os.path.isfile(caminho) and os.access(caminho, os.X_OK):
                return nome
    return None


# O Arch nao tem 'nc' (pacote openbsd-netcat) nem 'whatweb' (so AUR).
# ncat vem junto com o nmap e serve o mesmo proposito, entao preferimos ele.
_NC = _binario_disponivel("ncat", "nc", "netcat") or "nc"
_TRACE = _binario_disponivel("traceroute") or _binario_disponivel("tracepath") or "traceroute"
_WHATWEB = _binario_disponivel("whatweb")
_WHOIS = _binario_disponivel("whois")


def _wordlist(*nomes):
    """Primeira wordlist existente. SecLists fica em wordlists/ dentro do projeto."""
    candidatos = []
    for nome in nomes:
        candidatos += [
            str(Path.home() / "pentestlink-servidor" / "wordlists" / nome),
            f"/usr/share/wordlists/{nome}",
            f"/usr/share/wordlists/SecLists/Discovery/Web-Content/{nome}",
            f"/opt/SecLists/Discovery/Web-Content/{nome}",
        ]
    for caminho in candidatos:
        if os.path.isfile(caminho):
            return caminho
    return candidatos[0] if candidatos else ""


_WORDLIST_COMMON = _wordlist("common.txt")
_WORDLIST_SUBDOMAINS = _wordlist("subdomains.txt", "subdomains-all.txt")

# whatweb e so AUR; quando falta, os scripts http do nmap dao a mesma informacao
_WHATWEB_CMD = (f"whatweb {{alvo}}" if _WHATWEB
                else "nmap -sV --script http-title,http-headers,http-server-header "
                     "--script-args http.useragent=Spook {alvo}")

# whois tambem nao vem instalado; RDAP over HTTPS e o equivalente moderno
_WHOIS_CMD = (f"whois {{alvo}}" if _WHOIS
              else 'curl -sL --max-time 25 -H "Accept: application/rdap+json" '
                   '"https://rdap.org/$(case {alvo} in *[0-9].[0-9]|[0-9]*) echo ip;; *) echo domain;; esac)/{alvo}"')

SCANS_CMD_IA = {
    "LIVE HOST": "nmap -sn {alvo}",
    "NETWORK SCANNER": "nmap -sn {alvo}",
    "COMMON PORTS": "nmap -sT --top-ports 100 {alvo}",
    "FULL PORTS": "nmap -p- --min-rate 3000 {alvo}",
    "SERVICES": "nmap -sT -sV -p {portas} {alvo}",
    "OS DETECTION": "nmap -sT -sV --osscan-limit {alvo}",
    "COMPLETE SCAN": "nmap -sC -sV {alvo}",
    "VULN SCAN": "nmap --script vuln {alvo}",
    "VULN TARGETED": "nmap -sV --script vuln -p {portas} {alvo}",
    "BANNER GRAB": 'for p in {portas}; do echo "=== porta $p ==="; timeout 6 ' + _NC + ' -nv {alvo} "$p" < /dev/null; done',
    "HTTP HEADERS": "curl -I -L --max-redirs 3 --connect-timeout 10 --max-time 20 {alvo}",
    "HTTP RESPONSE": "curl -L --max-redirs 3 --connect-timeout 10 --max-time 20 --range 0-204799 {alvo}",
    "WHATWEB": _WHATWEB_CMD,
    "GOBUSTER DIRS": f"gobuster dir -u {{alvo}} -w {_WORDLIST_COMMON} --no-error -b 403,404",
    "NIKTO": "nikto -h {alvo}",
    "DNS RECORDS": "nslookup {alvo}",
    "DNS ANY": "nslookup -type=any {alvo}",
    "SUBDOMAINS": f"gobuster dns -d {{alvo}} -w {_WORDLIST_SUBDOMAINS} --no-error",
    "SMB ENUM": "nmap --script smb-enum-shares,smb-enum-users -p445 {alvo}",
    "FTP ANON": "nmap --script ftp-anon -p21 {alvo}",
    "WHOIS": _WHOIS_CMD,
    "TRACEROUTE": f"{_TRACE} {{alvo}}",
    "MY NETWORK": "ip -br addr; echo; hostname",
    "LOCAL ENUM": "whoami; id; uname -a; echo; uptime; echo; ps aux --sort=-%cpu | head -15",
    "SUID FIND": "find / -xdev -perm -4000 -type f -print 2>/dev/null | head -30",
    "TCP CONNECT TEST": 'for p in {portas}; do timeout 8 ' + _NC + ' -vz -w 5 {alvo} "$p"; done',
}

# Scans que precisam de UM comando por porta: o nc aceita uma porta so.
# "80,443" viraria um unico argumento invalido, entao a lista e normalizada
# para espacos e o template itera com for.
SCANS_LOOP_PORTA = {"BANNER GRAB", "TCP CONNECT TEST"}


def executar_scan_ia(nome_scan, alvo="", portas=""):
    """Executa um scan do catálogo no Arch e devolve resultado."""
    cmd_template = SCANS_CMD_IA.get(nome_scan)
    if not cmd_template:
        return {"ok": False, "erro": f"Scan não encontrado: {nome_scan}"}
    if "{alvo}" in cmd_template and not alvo:
        return {"ok": False, "erro": "Scan requer alvo."}
    if "{portas}" in cmd_template and not portas:
        portas = "80,443,445,3389"
    if nome_scan in SCANS_LOOP_PORTA:
        lista = [p for p in re.split(r"[,\s]+", str(portas)) if p]
        if not lista:
            return {"ok": False, "erro": "Scan requer portas."}
        portas = " ".join(lista)
    comando = cmd_template.format(alvo=alvo or "", portas=portas)
    nome_arquivo = nome_arquivo_seguro(nome_scan.replace(" ", "_"))
    try:
        resultado = subprocess.run(
            ["bash", "-c", comando],
            capture_output=True, text=True, timeout=300, check=False,
        )
        saida = (resultado.stdout or "") + (resultado.stderr or "")
        saida = saida.strip() or "Nenhuma saída."
        relatorio = salvar_relatorio(f"ia_scan_{nome_arquivo}", f"Scan IA: {nome_scan} {alvo}\n$ {comando}", saida)
        return {"ok": True, "saida": f"{saida}\n\nRelatório: {relatorio.name}", "relatorio": relatorio.name}
    except subprocess.TimeoutExpired:
        return {"ok": False, "erro": f"Scan {nome_scan} excedeu 300s."}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def executar_comando_ia(nome_comando):
    """Executa comando do catálogo no Arch e devolve resultado (sem emitir no socket)."""
    item = None
    for categoria, comandos in CATALOGO_COMANDOS.items():
        item = next((c for c in comandos if c["nome"] == nome_comando), None)
        if item:
            break
    if item is None:
        return {"ok": False, "erro": f"Comando não encontrado: {nome_comando}"}
    comando = item["comando"]
    try:
        _detectar_env_hypr()
        if comando == "screenshot":
            caminho = PASTA_COMPARTILHADOS / f"screenshot_{int(time.time())}.png"
            subprocess.run(["grim", str(caminho)], timeout=10, check=True)
            return {"ok": True, "saida": f"Screenshot salvo em {caminho.name}", "relatorio": caminho.name}
        if comando == "ip_publico":
            resultado = subprocess.run(["curl", "-fsS", "--max-time", "10", "https://ifconfig.me/ip"], capture_output=True, text=True, timeout=15, check=False)
            ip = resultado.stdout.strip()
            if resultado.returncode != 0 or not ip:
                return {"ok": False, "erro": resultado.stderr.strip() or "Falha ao obter IP público"}
            relatorio = salvar_relatorio("ip_publico", "IP público", ip)
            return {"ok": True, "saida": f"IP público: {ip}", "relatorio": relatorio.name}
        if comando.startswith("diagnostico_"):
            relatorio, codigo, saida = executar_diagnostico(comando)
            if codigo == 0:
                return {"ok": True, "saida": saida, "relatorio": relatorio}
            return {"ok": False, "erro": saida}
        if comando.startswith(("hyprctl", "systemctl", "loginctl", "pkill", "brightnessctl")):
            resultado = subprocess.run(["bash", "-c", comando], capture_output=True, text=True, timeout=10, check=False)
            saida = (resultado.stdout or "") + (resultado.stderr or "")
            return {"ok": resultado.returncode == 0, "saida": saida.strip() or comando + " executado", "retorno": resultado.returncode}
        return {"ok": False, "erro": f"Comando não suportado para IA: {nome_comando}"}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


@app.route("/status")
def status():
    return {"status": "online", "mensagem": "Notebook conectado!"}


@app.route("/parear", methods=["POST"])
def parear():
    dados = request.get_json()
    chave_publica_celular = dados.get("chave_publica")
    if not chave_publica_celular:
        return {"erro": "chave_publica ausente"}, 401
    salvar_chave_celular(chave_publica_celular)
    fingerprint = fingerprint_pem(chave_publica_celular)
    codigo = calcular_codigo_confirmacao(chave_publica_pem, chave_publica_celular)
    print(f"[PAREAMENTO] Código SAS: {codigo} | fingerprints: {fingerprint}")
    return {"chave_publica": chave_publica_pem, "codigo_confirmacao": codigo, "token": gerar_token_sessao()}


@app.route("/tocar-celular", methods=["POST"])
def rota_tocar_celular():
    socketio.emit("tocar_celular", {})
    return {"ok": True}


@app.route("/clipboard", methods=["GET"])
def rota_ler_clipboard():
    try:
        texto = pyperclip.paste() or ""
    except Exception:
        texto = ""
    return {"texto": texto}


@app.route("/clipboard/enviar", methods=["POST"])
def rota_enviar_clipboard():
    texto = request.get_json().get("texto", "")
    ignorar_proxima_leitura_clipboard["valor"] = True
    pyperclip.copy(texto)
    socketio.emit("clipboard_notebook", {"texto": texto})
    return {"ok": True}


def validar_conexao_socket(auth, contexto):
    if not auth:
        return False
    token = auth.get("token", "")
    if not token:
        return False
    token_atual = obter_token_sessao()
    return token == token_atual


def coletar_metricas():
    bateria = psutil.sensors_battery()
    ram = psutil.virtual_memory()
    swap = psutil.swap_memory()
    cpu = psutil.cpu_percent(interval=0.1)
    temps = psutil.sensors_temperatures()
    temp_cpu = 0.0
    for k, v in temps.items():
        for t in v:
            if t.current and ("core" in k.lower() or "cpu" in k.lower() or "package" in k.lower()):
                temp_cpu = t.current
                break
        if temp_cpu:
            break
    return {
        "bateria": int(bateria.percent) if bateria else -1,
        "ram_percent": ram.percent,
        "ram_usado_mb": ram.used // (1024 * 1024),
        "ram_total_mb": ram.total // (1024 * 1024),
        "swap_percent": swap.percent,
        "swap_usado_mb": swap.used // (1024 * 1024),
        "swap_total_mb": swap.total // (1024 * 1024),
        "cpu_percent": cpu,
        "temp_cpu": round(temp_cpu, 1)
    }


def emitir_metricas_periodicas():
    while True:
        try:
            time.sleep(5)
            metricas = coletar_metricas()
            socketio.emit("metricas_notebook", metricas)
            # Também publica no Hub (bridge Arch->Hub)
            try:
                if relay_bridge_sio and getattr(relay_bridge_sio, 'connected', False):
                    relay_bridge_sio.emit('metricas_notebook', metricas)
            except Exception as e:
                log.warning(f"bridge metricas_notebook falhou: {e}")
        except Exception as e:
            log.warning(f"emitir_metricas_periodicas: {e}")


@socketio.on("connect")
def ao_conectar(auth=None):
    print(f"[CONNECT] Auth recebido: {auth}", flush=True)
    if not validar_conexao_socket(auth, "connect"):
        print(f"[ERRO] Token inválido ou ausente. Auth: {auth}", flush=True)
        return False
    print("[CONECTADO] Celular via WebSocket (token válido).", flush=True)
    metricas = coletar_metricas()
    emit("bateria_notebook", {"nivel": metricas["bateria"]})
    emit("metricas_notebook", metricas)
    marcar_pareamento_confirmado()
    # Presenca do celular real: origem diferente do proprio Arch = app do celular.
    remoto = _client_remoto()
    mudou = remoto and not _remoto_sids
    if remoto:
        _remoto_sids.add(request.sid)
    if mudou:
        emit("celular_presenca", {"online": True}, broadcast=True)
    else:
        # O cliente que acabou de conectar (overlay ou celular) recebe o estado atual.
        emit("celular_presenca", {"online": bool(_remoto_sids)})


@socketio.on("disconnect")
def ao_desconectar():
    print("[DESCONECTADO] Celular desconectou.", flush=True)
    if request.sid in _remoto_sids:
        _remoto_sids.discard(request.sid)
        if not _remoto_sids:
            emit("celular_presenca", {"online": False}, broadcast=True)


@socketio.on_error_default
def erro_padrao(e):
    print(f"[ERRO SOCKET.IO] {e}", flush=True)


@socketio.on("chat_listar")
def chat_listar(dados=None):
    try:
        emit("chat_lista", {"mensagens": hub_store.listar_mensagens()})
    except Exception as e:
        emit("chat_lista", {"mensagens": [], "erro": str(e)})


@socketio.on("chat_enviar")
def chat_enviar(dados):
    texto = str((dados or {}).get("texto", "")).strip()
    msg_id = str((dados or {}).get("id", "")).strip()
    tipo = str((dados or {}).get("tipo", "") or "texto").strip()
    arquivo = str((dados or {}).get("arquivo", "")).strip()
    if not texto and not arquivo:
        emit("chat_erro", {"erro": "Mensagem vazia"})
        return
    try:
        # Se veio de local (TUI/Overlay), a origem é 'arch'
        origem = (dados or {}).get("from", "arch")

        if msg_id:
            if msg_id.endswith(".json"):
                msg_id = msg_id[:-5]
            msg = hub_store.enviar_texto_id(texto, origem=origem, msg_id=msg_id, tipo=tipo, arquivo=arquivo)
        else:
            msg = hub_store.enviar_texto(texto, origem=origem)

        # Broadcast local (deduplicado por id)
        _emitir_chat_nova(msg)

        # Envia para o hub do Redmi se estivermos em modo Bridge
        # para que o celular receba instantaneamente
        try:
            if relay_bridge_sio and relay_bridge_sio.connected:
                relay_bridge_sio.emit("chat_enviar", msg)
                print(f"[BRIDGE] Mensagem encaminhada para o hub do Redmi: {msg['id']}", flush=True)
            else:
                print(f"[BRIDGE] Alerta: bridge SIO não conectada. Não encaminhado.", flush=True)
        except Exception as e:
            print(f"[BRIDGE] Falha ao encaminhar para o hub do Redmi: {e}", flush=True)

        print(f"[CHAT] {origem.upper()}: {texto[:60]}", flush=True)
    except Exception as e:
        emit("chat_erro", {"erro": str(e)})


@socketio.on("chat_apagar")
def chat_apagar(dados):
    msg_id = str((dados or {}).get("id", ""))
    try:
        ok = hub_store.apagar_mensagem(msg_id)
        if ok:
            emit("chat_apagada", {"id": msg_id}, broadcast=True)
            # Avisa o hub do Redmi
            try:
                if relay_bridge_sio and relay_bridge_sio.connected:
                    relay_bridge_sio.emit("chat_apagar", {"id": msg_id})
            except Exception: pass
    except Exception as e:
        print(f"[CHAT] erro ao apagar: {e}")


@socketio.on("task_listar")
def task_listar(dados=None):
    try:
        emit("task_lista", {"tasks": hub_store.listar_tasks()})
    except Exception as e:
        emit("task_lista", {"tasks": [], "erro": str(e)})


@socketio.on("task_nova")
def task_criar(dados):
    titulo = str((dados or {}).get("titulo", "")).strip()
    if not titulo:
        return
    try:
        nova, lista = hub_store.criar_task(titulo)
        emit("task_nova", nova, broadcast=True)
        # Avisa o hub do Redmi
        try:
            if relay_bridge_sio and relay_bridge_sio.connected:
                relay_bridge_sio.emit("task_nova", {"titulo": titulo})
        except Exception: pass
    except Exception as e:
        emit("task_erro", {"erro": str(e)})


@socketio.on("task_atualizada")
def task_atualizar(dados):
    tid = str((dados or {}).get("id", ""))
    if not tid:
        return
    feito = bool((dados or {}).get("feito", False))
    try:
        lista = hub_store.marcar_task(tid, feito)
        t = next((x for x in lista if x.get("id") == tid), {})
        emit("task_atualizada", t, broadcast=True)
        # Avisa o hub do Redmi
        try:
            if relay_bridge_sio and relay_bridge_sio.connected:
                relay_bridge_sio.emit("task_atualizada", {"id": tid, "feito": feito})
        except Exception: pass
    except Exception as e:
        emit("task_erro", {"erro": str(e)})


@socketio.on("task_apagada")
def task_apagar(dados):
    tid = str((dados or {}).get("id", ""))
    try:
        hub_store.apagar_task(tid)
        emit("task_apagada", {"id": tid}, broadcast=True)
        # Avisa o hub do Redmi
        try:
            if relay_bridge_sio and relay_bridge_sio.connected:
                relay_bridge_sio.emit("task_apagada", {"id": tid})
        except Exception: pass
    except Exception as e:
        print(f"[SERVER] erro ao apagar task: {e}")


_registro_chat = {"ids": set()}
_registro_tasks = {"ids": set()}


def monitorar_chat():
    while True:
        time.sleep(2)
        try:
            msgs = hub_store.listar_mensagens()
        except Exception:
            continue
        ids_atuais = {m.get("id") for m in msgs}
        for m in msgs:
            if m.get("id") not in _registro_chat["ids"]:
                _emitir_chat_nova(m)
        removidas = _registro_chat["ids"] - ids_atuais
        for rid in removidas:
            socketio.emit("chat_apagada", {"id": rid})
        _registro_chat["ids"] = ids_atuais


def monitorar_tasks():
    while True:
        time.sleep(2)
        try:
            tasks = hub_store.listar_tasks()
        except Exception:
            continue
        ids_atuais = {t.get("id") for t in tasks}
        for t in tasks:
            if t.get("id") not in _registro_tasks["ids"]:
                socketio.emit("task_nova", t)
        removidas = _registro_tasks["ids"] - ids_atuais
        for rid in removidas:
            socketio.emit("task_apagada", {"id": rid})
        atualizados = _registro_tasks["ids"] & ids_atuais
        for tid in atualizados:
            local = next((t for t in tasks if t.get("id") == tid), None)
            if local and tid in _registro_tasks["ids"]:
                prev = next((t for t in _registro_tasks.get("tasks", []) if t.get("id") == tid), None)
                if prev and (local.get("ts", 0) != prev.get("ts", 0) or local.get("feito") != prev.get("feito")):
                    socketio.emit("task_atualizada", local)
        _registro_tasks["ids"] = ids_atuais
        _registro_tasks["tasks"] = tasks


@socketio.on("bateria_celular")
def bateria_recebida(dados):
    nivel = dados.get("nivel")
    bateria_celular["nivel"] = nivel
    print(f"Bateria do celular: {nivel}%")

    with open("/tmp/bateria_celular.txt", "w") as f:
        f.write(str(nivel))


def _atualizar_localizacao_arch():
    """Uma passada: atualiza o geo do Arch (ipinfo) pro card LOCATION do app.

    Sem isso o card mostrava "No Arch location" pra sempre: o endpoint
    /localizacao so lia o que alguem tinha gravado antes. O loop de 30min
    vive na thread (_loop_localizacao_arch), nao aqui -- handler de socket
    nao pode segurar a requisicao.
    """
    import localizacao as loc
    try:
        loc.atualizar_arch()
        print("[LOCALIZACAO] Arch atualizado via ip geolocation", flush=True)
    except Exception as e:
        print(f"[LOCALIZACAO ERRO] {e}", flush=True)


def _loop_localizacao_arch():
    while True:
        _atualizar_localizacao_arch()
        time.sleep(1800)


@socketio.on("gps_celular")
def _gps_celular(dados=None):
    """O celular manda o GPS a cada 30s; aproveitamos pra refrescar o do Arch."""
    _atualizar_localizacao_arch()
    gps_recebido(dados or {})


def gps_recebido(dados):
    try:
        import localizacao
        localizacao.atualizar_celular(dados)
        print(f"[GPS] Celular: {dados.get('lat')},{dados.get('lon')} precisão {dados.get('precisao')}m")
    except Exception as e:
        print(f"[GPS] erro ao salvar: {e}")


def rota_enviar_gps_ping():
    try:
        import localizacao
        dados = localizacao.estado()
        socketio.emit("gps_notebook", {"solicitar": True})
        return {"ok": True, "dados": dados}
    except Exception as e:
        return {"erro": str(e)}, 500


@app.route("/localizacao", methods=["GET"])
def rota_localizacao():
    try:
        import localizacao
        return {"dados": localizacao.estado()}
    except Exception as e:
        return {"erro": str(e)}, 500


@app.route("/arquivo-link", methods=["GET"])
def arquivo_link():
    nome = request.args.get("nome", "")
    if not nome:
        return {"erro": "nome vazio"}, 400
    try:
        arquivo_id = hub_store.id_arquivo(nome)
        if arquivo_id:
            return {"link": f"https://drive.google.com/file/d/{arquivo_id}/view"}
        return {"erro": "não encontrado no drive"}, 404
    except Exception as e:
        return {"erro": str(e)}, 500


@app.route("/upload", methods=["POST"])
def rota_upload_media():
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "").strip()
    if not token or token != obter_token_sessao():
        return {"ok": False, "erro": "Unauthorized"}, 401

    if "file" not in request.files:
        return {"ok": False, "erro": "No file part"}, 400

    file = request.files["file"]
    msg_id = request.form.get("id", "")
    texto = request.form.get("texto", "")
    tipo = request.form.get("tipo", "arquivo")

    if file.filename == "":
        return {"ok": False, "erro": "No selected file"}, 400

    try:
        nome_seguro = nome_arquivo_seguro(file.filename)
        temp_path = PASTA_RECEBIDOS / f"upload_{int(time.time())}_{nome_seguro}"
        file.save(str(temp_path))

        msg = hub_store.enviar_arquivo(str(temp_path), tipo=tipo, caption=texto, msg_id=msg_id)
        _emitir_chat_nova(msg)

        return {"ok": True, "filename": msg["arquivo"].split("/")[-1]}
    except Exception as e:
        return {"ok": False, "erro": str(e)}, 500


@app.route("/relatorios")
def rota_listar_relatorios():
    """Lista os relatorios gerados no Arch (scans da IA, diagnosticos, comandos)."""
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "").strip()
    if not token or token != obter_token_sessao():
        return {"ok": False, "erro": "Unauthorized"}, 401
    try:
        itens = []
        for arquivo in sorted(PASTA_RELATORIOS.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True):
            stat = arquivo.stat()
            itens.append({
                "nome": arquivo.name,
                "bytes": stat.st_size,
                "modificado": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
            })
        return {"ok": True, "items": itens[:200], "total": len(itens)}
    except Exception as e:
        return {"ok": False, "erro": str(e)}, 500


@app.route("/relatorios/<path:filename>")
def rota_baixar_relatorio(filename):
    try:
        nome_seguro = nome_arquivo_seguro(filename)
        caminho = PASTA_RELATORIOS / nome_seguro
        if caminho.exists() and caminho.is_file():
            return send_file(caminho)
        return {"ok": False, "erro": "File not found"}, 404
    except Exception as e:
        return {"ok": False, "erro": str(e)}, 500


@app.route("/media/<path:filename>")
def rota_servir_media(filename):
    try:
        nome_seguro = nome_arquivo_seguro(filename)
        caminho_local = hub_store.baixar_midia(nome_seguro)
        if caminho_local and os.path.exists(caminho_local):
            return send_file(caminho_local)
        return {"ok": False, "erro": "File not found"}, 404
    except Exception as e:
        return {"ok": False, "erro": str(e)}, 500


@socketio.on("clipboard_celular")
def clipboard_recebido(dados):
    texto = dados.get("texto", "")
    ignorar_proxima_leitura_clipboard["valor"] = True
    pyperclip.copy(texto)
    print(f"Clipboard atualizado a partir do celular: {texto[:50]}")


@socketio.on("notificacao_celular")
def notificacao_recebida(dados):
    app_origem = dados.get("app", "")
    titulo = dados.get("titulo", "")
    texto = dados.get("texto", "")
    registro = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "app": app_origem,
        "titulo": titulo,
        "texto": texto,
    }
    with ARQUIVO_NOTIFICACOES.open("a", encoding="utf-8") as arquivo:
        arquivo.write(json.dumps(registro, ensure_ascii=False) + "\n")
    print(f"Notificação de {app_origem}: {titulo} - {texto}")
    # O overlay (spook-overlay.py) escuta "notificacao_nova"; sem esse emit
    # local a notificacao do celular so aparecia no historico/relay.
    try:
        socketio.emit("notificacao_nova", registro)
    except Exception as e:
        print(f"[NOTIF ERRO] emit local: {e}", flush=True)
    # O overlay (spook-overlay.py) conecta no RELAY, nao no Arch: o evento tem
    # que atravessar a ponte (Arch -> relay -> broadcast) para aparecer no canto.
    try:
        if relay_bridge_sio and relay_bridge_sio.connected:
            relay_bridge_sio.emit("notificacao_celular", registro)
        else:
            print("[NOTIF] relay bridge offline: notificacao so no historico local", flush=True)
    except Exception as e:
        print(f"[NOTIF ERRO] relay: {e}", flush=True)


@socketio.on("enviar_arquivo")
def receber_arquivo(dados):
    nome_seguro = "arquivo_sem_nome"
    try:
        nome_seguro = nome_arquivo_seguro(dados.get("nome", nome_seguro))
        conteudo_bytes = base64.b64decode(dados.get("conteudo", ""), validate=True)
        if not conteudo_bytes:
            raise ValueError("Arquivo vazio")
        requisicao_filebrowser("POST", "SPOOK/", data=b"")
        resposta = requisicao_filebrowser(
            "POST",
            f"SPOOK/{nome_seguro}?override=true",
            data=conteudo_bytes,
            headers={"Content-Type": "application/octet-stream"},
        )
        resposta.raise_for_status()
        print(f"Arquivo enviado ao File Browser: {nome_seguro} ({len(conteudo_bytes)} bytes)")
        emit("arquivo_recebido_confirmacao", {"nome": nome_seguro, "ok": True})
    except Exception as e:
        emit("arquivo_recebido_confirmacao", {"nome": nome_seguro, "ok": False, "erro": str(e)})


@socketio.on("listar_compartilhados")
def listar_arquivos_compartilhados(dados=None):
    try:
        requisicao_filebrowser("POST", "SPOOK/", data=b"")
        resposta = requisicao_filebrowser("GET", "SPOOK/")
        resposta.raise_for_status()
        dados_filebrowser = resposta.json()
        arquivos = sorted(
            item["name"]
            for item in dados_filebrowser.get("items", [])
            if not item.get("isDir", False)
        )
        emit("lista_compartilhados", {"arquivos": arquivos})
    except Exception as e:
        print(f"[FILE BROWSER] Erro ao listar: {e}")
        emit("lista_compartilhados", {"arquivos": [], "erro": "Não foi possível listar o File Browser"})


@socketio.on("listar_relatorios")
def listar_relatorios(dados=None):
    relatorios = sorted(
        (f.name for f in PASTA_RELATORIOS.iterdir() if f.is_file()),
        reverse=True,
    )
    emit("lista_relatorios", {"relatorios": relatorios})


@socketio.on("solicitar_arquivo")
def enviar_arquivo_para_celular(dados):
    nome_seguro = ""
    try:
        nome_seguro = nome_arquivo_seguro(dados.get("nome", ""))
        resposta = requisicao_filebrowser("GET", f"SPOOK/{nome_seguro}")
        if resposta.status_code == 404:
            emit("arquivo_notebook", {"nome": nome_seguro, "ok": False, "erro": "Arquivo não encontrado"})
            return
        resposta.raise_for_status()
        if resposta.headers.get("Content-Type", "").startswith("application/json"):
            conteudo_bytes = resposta.json().get("content", "").encode("utf-8")
        else:
            conteudo_bytes = resposta.content
        conteudo_base64 = base64.b64encode(conteudo_bytes).decode("utf-8")
        emit("arquivo_notebook", {"nome": nome_seguro, "conteudo": conteudo_base64, "ok": True})
    except Exception as e:
        emit("arquivo_notebook", {"nome": nome_seguro, "ok": False, "erro": str(e)})


@socketio.on("listar_comandos")
def listar_comandos_disponiveis(dados=None):
    lista = {}
    for categoria, comandos in CATALOGO_COMANDOS.items():
        lista[categoria] = [
            {
                "nome": c["nome"],
                "icone": c["icone"],
                "confirmar": c["confirmar"],
                "comando": c["comando"],
                "ativo": _estado_comando(c["comando"]),
            }
            for c in comandos
        ]
    emit("lista_comandos", {"comandos": lista})


def _proc_rodando(nome):
    try:
        pid = subprocess.run(["pidof", "-x", nome], capture_output=True, text=True, timeout=3, check=False)
        return bool(pid.stdout.strip())
    except Exception:
        return False


def _detectar_env_hypr():
    # O servidor roda como systemd user unit sem as variáveis da sessão do
    # Hyprland. Sem elas, hyprctl/grim/hyprsunset/hyprlock falham (ex: botões
    # "Lock Screen", "Reload WM", "Modo Jogo" pareciam não fazer nada).
    # NOTA: o wayland socket real nesta máquina é wayland-1 (não wayland-0).
    try:
        runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        hypr_dir = Path(runtime) / "hypr"
        if hypr_dir.exists():
            sigs = sorted((p.name for p in hypr_dir.glob("*") if p.is_dir() and p.name != "lock"), reverse=True)
            if sigs:
                os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = sigs[0]
        wl = sorted((p.name for p in Path(runtime).glob("wayland-*") if p.is_socket()), reverse=True)
        if wl:
            os.environ["WAYLAND_DISPLAY"] = wl[0]
        os.environ.setdefault("WAYLAND_DISPLAY", "wayland-1")
    except Exception:
        pass


_ultimas_chat_novas = collections.deque(maxlen=300)


def _emitir_chat_nova(msg):
    """Emite chat_nova deduplicado por id.

    O mesmo evento chega por até 3 caminhos (broadcast do chat_enviar, monitor
    via mirror/Drive e o loop-back do Relay Bridge). Sem dedup, clientes do
    socket do Arch viam a mesma mensagem 2x-3x ("oioi").
    """
    mid = msg.get("id")
    if mid and mid in _ultimas_chat_novas:
        return
    if mid:
        _ultimas_chat_novas.append(mid)
    socketio.emit("chat_nova", msg)


def _estado_ii():
    try:
        estado = json.loads(ARQUIVO_SPOOK_II_ESTADO.read_text(encoding="utf-8"))
        return estado if isinstance(estado, dict) else {}
    except Exception:
        return {}


def _executar_ii(acao, alvo):
    pedido_id = str(uuid.uuid4())
    temporario = ARQUIVO_SPOOK_II_COMANDO.with_suffix(".tmp")
    temporario.write_text(json.dumps({"id": pedido_id, "action": acao, "target": bool(alvo)}), encoding="utf-8")
    os.replace(temporario, ARQUIVO_SPOOK_II_COMANDO)
    limite = time.monotonic() + 3
    while time.monotonic() < limite:
        estado = _estado_ii()
        if estado.get("id") == pedido_id:
            return estado
        time.sleep(0.05)
    raise RuntimeError("O painel ii não confirmou o comando. Verifique se o Quickshell está aberto.")


def _estado_comando(comando):
    try:
        estado_ii = _estado_ii()
        if comando == "wifi_toggle":
            return subprocess.run(["nmcli", "radio", "wifi"], capture_output=True, text=True, timeout=5, check=False).stdout.strip() == "enabled"
        if comando == "bluetooth_toggle":
            saida = subprocess.run(["bluetoothctl", "show"], capture_output=True, text=True, timeout=5, check=False).stdout
            return "Powered: yes" in saida
        if comando == "game_mode_toggle":
            return estado_ii.get("game_mode", not _hypr_bool("animations:enabled"))
        if comando == "dark_mode_toggle":
            return estado_ii.get("dark_mode", False)
        if comando == "keep_awake_toggle":
            return estado_ii.get("keep_awake", False)
        if comando == "notifications_toggle":
            return estado_ii.get("notifications_silent", os.path.exists("/tmp/spook_notif_silent"))
        if comando == "night_light_toggle":
            return estado_ii.get("night_light", False)
        if comando == "brightness_protection_toggle":
            return ARQUIVO_BRIGHT_PROTECT.exists()
        if comando == "remote_input":
            return False
    except Exception:
        pass
    return None


def _hypr_bool(chave):
    try:
        r = subprocess.run(["hyprctl", "getoption", chave], capture_output=True, text=True, timeout=3, check=False)
        saida = r.stdout.strip().lower()
        return saida.startswith("bool: true") or saida == "bool: true"
    except Exception:
        return False


BRILHO_PROTECAO_PCT = 15


def _dispositivo_backlight():
    for nome in ("intel_backlight", "amdgpu_bl0", "nvidia_0", "dell_backlight", "asus_led_backlight"):
        r = subprocess.run(["brightnessctl", "-d", nome, "get"], capture_output=True, text=True, timeout=3, check=False)
        if r.returncode == 0 and r.stdout.strip().isdigit():
            return nome
    r = subprocess.run(["brightnessctl", "-l"], capture_output=True, text=True, timeout=3, check=False)
    for linha in r.stdout.splitlines():
        if "class 'backlight'" in linha and "Device '" in linha:
            return linha.split("Device '", 1)[1].split("'", 1)[0]
    return None


def _brilho_pct():
    dev = _dispositivo_backlight()
    if not dev:
        return None
    atual = subprocess.run(["brightnessctl", "-d", dev, "get"], capture_output=True, text=True, timeout=3, check=False).stdout.strip()
    maximo = subprocess.run(["brightnessctl", "-d", dev, "info"], capture_output=True, text=True, timeout=3, check=False).stdout
    linhas = [l for l in maximo.splitlines() if "Max brightness" in l]
    if not atual.isdigit() or not linhas:
        return None
    maximo = linhas[0].split(":")[-1].strip()
    if not maximo.isdigit() or int(maximo) <= 0:
        return None
    return round(int(atual) * 100 / int(maximo))


def _definir_brilho_pct(pct):
    dev = _dispositivo_backlight()
    if not dev:
        return False
    r = subprocess.run(["brightnessctl", "-d", dev, "set", f"{pct}%"], capture_output=True, text=True, timeout=5, check=False)
    return r.returncode == 0



_pc_notifs = {"ativo": True, "proc": None, "thread": None}


def _monitorar_notificacoes():
    while True:
        proc = None
        try:
            env = dict(os.environ)
            env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
            proc = subprocess.Popen(
                ["dbus-monitor", "--session", "interface='org.freedesktop.Notifications',type='method_call'"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env
            )
            _pc_notifs["proc"] = proc
            coletando = False
            strings = []
            while _pc_notifs["ativo"]:
                linha = proc.stdout.readline()
                if not linha:
                    break
                if "member=Notify" in linha:
                    coletando = True
                    strings = []
                    continue
                if coletando:
                    if len(strings) > 6:
                        coletando = False
                        strings = []
                        continue
                    m = re.match(r'\s+string "(.*)"', linha)
                    if m:
                        strings.append(m.group(1))
                        if len(strings) == 4:
                            coletando = False
                            app = strings[0]
                            titulo = strings[2]
                            corpo = strings[3]
                            socketio.emit("pc_notificacao", {"app": app, "titulo": titulo, "texto": corpo})
                            strings = []
                            continue
        except Exception:
            pass
        finally:
            _pc_notifs["proc"] = None
            if proc is not None:
                try:
                    proc.terminate()
                except Exception:
                    pass
        time.sleep(3)


@socketio.on("executar_comando")
def executar_comando_catalogo(dados):
    nome = dados.get("nome", "")
    item = None
    for categoria, comandos in CATALOGO_COMANDOS.items():
        item = next((c for c in comandos if c["nome"] == nome), None)
        if item: break
    if item is None:
        emit("resultado_comando", {"ok": False, "erro": "Command not found in catalog"})
        return
    comando = item["comando"]
    try:
        _detectar_env_hypr()
        if comando == "screenshot":
            caminho = PASTA_COMPARTILHADOS / f"screenshot_{int(time.time())}.png"
            subprocess.run(["grim", str(caminho)], timeout=10, check=True)
            emit("resultado_comando", {"ok": True, "erro": ""})
        elif comando == "ip_publico":
            resultado = subprocess.run(["curl", "-fsS", "--max-time", "10", "https://ifconfig.me/ip"], capture_output=True, text=True, timeout=15, check=False)
            ip = resultado.stdout.strip()
            if resultado.returncode != 0 or not ip:
                emit("resultado_comando", {"ok": False, "erro": resultado.stderr.strip() or "Could not get the public IP"})
                return
            relatorio = salvar_relatorio("ip_publico", "IP público", ip)
            emit("resultado_comando", {"ok": True, "erro": "", "saida": f"Public IP: {ip}\nReport: {relatorio.name}"})
        elif comando == "loginctl lock-session":
            subprocess.run(["loginctl", "lock-session"], timeout=5, check=True)
            emit("resultado_comando", {"ok": True, "erro": "Screen locked"})
        elif comando == "hyprctl dispatch lock-screen":
            if not _proc_rodando("hyprlock"):
                subprocess.Popen(["hyprlock"], start_new_session=True)
            subprocess.run(["hyprctl", "dispatch", "lock-screen"], timeout=5, check=True)
            emit("resultado_comando", {"ok": True, "erro": "Screen locked"})
        elif comando == "systemctl suspend":
            subprocess.run(["systemctl", "suspend"], timeout=5, check=True)
            emit("resultado_comando", {"ok": True, "erro": "Suspend started"})
        elif comando == "systemctl poweroff":
            subprocess.run(["systemctl", "poweroff"], timeout=5, check=True)
            emit("resultado_comando", {"ok": True, "erro": "Shutting down..."})
        elif comando == "systemctl reboot":
            subprocess.run(["systemctl", "reboot"], timeout=5, check=True)
            emit("resultado_comando", {"ok": True, "erro": "Rebooting..."})
        elif comando == "hyprctl reload":
            subprocess.run(["hyprctl", "reload"], timeout=5, check=True)
            emit("resultado_comando", {"ok": True, "erro": "WM reloaded"})
        elif comando == "nmcli radio all off":
            subprocess.run(["nmcli", "radio", "all", "off"], timeout=5, check=True)
            emit("resultado_comando", {"ok": True, "erro": "Airplane mode enabled"})
        elif comando == "nmcli radio all on":
            subprocess.run(["nmcli", "radio", "all", "on"], timeout=5, check=True)
            emit("resultado_comando", {"ok": True, "erro": "Radio enabled"})
        elif comando.startswith("diagnostico_"):
            relatorio, codigo_saida, saida = executar_diagnostico(comando)
            if codigo_saida == 0:
                emit("resultado_comando", {"ok": True, "erro": f"Report saved: {relatorio}", "saida": saida})
            else:
                emit("resultado_comando", {"ok": False, "erro": f"Diagnostic exited with code {codigo_saida}. Report: {relatorio}", "saida": saida})
        elif comando == "media_toggle":
            subprocess.run(["playerctl", "play-pause"], timeout=3, check=False)
            emit("resultado_comando", {"ok": True, "erro": "Play/Pause sent"})
        elif comando == "media_next":
            subprocess.run(["playerctl", "next"], timeout=3, check=False)
            emit("resultado_comando", {"ok": True, "erro": "Next track"})
        elif comando == "media_prev":
            subprocess.run(["playerctl", "previous"], timeout=3, check=False)
            emit("resultado_comando", {"ok": True, "erro": "Previous track"})
        elif comando == "volume_up":
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+5%"], timeout=3, check=False)
            emit("resultado_comando", {"ok": True, "erro": "Volume +5%"})
        elif comando == "volume_down":
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-5%"], timeout=3, check=False)
            emit("resultado_comando", {"ok": True, "erro": "Volume -5%"})
        elif comando == "brightness_up":
            antes = _brilho_pct()
            dev = _dispositivo_backlight()
            subprocess.run(["brightnessctl", "-d", dev, "set", "+10%"] if dev else ["brightnessctl", "set", "+10%"], timeout=3, check=False)
            emit("resultado_comando", {"ok": True, "erro": f"Brightness {antes}% -> {_brilho_pct()}%"})
        elif comando == "brightness_down":
            antes = _brilho_pct()
            dev = _dispositivo_backlight()
            subprocess.run(["brightnessctl", "-d", dev, "set", "10%-"] if dev else ["brightnessctl", "set", "10%-"], timeout=3, check=False)
            emit("resultado_comando", {"ok": True, "erro": f"Brightness {antes}% -> {_brilho_pct()}%"})
        elif comando == "night_light_toggle":
            estado = _executar_ii("night_light", not bool(_estado_ii().get("night_light", False)))
            ativo = bool(estado["night_light"])
            socketio.emit("estado_comando", {"comando": comando, "ativo": ativo})
            emit("resultado_comando", {"ok": True, "erro": "Night light ON" if ativo else "Night light OFF"})
        elif comando == "brightness_protection_toggle":
            marcador = ARQUIVO_BRIGHT_PROTECT
            antes = _brilho_pct()
            if marcador.exists():
                try:
                    anterior = int(open(marcador).read().strip())
                except Exception:
                    anterior = 100
                if not 5 <= anterior <= 100:
                    anterior = 100
                ok = _definir_brilho_pct(anterior)
                try:
                    os.remove(marcador)
                except Exception:
                    pass
                depois = _brilho_pct()
                socketio.emit("estado_comando", {"comando": comando, "ativo": False})
                emit("resultado_comando", {
                    "ok": ok,
                    "erro": f"Brightness restored {antes}% -> {depois}%" if ok else "Could not restore brightness",
                    "saida": f"{antes}% -> {depois}%",
                })
            else:
                ok = _definir_brilho_pct(BRILHO_PROTECAO_PCT)
                if ok:
                    try:
                        marcador.parent.mkdir(parents=True, exist_ok=True)
                        with open(marcador, "w") as f:
                            f.write(str(antes if antes is not None else 100))
                    except Exception:
                        _definir_brilho_pct(antes if antes is not None else 100)
                        ok = False
                depois = _brilho_pct()
                socketio.emit("estado_comando", {"comando": comando, "ativo": ok})
                emit("resultado_comando", {
                    "ok": ok,
                    "erro": f"Brightness locked to {depois}% (was {antes}%)" if ok else "Could not adjust brightness",
                    "saida": f"{antes}% -> {depois}%",
                })
        elif comando == "game_mode_toggle":
            estado = _executar_ii("game_mode", not bool(_estado_ii().get("game_mode", False)))
            ativo = bool(estado["game_mode"])
            socketio.emit("estado_comando", {"comando": comando, "ativo": ativo})
            emit("resultado_comando", {"ok": True, "erro": "Game Mode ON" if ativo else "Game Mode OFF"})
        elif comando == "dark_mode_toggle":
            estado = _executar_ii("dark_mode", not bool(_estado_ii().get("dark_mode", False)))
            ativo = bool(estado["dark_mode"])
            socketio.emit("estado_comando", {"comando": comando, "ativo": ativo})
            emit("resultado_comando", {"ok": True, "erro": "Dark mode ON" if ativo else "Light mode ON"})
        elif comando == "bluetooth_toggle":
            atual = _estado_comando(comando)
            subprocess.run(["bluetoothctl", "power", "off" if atual else "on"], timeout=8, check=True)
            estado = _estado_comando(comando)
            socketio.emit("estado_comando", {"comando": comando, "ativo": estado})
            emit("resultado_comando", {"ok": True, "erro": "Bluetooth ON" if estado else "Bluetooth OFF"})
        elif comando == "notifications_toggle_legacy":
            # ativo=True = notificações do PC escondidas/silenciadas.
            # daemon real = quickshell "ii" (~/.config/quickshell/ii/services/Notifications.qml),
            # que lê o marcador /tmp/spook_notif_silent e liga Notifications.silent
            # (mako NÃO roda nesta máquina — nome do bus é do ii desde o boot).
            marcador = "/tmp/spook_notif_silent"
            if os.path.exists(marcador):
                try:
                    os.remove(marcador)
                except Exception:
                    pass
                estado = False
                socketio.emit("estado_comando", {"comando": comando, "ativo": False})
                emit("resultado_comando", {"ok": True, "erro": "PC notifications unmuted"})
            else:
                try:
                    with open(marcador, "w") as f:
                        f.write("1")
                except Exception as e:
                    emit("resultado_comando", {"ok": False, "erro": str(e)})
                    return
                estado = True
                socketio.emit("estado_comando", {"comando": comando, "ativo": True})
                emit("resultado_comando", {"ok": True, "erro": "PC notifications muted"})
        elif comando == "notifications_toggle":
            estado = _executar_ii("notifications", not bool(_estado_ii().get("notifications_silent", False)))
            ativo = bool(estado["notifications_silent"])
            socketio.emit("estado_comando", {"comando": comando, "ativo": ativo})
            emit("resultado_comando", {"ok": True, "erro": "Notifications muted" if ativo else "Notifications active"})
        elif comando == "remote_input":
            emit("resultado_comando", {"ok": True, "erro": "Open the tool in the app Commands screen"})
        elif comando == "keep_awake_toggle":
            estado = _executar_ii("keep_awake", not bool(_estado_ii().get("keep_awake", False)))
            ativo = bool(estado["keep_awake"])
            socketio.emit("estado_comando", {"comando": comando, "ativo": ativo})
            emit("resultado_comando", {"ok": True, "erro": "Keep Awake ON" if ativo else "Keep Awake OFF"})
        else:
            emit("resultado_comando", {"ok": False, "erro": f"Command not implemented: {comando}"})
    except Exception as e:
        emit("resultado_comando", {"ok": False, "erro": str(e)})


def _ydotool_rodavel():
    """ydotool so funciona com o daemon ydotoald (uinput, root).

    Sem o socket ele NAO dá erro: Sai com rc=0 e nao faz nada — por isso o
    touchpad/teclado "nao mexiam" e nao parecia bug. Aqui avisamos de vez.
    """
    socket_path = YENV.get("YDOTOOL_SOCKET", "")
    if socket_path and os.path.exists(socket_path):
        return True
    if not getattr(_ydotool_rodavel, "_avisou", False):
        _ydotool_rodavel._avisou = True
        msg = ("ydotool SEM ydotoald: inicie com "
               "'systemctl --user enable --now ydotool' (uinput/user)")
        print(f"[PC_TECLADO] {msg}", flush=True)
        emit("resultado_comando", {"ok": False, "erro": msg})
    return False


@socketio.on("pc_teclado")
def pc_teclado(dados):
    tipo = dados.get("tipo", "") if dados else ""
    try:
        if tipo == "texto":
            texto = dados.get("texto", "")
            if texto:
                def _t():
                    try:
                        subprocess.run(["wtype", texto], timeout=5, check=False)
                    except Exception:
                        pass
                _input_executor.submit(_t)
        elif not _ydotool_rodavel():
            return
        elif tipo == "backspace":
            def _t():
                try:
                    subprocess.run(["ydotool", "key", "14:1", "14:0"], timeout=3, check=False, env=YENV)
                except Exception:
                    pass
            _input_executor.submit(_t)
        elif tipo == "enter":
            def _t():
                try:
                    subprocess.run(["ydotool", "key", "28:1", "28:0"], timeout=3, check=False, env=YENV)
                except Exception:
                    pass
            _input_executor.submit(_t)
        elif tipo == "tab":
            def _t():
                try:
                    subprocess.run(["ydotool", "key", "15:1", "15:0"], timeout=3, check=False, env=YENV)
                except Exception:
                    pass
            _input_executor.submit(_t)
        elif tipo == "esc":
            def _t():
                try:
                    subprocess.run(["ydotool", "key", "1:1", "1:0"], timeout=3, check=False, env=YENV)
                except Exception:
                    pass
            _input_executor.submit(_t)
    except Exception as e:
        emit("resultado_comando", {"ok": False, "erro": str(e)})


@socketio.on("pc_touchpad")
def pc_touchpad(dados):
    if not dados:
        return
    try:
        tipo = dados.get("tipo", "")
        if tipo != "move" and not _ydotool_rodavel():
            return
        if tipo == "move":
            dx = dados.get("dx", 0)
            dy = dados.get("dy", 0)
            if dx or dy:
                def _m():
                    try:
                        # Movimento relativo (padrão do ydotool)
                        subprocess.run(["ydotool", "mousemove", "-x", str(dx), "-y", str(dy)],
                                       timeout=3, check=False, env=YENV)
                    except Exception:
                        pass
                _input_executor.submit(_m)
        elif tipo == "clique":
            code = {"esquerdo": "0xC0", "direito": "0xC1", "medio": "0xC2"}.get(dados.get("botao", "esquerdo"), "0xC0")
            def _c():
                try:
                    subprocess.run(["ydotool", "click", code], timeout=3, check=False, env=YENV)
                except Exception:
                    pass
            _input_executor.submit(_c)
        elif tipo == "scroll":
            dy = dados.get("dy", 0)
            if dy:
                def _s():
                    try:
                        subprocess.run(["ydotool", "mousemove", "-w", "0", str(dy)], timeout=3, check=False, env=YENV)
                    except Exception:
                        pass
                _input_executor.submit(_s)
    except Exception as e:
        emit("resultado_comando", {"ok": False, "erro": str(e)})


@socketio.on("iniciar_terminal")
def _iniciar_terminal(dados=None):
    """Aba TERMINAL do app: um bash no Arch com stdout via 'saida_terminal'."""
    global sessao_pty
    if sessao_pty.get("fd") is None:
        try:
            _detectar_env_hypr()
            os.environ["TERM"] = "xterm-256color"
            iniciar_sessao_pty()
        except Exception as e:
            print(f"[TERMINAL] nao iniciou: {e}", flush=True)
            emit("terminal_erro", {"erro": str(e)})
            return
    emit("terminal_pronto", {})
    socketio.emit("saida_terminal", {"dados": "\x1b[2mshell do Arch pronta — digite abaixo e ENTER\x1b[0m\n"})


@socketio.on("entrada_terminal")
def _entrada_terminal(dados):
    fd = sessao_pty.get("fd")
    if fd is None:
        emit("terminal_erro", {"erro": "terminal nao iniciado — abra a aba TERMINAL"})
        return
    texto = str((dados or {}).get("dados", ""))
    if not texto:
        return
    try:
        os.write(fd, (texto if texto.endswith("\n") else texto + "\n").encode())
    except OSError as e:
        emit("terminal_erro", {"erro": f"terminal caiu: {e}"})


def iniciar_sessao_pty():
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp("bash", ["bash"])
    else:
        sessao_pty["fd"] = fd
        sessao_pty["pid"] = pid
        thread = threading.Thread(target=ler_saida_pty, args=(fd,), daemon=True)
        thread.start()


def ler_saida_pty(fd):
    while True:
        try:
            pronto, _, _ = select.select([fd], [], [], 0.10)
            if fd in pronto:
                dados = os.read(fd, 4096)
                if not dados: break
                texto = dados.decode("utf-8", errors="replace")
                socketio.emit("saida_terminal", {"dados": texto})
        except OSError: break


def monitorar_clipboard():
    ultimo = pyperclip.paste()
    while True:
        time.sleep(2)
        atual = pyperclip.paste()
        if atual != ultimo:
            ultimo = atual
            if ignorar_proxima_leitura_clipboard["valor"]:
                ignorar_proxima_leitura_clipboard["valor"] = False
                continue
            socketio.emit("clipboard_notebook", {"texto": atual})


def iniciar_relay_bridge():
    """Conecta este servidor Arch ao hub do Redmi como cliente, para sincronismo instantaneo."""
    global relay_bridge_sio
    import socketio as sio_client
    print(f"[BRIDGE] Iniciando conexão com o hub do Redmi em {HUB_URL}...", flush=True)
    sio = sio_client.Client(reconnection_delay=5, reconnection_delay_max=30)
    relay_bridge_sio = sio
    token = obter_token_sessao()
    @sio.on("connect")
    def on_connect():
        print(f"[BRIDGE] Conectado ao hub do Redmi: {HUB_URL}", flush=True)
        try:
            sio.emit("info_arch", coletar_info_servidor())
        except Exception as e:
            print(f"[BRIDGE] info_arch falhou: {e}", flush=True)
    @sio.on("chat_nova")
    def on_chat_nova(dados):
        if dados.get("from") != "arch":
            _emitir_chat_nova(dados)
    @sio.on("chat_apagada")
    def on_chat_apagada(dados):
        socketio.emit("chat_apagada", dados)
    @sio.on("task_nova")
    def on_task_nova(dados):
        socketio.emit("task_nova", dados)
    @sio.on("task_atualizada")
    def on_task_atualizada(dados):
        socketio.emit("task_atualizada", dados)
    @sio.on("task_apagada")
    def on_task_apagada(dados):
        socketio.emit("task_apagada", dados)
    @sio.on("ia_executar_scan")
    def on_ia_executar_scan(dados):
        try:
            scan = (dados or {}).get("scan", "")
            alvo = (dados or {}).get("alvo", "")
            portas = (dados or {}).get("portas", "")
            sessao_id = (dados or {}).get("sessao_id", "")
            pergunta_original = (dados or {}).get("pergunta_original", "")
            print(f"[BRIDGE IA] scan={scan} alvo={alvo}", flush=True)
            resultado = executar_scan_ia(scan, alvo, portas)
            if sio.connected:
                sio.emit("ia_executar_scan", {"sessao_id": sessao_id, "resultado": resultado, "pergunta_original": pergunta_original})
            else:
                print("[BRIDGE IA] relay desconectado, resultado descartado", flush=True)
        except Exception as e:
            print(f"[BRIDGE IA ERRO] ia_executar_scan: {e}", flush=True)
            try:
                if sio.connected:
                    sio.emit("ia_executar_scan", {"sessao_id": (dados or {}).get("sessao_id", ""), "resultado": {"ok": False, "erro": str(e)}, "pergunta_original": (dados or {}).get("pergunta_original", "")})
            except Exception:
                pass
    @sio.on("ia_executar_comando")
    def on_ia_executar_comando(dados):
        try:
            comando = (dados or {}).get("comando", "")
            sessao_id = (dados or {}).get("sessao_id", "")
            pergunta_original = (dados or {}).get("pergunta_original", "")
            print(f"[BRIDGE IA] comando={comando}", flush=True)
            resultado = executar_comando_ia(comando)
            if sio.connected:
                sio.emit("ia_executar_comando", {"sessao_id": sessao_id, "resultado": resultado, "pergunta_original": pergunta_original})
        except Exception as e:
            print(f"[BRIDGE IA ERRO] ia_executar_comando: {e}", flush=True)
            try:
                if sio.connected:
                    sio.emit("ia_executar_comando", {"sessao_id": (dados or {}).get("sessao_id", ""), "resultado": {"ok": False, "erro": str(e)}, "pergunta_original": (dados or {}).get("pergunta_original", "")})
            except Exception:
                pass
    @sio.on("ia_obter_info")
    def on_ia_obter_info(dados):
        try:
            sessao_id = (dados or {}).get("sessao_id", "")
            pergunta_original = (dados or {}).get("pergunta_original", "")
            print(f"[BRIDGE IA] obter_info solic (sessao={sessao_id})", flush=True)
            info = coletar_info_servidor()
            info.update(coletar_info_celular())
            resultado = info
            if sio.connected:
                sio.emit("ia_obter_info", {"sessao_id": sessao_id, "resultado": resultado, "pergunta_original": pergunta_original})
        except Exception as e:
            print(f"[BRIDGE IA ERRO] ia_obter_info: {e}", flush=True)
    def run_bridge():
        while True:
            try:
                if not sio.connected:
                    sio.connect(HUB_URL, auth={"token": token})
                sio.wait()
            except Exception as e:
                print(f"[BRIDGE ERRO] {e}", flush=True)
                time.sleep(10)
    threading.Thread(target=run_bridge, daemon=True).start()


def iniciar_bluetooth_thread():
    def run_bt():
        try:
            import dbus
            import dbus.mainloop.glib
            from gi.repository import GLib
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            bus = dbus.SystemBus()
            perfil = PerfilPentestLink(bus)
            manager = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"), "org.bluez.ProfileManager1")
            opcoes = {"Name": "SPOOK", "Role": "server", "Channel": dbus.UInt16(22), "RequireAuthentication": True, "RequireAuthorization": False, "AutoConnect": False}
            try: manager.UnregisterProfile(CAMINHO_PERFIL)
            except: pass
            manager.RegisterProfile(CAMINHO_PERFIL, UUID_PENTESTLINK, opcoes)
            print("[BLUETOOTH] Perfil SPOOK registrado no BlueZ", flush=True)
            GLib.MainLoop().run()
        except Exception as e:
            print(f"[BLUETOOTH ERRO] Falha ao iniciar: {e}", flush=True)
    threading.Thread(target=run_bt, daemon=True).start()


@app.route("/healthz")
def healthz():
    return "OK", 200

if __name__ == "__main__":
    _detectar_env_hypr()
    if not MODO_CLOUD:
        threading.Thread(target=monitorar_clipboard, daemon=True).start()
        threading.Thread(target=emitir_metricas_periodicas, daemon=True).start()
        threading.Thread(target=monitorar_chat, daemon=True).start()
        threading.Thread(target=monitorar_tasks, daemon=True).start()
        threading.Thread(target=_monitorar_notificacoes, daemon=True).start()
        threading.Thread(target=_loop_localizacao_arch, daemon=True).start()
        iniciar_relay_bridge()
        iniciar_bluetooth_thread()
    print("[HTTP] Servidor HTTP na porta 5000", flush=True)
    socketio.run(app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True, use_reloader=False)

