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
import collections
import requests
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
ignorar_proxima_leitura_clipboard = {"valor": False}

PASTA_RECEBIDOS = Path.home() / "pentestlink-servidor" / "recebidos"
PASTA_COMPARTILHADOS = Path.home() / "pentestlink-servidor" / "compartilhados"
PASTA_RELATORIOS = Path.home() / "pentestlink-servidor" / "relatorios"
RELATORIOS_DRIVE = "relatorios"
ARQUIVO_NOTIFICACOES = Path.home() / "pentestlink-servidor" / "notificacoes.jsonl"
ARQUIVO_CONFIGURACAO = Path.home() / "pentestlink-servidor" / ".env"
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
RELAY_URL = "http://100.70.88.101:5000"
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
        {"nome": "Faixa Anterior", "comando": "media_prev", "icone": "skip_prev", "confirmar": False},
        {"nome": "Play/Pause", "comando": "media_toggle", "icone": "play", "confirmar": False},
        {"nome": "Próxima Faixa", "comando": "media_next", "icone": "skip_next", "confirmar": False},
        {"nome": "Diminuir Volume", "comando": "volume_down", "icone": "volume_down", "confirmar": False},
        {"nome": "Aumentar Volume", "comando": "volume_up", "icone": "volume_up", "confirmar": False},
    ],
    "display": [
        {"nome": "Brilho -", "comando": "brightness_down", "icone": "brightness_low", "confirmar": False},
        {"nome": "Brilho +", "comando": "brightness_up", "icone": "brightness_high", "confirmar": False},
        {"nome": "Luz Noturna", "comando": "night_light_toggle", "icone": "nightlight", "confirmar": False},
        {"nome": "Proteção Brilho", "comando": "brightness_protection_toggle", "icone": "shield", "confirmar": False},
        {"nome": "Modo Jogo", "comando": "game_mode_toggle", "icone": "sports_esports", "confirmar": False},
    ],
    "rede_avancada": [
        {"nome": "Cloudflare WARP", "comando": "warp_toggle", "icone": "cloud", "confirmar": False},
        {"nome": "Notificações", "comando": "notifications_toggle", "icone": "notifications", "confirmar": False},
    ],
    "input": [
        {"nome": "Controle Remoto", "comando": "remote_input", "icone": "keyboard", "confirmar": False},
        {"nome": "Manter Acordado", "comando": "keep_awake_toggle", "icone": "coffee", "confirmar": False},
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
        emit("chat_ia_resposta", {"ok": True, "resposta": conteudo})
    except requests.RequestException as erro:
        emit("chat_ia_resposta", {"ok": False, "resposta": f"Falha na IA: {erro}"})
    except (KeyError, IndexError, TypeError, ValueError):
        emit("chat_ia_resposta", {"ok": False, "resposta": "Resposta inválida da IA."})


def salvar_relatorio(nome, titulo, resultado):
    instante = time.strftime("%Y-%m-%d %H:%M:%S %z")
    arquivo = PASTA_RELATORIOS / f"{nome}_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    arquivo.write_text(f"{titulo}\nGerado: {instante}\n\n{resultado}", encoding="utf-8")
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
        "diagnostico_dns": ("Configuração DNS", ["resolvectl", "status"]),
        "diagnostico_portas_locais": ("Portas locais em escuta", ["ss", "-tulpen"]),
        "diagnostico_tailscale": ("Status Tailscale", ["tailscale", "status"]),
    }
    titulo, argv = acoes[comando]
    resultado = subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False)
    saida = resultado.stdout or resultado.stderr or "Nenhuma saída disponível."
    relatorio = salvar_relatorio(comando, titulo, saida)
    return relatorio.name, resultado.returncode, saida


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
        time.sleep(5)
        metricas = coletar_metricas()
        socketio.emit("metricas_notebook", metricas)


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


@socketio.on("disconnect")
def ao_desconectar():
    print("[DESCONECTADO] Celular desconectou.", flush=True)


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

        # Envia para o Relay da Oracle se estivermos em modo Bridge
        # para que o celular receba instantaneamente
        try:
            if relay_bridge_sio and relay_bridge_sio.connected:
                relay_bridge_sio.emit("chat_enviar", msg)
                print(f"[BRIDGE] Mensagem encaminhada para Oracle: {msg['id']}", flush=True)
            else:
                print(f"[BRIDGE] Alerta: Relay SIO não conectado. Não encaminhado.", flush=True)
        except Exception as e:
            print(f"[BRIDGE] Falha ao encaminhar para Oracle: {e}", flush=True)

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
            # Avisa Oracle
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
        # Avisa Oracle
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
        # Avisa Oracle
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
        # Avisa Oracle
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


