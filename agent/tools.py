"""
Registry de ferramentas com validação Pydantic, aprovação humana e sandbox.
"""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Callable, Type
from pydantic import BaseModel, ValidationError, Field

MAX_OUT = 4000
REGISTRY: dict[str, "Tool"] = {}

WORKSPACE = Path("./workspace").resolve()
WORKSPACE.mkdir(parents=True, exist_ok=True)

# Wordlists próprias do SPOOK (baixadas uma vez em ~/.local/share/spook-agent/wordlists)
WORDLIST_DIR = Path(
    os.getenv("SPOOK_WORDLISTS", Path.home() / ".local/share/spook-agent/wordlists")
)
WORDLIST_PADRAO = WORDLIST_DIR / "common.txt"

def safe_path(p: str) -> Path:
    full = (WORKSPACE / p).resolve()
    if not full.is_relative_to(WORKSPACE):
        raise ValueError("caminho fora do workspace")
    return full

class Tool:
    def __init__(self, name: str, desc: str, schema: Type[BaseModel], fn: Callable, risk: str = "low"):
        self.name, self.desc, self.schema, self.fn, self.risk = name, desc, schema, fn, risk
    
    def spec(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.desc,
                "parameters": self.schema.model_json_schema()
            }
        }

def tool(name: str, desc: str, schema: Type[BaseModel], risk: str = "low"):
    def deco(fn: Callable):
        REGISTRY[name] = Tool(name, desc, schema, fn, risk)
        return fn
    return deco

def specs(names: list = None) -> list:
    return [t.spec() for n, t in REGISTRY.items() if names is None or n in names]

def _modo_aprovacao() -> str:
    """
    ask  -> pergunta no terminal (default quando há TTY)
    auto -> aprova sozinho (usado pelo bridge/app; permite o agent agir)
    deny -> nega ferramentas de risco alto
    """
    m = os.getenv("SPOOK_APROVACAO")
    if m:
        return m.strip().lower()
    return "ask" if sys.stdin.isatty() else "deny"


def ask_human(name: str, args: BaseModel) -> bool:
    modo = _modo_aprovacao()
    if modo == "auto":
        return True
    if modo == "deny":
        return False
    print(f"\n⚠️  {name}({args.model_dump()})")
    try:
        return input("Aprovar? [s/N] ").strip().lower() == "s"
    except EOFError:
        return False

def execute(name: str, raw_args: str) -> str:
    t = REGISTRY.get(name)
    if not t:
        return f"ERRO: ferramenta '{name}' não existe. Disponíveis: {list(REGISTRY)}"
    try:
        args = t.schema.model_validate_json(raw_args or "{}")
    except ValidationError as e:
        return f"ERRO de validação (corrija os argumentos): {e}"
    if t.risk == "high" and not ask_human(name, args):
        return "NEGADO pelo usuário. Proponha outra abordagem."
    try:
        out = str(t.fn(args))
    except Exception as e:
        return f"ERRO ao executar: {type(e).__name__}: {e}"
    return f"<untrusted_data>\n{out[:MAX_OUT]}\n</untrusted_data>"

# --- Ferramentas de arquivo ---
class ReadArgs(BaseModel):
    path: str = Field(description="caminho relativo ao workspace")

@tool("read_file", "Lê um arquivo de texto do workspace", ReadArgs)
def read_file(a: ReadArgs):
    return safe_path(a.path).read_text()

class WriteArgs(BaseModel):
    path: str
    content: str

