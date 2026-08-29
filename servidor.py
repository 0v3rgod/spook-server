from flask import Flask, request
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
import select
import fcntl
import struct
import termios
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

try:
    from servidor_bluetooth import CAMINHO_PERFIL, UUID_PENTESTLINK, PerfilPentestLink
except Exception:
    CAMINHO_PERFIL = UUID_PENTESTLINK = PerfilPentestLink = None

app = Flask(__name__)
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
        {"nome": "Lock Screen", "comando": "hyprctl dispatch lock-screen", "icone": "lock", "confirmar": False},
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
        {"nome": "Play/Pause", "comando": "media_toggle", "icone": "play", "confirmar": False},
        {"nome": "Próxima Faixa", "comando": "media_next", "icone": "skip_next", "confirmar": False},
        {"nome": "Faixa Anterior", "comando": "media_prev", "icone": "skip_prev", "confirmar": False},
        {"nome": "Aumentar Volume", "comando": "volume_up", "icone": "volume_up", "confirmar": False},
        {"nome": "Diminuir Volume", "comando": "volume_down", "icone": "volume_down", "confirmar": False},
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
        "Você é SPOOK, assistente técnico do app SPOOK. Responda em português, de forma clara e concisa. "
        "Nunca execute ações, comandos ou mudanças no sistema; apenas explique e sugira. "
        "Quando o usuário pedir o que fazer, escolha SOMENTE itens destas listas e diga em qual aba usar: Scans ou Notebook. "
        "Não invente scans ou comandos que não estejam abaixo.\n\n"
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


@socketio.on("chat_ia_perguntar")
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
    print(f"[CONNECT] Auth recebido: {auth}")
    if not validar_conexao_socket(auth, "connect"):
        print(f"[ERRO] Token inválido ou ausente. Auth: {auth}")
        return False
    print("[CONECTADO] Celular via WebSocket (token válido).")
    metricas = coletar_metricas()
    emit("bateria_notebook", {"nivel": metricas["bateria"]})
    emit("metricas_notebook", metricas)
    marcar_pareamento_confirmado()


@socketio.on("disconnect")
def ao_desconectar():
    print("[DESCONECTADO] Celular desconectou.")


@socketio.on_error_default
def erro_padrao(e):
    print(f"[ERRO SOCKET.IO] {e}")


@socketio.on("bateria_celular")
def bateria_recebida(dados):
    nivel = dados.get("nivel")
    bateria_celular["nivel"] = nivel
    print(f"Bateria do celular: {nivel}%")

    with open("/tmp/bateria_celular.txt", "w") as f:
        f.write(str(nivel))

    try:
        subprocess.run(["notify-send", "SPOOK", f"Bateria do celular: {nivel}%"])
    except FileNotFoundError:
        pass


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

    try:
        subprocess.run(["notify-send", f"{titulo} ({app_origem})", texto])
    except FileNotFoundError:
        pass


@socketio.on("enviar_arquivo")
def receber_arquivo(dados):
    nome_seguro = "arquivo_sem_nome"
    try:
        nome_seguro = nome_arquivo_seguro(dados.get("nome", nome_seguro))
        conteudo_bytes = base64.b64decode(dados.get("conteudo", ""), validate=True)
        if not conteudo_bytes:
            raise ValueError("Arquivo vazio")
        resposta = requisicao_filebrowser(
            "POST",
            f"{nome_seguro}?override=true",
            data=conteudo_bytes,
            headers={"Content-Type": "application/octet-stream"},
        )
        resposta.raise_for_status()
        print(f"Arquivo enviado ao File Browser: {nome_seguro} ({len(conteudo_bytes)} bytes)")
        emit("arquivo_recebido_confirmacao", {"nome": nome_seguro, "ok": True})
        try:
            subprocess.run(["notify-send", "SPOOK", f"Arquivo enviado: {nome_seguro}"])
        except FileNotFoundError:
            pass
    except Exception as e:
        emit("arquivo_recebido_confirmacao", {"nome": nome_seguro, "ok": False, "erro": str(e)})


@socketio.on("listar_compartilhados")
def listar_arquivos_compartilhados(dados=None):
    try:
        resposta = requisicao_filebrowser("GET", "")
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
        resposta = requisicao_filebrowser("GET", nome_seguro)
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
            }
            for c in comandos
        ]
    emit("lista_comandos", {"comandos": lista})