@socketio.on("gps_celular")
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


def _estado_comando(comando):
    try:
        if comando == "warp_toggle":
            saida = subprocess.run(["warp-cli", "status"], capture_output=True, text=True, timeout=6, check=False).stdout or ""
            return "Connected" in saida
        if comando == "game_mode_toggle":
            return not _hypr_bool("animations:enabled")
        if comando == "keep_awake_toggle":
            return subprocess.run(["systemctl", "--user", "is-active", "keep-awake.service"], capture_output=True, text=True, timeout=5, check=False).stdout.strip() == "active"
        if comando == "notifications_toggle":
            # ativo = notificações do PC escondidas/silenciadas (marcador do ii)
            return os.path.exists("/tmp/spook_notif_silent")
        if comando == "night_light_toggle":
            return _proc_rodando("hyprsunset")
        if comando == "brightness_protection_toggle":
            return os.path.exists("/tmp/spook_bright_protect")
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
        emit("resultado_comando", {"ok": False, "erro": "Comando não encontrado no catálogo"})
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
                emit("resultado_comando", {"ok": False, "erro": resultado.stderr.strip() or "Não foi possível obter o IP público"})
                return
            relatorio = salvar_relatorio("ip_publico", "IP público", ip)
            emit("resultado_comando", {"ok": True, "erro": "", "saida": f"IP público: {ip}\nRelatório: {relatorio.name}"})
        elif comando == "loginctl lock-session":
            subprocess.run(["loginctl", "lock-session"], timeout=5, check=True)
            emit("resultado_comando", {"ok": True, "erro": "Tela bloqueada"})
        elif comando == "hyprctl dispatch lock-screen":
            if not _proc_rodando("hyprlock"):
                subprocess.Popen(["hyprlock"], start_new_session=True)
            subprocess.run(["hyprctl", "dispatch", "lock-screen"], timeout=5, check=True)
            emit("resultado_comando", {"ok": True, "erro": "Tela bloqueada"})
        elif comando == "systemctl suspend":
            subprocess.run(["systemctl", "suspend"], timeout=5, check=True)
            emit("resultado_comando", {"ok": True, "erro": "Suspend iniciado"})
        elif comando == "systemctl poweroff":
            subprocess.run(["systemctl", "poweroff"], timeout=5, check=True)
            emit("resultado_comando", {"ok": True, "erro": "Desligando..."})
        elif comando == "systemctl reboot":
            subprocess.run(["systemctl", "reboot"], timeout=5, check=True)
            emit("resultado_comando", {"ok": True, "erro": "Reiniciando..."})
        elif comando == "hyprctl reload":
            subprocess.run(["hyprctl", "reload"], timeout=5, check=True)
            emit("resultado_comando", {"ok": True, "erro": "WM recarregado"})
        elif comando == "nmcli radio all off":
            subprocess.run(["nmcli", "radio", "all", "off"], timeout=5, check=True)
            emit("resultado_comando", {"ok": True, "erro": "Modo avião ativado"})
        elif comando == "nmcli radio all on":
            subprocess.run(["nmcli", "radio", "all", "on"], timeout=5, check=True)
            emit("resultado_comando", {"ok": True, "erro": "Rádio ligado"})
        elif comando.startswith("diagnostico_"):
            relatorio, codigo_saida, saida = executar_diagnostico(comando)
            if codigo_saida == 0:
                emit("resultado_comando", {"ok": True, "erro": f"Report saved: {relatorio}", "saida": saida})
            else:
                emit("resultado_comando", {"ok": False, "erro": f"Diagnostic exited with code {codigo_saida}. Report: {relatorio}", "saida": saida})
        elif comando == "media_toggle":
            subprocess.run(["playerctl", "play-pause"], timeout=3, check=False)
            emit("resultado_comando", {"ok": True, "erro": "Play/Pause enviado"})
        elif comando == "media_next":
            subprocess.run(["playerctl", "next"], timeout=3, check=False)
            emit("resultado_comando", {"ok": True, "erro": "Próxima faixa"})
        elif comando == "media_prev":
            subprocess.run(["playerctl", "previous"], timeout=3, check=False)
            emit("resultado_comando", {"ok": True, "erro": "Faixa anterior"})
        elif comando == "volume_up":
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+5%"], timeout=3, check=False)
            emit("resultado_comando", {"ok": True, "erro": "Volume +5%"})
        elif comando == "volume_down":
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-5%"], timeout=3, check=False)
            emit("resultado_comando", {"ok": True, "erro": "Volume -5%"})
        elif comando == "brightness_up":
            subprocess.run(["brightnessctl", "set", "+10%"], timeout=3, check=False)
            emit("resultado_comando", {"ok": True, "erro": "Brilho +10%"})
        elif comando == "brightness_down":
            subprocess.run(["brightnessctl", "set", "10%-"], timeout=3, check=False)
            emit("resultado_comando", {"ok": True, "erro": "Brilho -10%"})
        elif comando == "night_light_toggle":
            if _proc_rodando("hyprsunset"):
                subprocess.run(["pkill", "-x", "hyprsunset"], timeout=3, check=False)
                socketio.emit("estado_comando", {"comando": comando, "ativo": False})
                emit("resultado_comando", {"ok": True, "erro": "Luz noturna desligada"})
            else:
                try:
                    subprocess.Popen(["hyprsunset", "-t", "3500"], start_new_session=True)
                    socketio.emit("estado_comando", {"comando": comando, "ativo": True})
                    emit("resultado_comando", {"ok": True, "erro": "Luz noturna ligada (3500K)"})
                except Exception as e:
                    emit("resultado_comando", {"ok": False, "erro": str(e)})
        elif comando == "brightness_protection_toggle":
            marcador = "/tmp/spook_bright_protect"
            if os.path.exists(marcador):
                try:
                    anterior = open(marcador).read().strip()
                    subprocess.run(["brightnessctl", "set", anterior], timeout=3, check=False)
                except Exception:
                    subprocess.run(["brightnessctl", "set", "50%"], timeout=3, check=False)
                try:
                    os.remove(marcador)
                except Exception:
                    pass
                socketio.emit("estado_comando", {"comando": comando, "ativo": False})
                emit("resultado_comando", {"ok": True, "erro": "Proteção de brilho desligada"})
            else:
                r = subprocess.run(["brightnessctl", "-d", "intel_backlight", "get"], capture_output=True, text=True, timeout=3, check=False)
                try:
                    with open(marcador, "w") as f:
                        f.write(r.stdout.strip() or "9600")
                except Exception:
                    pass
                subprocess.run(["brightnessctl", "set", "15%"], timeout=3, check=False)
                socketio.emit("estado_comando", {"comando": comando, "ativo": True})
                emit("resultado_comando", {"ok": True, "erro": "Proteção de brilho ligada (15%)"})
        elif comando == "game_mode_toggle":
            # Hyprland ≥0.55 usa config Lua; `hyprctl keyword` é inválido.
            atual = _hypr_bool("animations:enabled")
            alvo_lua = "false" if atual else "true"
            subprocess.run(
                ["hyprctl", "eval", f"hl.config({{ animations = {{ enabled = {alvo_lua} }}, decoration = {{ blur = {{ enabled = {alvo_lua} }} }} }})"],
                timeout=3, check=False,
            )
            socketio.emit("estado_comando", {"comando": comando, "ativo": not atual})
            emit("resultado_comando", {"ok": True, "erro": ("Modo jogo ATIVADO (animações desligadas)" if not atual else "Modo jogo DESATIVADO (animações ligadas)")})
        elif comando == "warp_toggle":
            estado = subprocess.run(["warp-cli", "status"], capture_output=True, text=True, timeout=6, check=False).stdout or ""
            if "Connected" in estado:
                subprocess.run(["warp-cli", "disconnect"], timeout=6, check=False)
                socketio.emit("estado_comando", {"comando": comando, "ativo": False})
                emit("resultado_comando", {"ok": True, "erro": "Cloudflare WARP desligado"})
            else:
                subprocess.run(["warp-cli", "connect"], timeout=6, check=False)
                socketio.emit("estado_comando", {"comando": comando, "ativo": True})
                emit("resultado_comando", {"ok": True, "erro": "Cloudflare WARP ligado"})
        elif comando == "notifications_toggle":
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
                emit("resultado_comando", {"ok": True, "erro": "Notificações do PC ativas"})
            else:
                try:
                    with open(marcador, "w") as f:
                        f.write("1")
                except Exception as e:
                    emit("resultado_comando", {"ok": False, "erro": str(e)})
                    return
                estado = True
                socketio.emit("estado_comando", {"comando": comando, "ativo": True})
                emit("resultado_comando", {"ok": True, "erro": "Notificações do PC silenciadas"})
        elif comando == "remote_input":
            emit("resultado_comando", {"ok": True, "erro": "Abra a ferramenta na tela de comandos do app"})
        elif comando == "keep_awake_toggle":
            ativo_atual = subprocess.run(["systemctl", "--user", "is-active", "keep-awake.service"], capture_output=True, text=True, timeout=5, check=False).stdout.strip() == "active"
            subprocess.run(["systemctl", "--user", "stop" if ativo_atual else "start", "keep-awake.service"], timeout=5, check=False)
            estado = subprocess.run(["systemctl", "--user", "is-active", "keep-awake.service"], capture_output=True, text=True, timeout=5, check=False).stdout.strip() == "active"
            socketio.emit("estado_comando", {"comando": comando, "ativo": estado})
            emit("resultado_comando", {"ok": True, "erro": ("Manter acordado ATIVO" if estado else "Manter acordado parado")})
        else:
            emit("resultado_comando", {"ok": False, "erro": f"Comando não implementado: {comando}"})
    except Exception as e:
        emit("resultado_comando", {"ok": False, "erro": str(e)})