@tool("write_file", "Escreve/sobrescreve um arquivo no workspace", WriteArgs, risk="high")
def write_file(a: WriteArgs):
    p = safe_path(a.path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(a.content)
    return f"ok: {a.path}"

class ListArgs(BaseModel):
    path: str = Field(default=".", description="diretório relativo ao workspace")

@tool("list_dir", "Lista arquivos no workspace", ListArgs)
def list_dir(a: ListArgs):
    p = safe_path(a.path)
    return "\n".join(f"{'d' if f.is_dir() else '-'} {f.name}" for f in p.iterdir())

# --- Ferramentas de shell (allowlist) ---
class ShellArgs(BaseModel):
    cmd: str

ALLOWED = {"ls", "cat", "grep", "head", "tail", "wc", "pwd", "find", "ps", "netstat", "ss", "ip", "whoami", "id", "df", "free", "uname", "which", "file", "stat", "tar", "gzip", "gunzip", "echo", "nvidia-smi", "lscpu", "lsblk", "date", "uptime", "hostname", "nproc", "tailscale", "hyprctl", "journalctl", "ping", "dig", "nslookup", "whois", "curl"}

_SEPARADORES = {"&&", "||", "|", ";", "&"}


def _valida_pipeline(cmd: str) -> None:
    """Garante que cada comando do pipeline/encadeamento está na allowlist."""
    try:
        partes = shlex.split(cmd, posix=True)
    except ValueError as e:
        raise ValueError(f"comando inválido: {e}")
    if not partes:
        raise ValueError("comando vazio")
    # tokens que redirecionam/expandem são bloqueados
    for p in partes:
        if any(c in p for c in "><`$()"):
            raise ValueError(f"metacaractere não permitido: {p}")
    # cada segmento precisa começar num binário da allowlist
    segmento = []
    segmentos = []
    for p in partes:
        if p in _SEPARADORES:
            if not segmento:
                raise ValueError("operador sem comando")
            segmentos.append(segmento)
            segmento = []
        else:
            segmento.append(p)
    if segmento:
        segmentos.append(segmento)
    for seg in segmentos:
        if seg[0] not in ALLOWED:
            raise ValueError(f"comando fora da allowlist: {seg[0]}")


@tool("shell", "Executa comando da allowlist", ShellArgs, risk="high")
def shell(a: ShellArgs):
    _valida_pipeline(a.cmd)
    r = subprocess.run(
        ["bash", "-c", a.cmd], capture_output=True, text=True, timeout=30, cwd=WORKSPACE
    )
    saida = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        return f"[exit {r.returncode}]\n{saida}"
    return saida or "(sem saída)"

CAP_SCAN = 12000


def _run(cmd: list[str], timeout: int = 300, cwd=None) -> str:
    """Executa sem shell (sem injeção) e devolve stdout+stderr."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except FileNotFoundError:
        return f"ERRO: '{cmd[0]}' não está instalado nesta máquina"
    except subprocess.TimeoutExpired:
        return f"ERRO: timeout de {timeout}s"
    saida = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        return f"[exit {r.returncode}]\n{saida or '(sem saída)'}"
    return (saida or "(sem saída)")[:CAP_SCAN]


def _host(valor: str) -> str:
    v = (valor or "").strip()
    if not v or len(v) > 255:
        raise ValueError("alvo vazio ou longo demais")
    if any(c in v for c in " \t\n;|&$`<>()'\"\\"):
        raise ValueError(f"alvo inválido: {v!r}")
    return v


def _flags(valor: str) -> list[str]:
    """Quebra as flags em tokens e recusa metacaracteres de shell."""
    valor = (valor or "").strip()
    if not valor:
        return []
    if any(c in valor for c in ";&|`$><\n()"):
        raise ValueError(f"flags inválidas: {valor!r}")
    return shlex.split(valor)


class NmapArgs(BaseModel):
    target: str = Field(description="IP, host ou range CIDR")
    flags: str = Field(default="-sV -T4 --top-ports 200", description="flags do nmap")


@tool("run_nmap", "Escaneia portas/serviços com nmap", NmapArgs, risk="high")
def run_nmap(a: NmapArgs):
    cmd = ["nmap", *_flags(a.flags), _host(a.target)]
    return _run(cmd, timeout=420)


class GobusterArgs(BaseModel):
    target: str = Field(description="URL base (ex: http://192.168.0.1)")
    wordlist: str = Field(default="", description="deixe vazio para a wordlist padrão do SPOOK")
    mode: str = Field(default="dir", description="dir|dns|vhost|fuzz")


@tool("run_gobuster", "Enumera diretórios/DNS/vhosts com gobuster", GobusterArgs, risk="high")
def run_gobuster(a: GobusterArgs):
    modo = (a.mode or "dir").strip().lower()
    if modo not in {"dir", "dns", "vhost", "fuzz"}:
        raise ValueError("modo inválido (dir|dns|vhost|fuzz)")
    wordlist = a.wordlist.strip() or str(WORDLIST_PADRAO)
    if not Path(wordlist).is_file():
        raise ValueError(f"wordlist não encontrada: {wordlist}")
    alvo = _host(a.target)
    if modo == "dir":
        cmd = ["gobuster", "dir", "-u", alvo, "-w", wordlist, "-q", "--no-error"]
    elif modo == "dns":
        cmd = ["gobuster", "dns", "-d", alvo, "-w", wordlist, "-q", "--no-error"]
    elif modo == "vhost":
        cmd = ["gobuster", "vhost", "-u", alvo, "-w", wordlist, "-q", "--no-error"]
    else:
        cmd = ["gobuster", "fuzz", "-u", alvo, "-w", wordlist, "-q", "--no-error"]
    return _run(cmd, timeout=420)


class NiktoArgs(BaseModel):
    target: str = Field(description="URL ou host alvo")
    flags: str = Field(default="-nointeractive -maxtime 180s")


@tool("run_nikto", "Scan de vulnerabilidades web com nikto", NiktoArgs, risk="high")
def run_nikto(a: NiktoArgs):
    cmd = ["nikto", "-h", _host(a.target), *_flags(a.flags)]
    return _run(cmd, timeout=420)


class HydraArgs(BaseModel):
    target: str = Field(description="IP ou host")
    service: str = Field(description="ssh|ftp|http-get|smb|...")
    user: str = Field(default="", description="usuário único; vazio usa a wordlist de usuários")
    userlist: str = Field(default="", description="wordlist de usuários (se user vazio)")
    wordlist: str = Field(default="", description="wordlist de senhas")
    port: int = Field(default=0, description="porta (0 = padrão do serviço)")


@tool("run_hydra", "Força bruta com hydra (só em alvo autorizado)", HydraArgs, risk="high")
def run_hydra(a: HydraArgs):
    wordlist = a.wordlist.strip() or str(WORDLIST_PADRAO)
    if not Path(wordlist).is_file():
        raise ValueError(f"wordlist não encontrada: {wordlist}")
    cmd = ["hydra"]
    if a.user.strip():
        cmd += ["-l", a.user.strip()]
    else:
        ul = a.userlist.strip()
        if not ul or not Path(ul).is_file():
            raise ValueError("informe 'user' ou uma 'userlist' válida")
        cmd += ["-L", ul]
    cmd += ["-P", wordlist, "-f", "-I"]
    if a.port:
        cmd += ["-s", str(int(a.port))]
    cmd += [_host(a.target), a.service.strip()]
    return _run(cmd, timeout=600)


class SqlmapArgs(BaseModel):
    target: str = Field(description="URL com parâmetro (ex: http://site/pagina?id=1)")
    flags: str = Field(default="--batch --level 2 --risk 1")


@tool("run_sqlmap", "Testa SQL injection com sqlmap", SqlmapArgs, risk="high")
def run_sqlmap(a: SqlmapArgs):
    cmd = ["sqlmap", "-u", a.target.strip(), *_flags(a.flags)]
    return _run(cmd, timeout=600)


class MsFArgs(BaseModel):
    module: str = Field(description="ex: exploit/windows/smb/ms17_010_eternalblue")
    options: str = Field(default="", description="RHOSTS=10.0.0.1 RPORT=445 PAYLOAD=...")
    timeout: int = Field(default=300)


@tool("run_msfconsole", "Executa módulo do Metasploit", MsFArgs, risk="high")
def run_msfconsole(a: MsFArgs):
    if any(c in a.module + a.options for c in ";&|`$\n"):
        raise ValueError("module/options com caracteres inválidos")
    if not Path("/usr/bin/msfconsole").exists():
        return "ERRO: msfconsole não está instalado (sudo pacman -S metasploit)"
    script = f"use {a.module}; " + "".join(
        f"set {par.strip()}; " for par in a.options.split() if "=" in par
    ) + "run -z; exit"
    return _run(["msfconsole", "-q", "-x", script], timeout=int(a.timeout))


class SshArgs(BaseModel):
    target: str = Field(description="user@host")
    command: str = Field(description="comando remoto")
    key: str = Field(default="", description="caminho da chave SSH (opcional)")


@tool("run_ssh", "Executa comando via SSH", SshArgs, risk="high")
def run_ssh(a: SshArgs):
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    if a.key.strip():
        if not Path(a.key).is_file():
            raise ValueError(f"chave não encontrada: {a.key}")
        cmd += ["-i", a.key]
    cmd += [_host(a.target), a.command]
    return _run(cmd, timeout=120)

# --- Memória ---
class MemArgs(BaseModel):
    text: str

@tool("save_memory", "Salva um fato durável para usar em tarefas futuras", MemArgs)
def save_memory(a: MemArgs):
    from agent.memory import remember
    remember(a.text)
    return "salvo"

class RecallArgs(BaseModel):
    query: str
    k: int = 4

@tool("search_memory", "Busca fatos salvos anteriormente", RecallArgs)
def search_memory(a: RecallArgs):
    from agent.memory import recall
    return "\n".join(recall(a.query, a.k)) or "nada encontrado"