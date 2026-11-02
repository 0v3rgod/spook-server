"""
Ponto de entrada único do agente: usado pelo servidor (socket /ia), pelo
bridge do relay e pelos testes de linha de comando.
"""
import threading

import agent.sandbox  # noqa: F401  (registra run_python_sandbox)
import agent.web  # noqa: F401  (registra web_search / fetch_url)
from agent import sessao
from agent.agent import SYSTEM, Budget, run_agent
from agent.llm import health, modelo_atual

# Ferramentas expostas ao app. Pentest fica no Arch; leitura/escrita de arquivo
# fica FORA (o app não precisa e reduz risco).
FERRAMENTAS_APP = [
    "shell",
    "list_dir",
    "run_nmap",
    "run_gobuster",
    "run_nikto",
    "run_hydra",
    "run_sqlmap",
    "run_ssh",
    "web_search",
    "fetch_url",
    "save_memory",
    "search_memory",
]

_trava = threading.Lock()  # 1 GPU só: uma execução por vez


def disponivel() -> bool:
    return health()


def info() -> dict:
    return {"modelo": modelo_atual(), "online": disponivel(), "ferramentas": FERRAMENTAS_APP}


def perguntar(
    pergunta: str,
    usuario: str = "taylor",
    ferramentas: list | None = None,
    passos: int = 12,
    segundos: int = 150,
) -> str:
    """
    Responde uma pergunta usando o agente local, mantendo a conversa por
    `usuario` (compartilhada entre Android e overlay).
    """
    pergunta = (pergunta or "").strip()
    if not pergunta:
        return "Envie uma pergunta."

    historico = sessao.carregar(usuario)
    ferramentas = ferramentas or FERRAMENTAS_APP
    orcamento = Budget(steps=passos, seconds=segundos)

    with _trava:
        resposta, mensagens = run_agent(
            task=pergunta,
            tools=ferramentas,
            budget=orcamento,
            messages=historico or None,
        )
        historico_novo = [m for m in mensagens if m.get("role") != "system"]
        sessao.salvar(usuario, historico_novo)
    return resposta


def resetar(usuario: str = "taylor") -> None:
    sessao.limpar(usuario)