@socketio.on("pc_teclado")
def pc_teclado(dados):
    tipo = dados.get("tipo", "") if dados else ""
    try:
        if tipo == "texto":
            texto = dados.get("texto", "")
            if texto:
                subprocess.run(["wtype", texto], timeout=5, check=False)
        elif tipo == "backspace":
            subprocess.run(["ydotool", "key", "14:1", "14:0"], timeout=3, check=False)
        elif tipo == "enter":
            subprocess.run(["ydotool", "key", "28:1", "28:0"], timeout=3, check=False)
        elif tipo == "tab":
            subprocess.run(["ydotool", "key", "15:1", "15:0"], timeout=3, check=False)
        elif tipo == "esc":
            subprocess.run(["ydotool", "key", "1:1", "1:0"], timeout=3, check=False)
    except Exception as e:
        emit("resultado_comando", {"ok": False, "erro": str(e)})


@socketio.on("pc_touchpad")
def pc_touchpad(dados):
    if not dados:
        return
    try:
        tipo = dados.get("tipo", "")
        if tipo == "move":
            dx = dados.get("dx", 0)
            dy = dados.get("dy", 0)
            if dx or dy:
                subprocess.run(["ydotool", "mousemove", "-x", str(dx), "-y", str(dy)], timeout=3, check=False)
        elif tipo == "clique":
            code = {"esquerdo": "0xC0", "direito": "0xC1", "medio": "0xC2"}.get(dados.get("botao", "esquerdo"), "0xC0")
            subprocess.run(["ydotool", "click", code], timeout=3, check=False)
        elif tipo == "scroll":
            dy = dados.get("dy", 0)
            if dy:
                subprocess.run(["ydotool", "mousemove", "-w", "0", str(dy)], timeout=3, check=False)
    except Exception as e:
        emit("resultado_comando", {"ok": False, "erro": str(e)})


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
            pronto, _, _ = select.select([fd], [], [], 1)
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
    """Conecta este servidor Arch ao Oracle Relay como um cliente para sincronismo instantâneo."""
    global relay_bridge_sio
    import socketio as sio_client
    print(f"[BRIDGE] Iniciando conexão com Oracle Relay em {RELAY_URL}...", flush=True)
    sio = sio_client.Client(reconnection_delay=5, reconnection_delay_max=30)
    relay_bridge_sio = sio
    token = obter_token_sessao()
    @sio.on("connect")
    def on_connect():
        print(f"[BRIDGE] Conectado ao Oracle Relay: {RELAY_URL}", flush=True)
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
    def run_bridge():
        while True:
            try:
                if not sio.connected:
                    sio.connect(RELAY_URL, auth={"token": token})
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


if __name__ == "__main__":
    _detectar_env_hypr()
    if not MODO_CLOUD:
        threading.Thread(target=monitorar_clipboard, daemon=True).start()
        threading.Thread(target=emitir_metricas_periodicas, daemon=True).start()
        threading.Thread(target=monitorar_chat, daemon=True).start()
        threading.Thread(target=monitorar_tasks, daemon=True).start()
        threading.Thread(target=_monitorar_notificacoes, daemon=True).start()
        iniciar_relay_bridge()
        iniciar_bluetooth_thread()
    print("[HTTP] Servidor HTTP na porta 5000", flush=True)
    socketio.run(app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True, use_reloader=False)