@socketio.on("executar_comando")
def executar_comando_catalogo(dados):
    nome = dados.get("nome", "")
    item = None
    
    for categoria, comandos in CATALOGO_COMANDOS.items():
        item = next((c for c in comandos if c["nome"] == nome), None)
        if item:
            break

    if item is None:
        emit("resultado_comando", {"ok": False, "erro": "Comando não encontrado no catálogo"})
        return

    comando = item["comando"]

    try:
        if comando == "screenshot":
            caminho = PASTA_COMPARTILHADOS / f"screenshot_{int(time.time())}.png"
            subprocess.run(["grim", str(caminho)], timeout=10, check=True)
            emit("resultado_comando", {"ok": True, "erro": ""})

        elif comando == "ip_publico":
            resultado = subprocess.run(
                ["curl", "-fsS", "--max-time", "10", "https://ifconfig.me/ip"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            ip = resultado.stdout.strip()
            if resultado.returncode != 0 or not ip:
                emit("resultado_comando", {"ok": False, "erro": resultado.stderr.strip() or "Não foi possível obter o IP público"})
                return
            relatorio = salvar_relatorio("ip_publico", "IP público", ip)
            emit("resultado_comando", {"ok": True, "erro": f"IP: {ip} | Relatório: {relatorio.name}"})

        elif comando == "hyprctl dispatch lock-screen":
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

        else:
            emit("resultado_comando", {"ok": False, "erro": f"Comando não implementado: {comando}"})

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
                if not dados:
                    break
                texto = dados.decode("utf-8", errors="replace")
                socketio.emit("saida_terminal", {"dados": texto})
        except OSError:
            break


@socketio.on("iniciar_terminal")
def rota_iniciar_terminal(dados=None):
    if sessao_pty["fd"] is None:
        iniciar_sessao_pty()
        print("Sessão PTY iniciada.")
    emit("terminal_pronto", {"ok": True})


@socketio.on("entrada_terminal")
def rota_entrada_terminal(dados):
    texto = dados.get("dados", "")
    fd = sessao_pty["fd"]
    if fd is not None:
        try:
            os.write(fd, texto.encode("utf-8"))
        except OSError as e:
            emit("saida_terminal", {"dados": f"\r\n[erro ao escrever no terminal: {e}]\r\n"})


@socketio.on("redimensionar_terminal")
def rota_redimensionar_terminal(dados):
    fd = sessao_pty["fd"]
    if fd is not None:
        colunas = dados.get("colunas", 80)
        linhas = dados.get("linhas", 24)
        try:
            tamanho = struct.pack("HHHH", linhas, colunas, 0, 0)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, tamanho)
        except OSError:
            pass


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


if not MODO_CLOUD:
    threading.Thread(target=monitorar_clipboard, daemon=True).start()
    threading.Thread(target=emitir_metricas_periodicas, daemon=True).start()

def iniciar_bluetooth_thread():
    import threading
    def run_bt():
        try:
            import dbus
            import dbus.mainloop.glib
            from gi.repository import GLib
            
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            bus = dbus.SystemBus()
            perfil = PerfilPentestLink(bus)
            manager = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"), "org.bluez.ProfileManager1")
            opcoes = {
                "Name": "SPOOK",
                "Role": "server",
                "Channel": dbus.UInt16(22),
                "RequireAuthentication": True,
                "RequireAuthorization": False,
                "AutoConnect": False,
            }
            try:
                manager.UnregisterProfile(CAMINHO_PERFIL)
            except dbus.exceptions.DBusException:
                pass
            manager.RegisterProfile(CAMINHO_PERFIL, UUID_PENTESTLINK, opcoes)
            print("[BLUETOOTH] Perfil SPOOK registrado no BlueZ")
            GLib.MainLoop().run()
        except Exception as e:
            print(f"[BLUETOOTH ERRO] Falha ao iniciar: {e}")
            import traceback
            traceback.print_exc()
    
    threading.Thread(target=run_bt, daemon=True).start()


if __name__ == "__main__":
    if not MODO_CLOUD:
        iniciar_bluetooth_thread()
    print("[HTTP] Servidor HTTP na porta 5000")
    socketio.run(app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True, use_reloader=False)
