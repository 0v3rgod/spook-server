"""
Sessões de conversa por usuário — a MESMA memória para o app Android e o
overlay do Arch (ambos enviam `usuario` = "taylor").

Guarda só as últimas trocas (curtas) em disco; o conhecimento durável fica no
Chroma via save_memory/recall.
"""
import json
import os
import threading
from pathlib import Path

DIR = Path(os.getenv("SPOOK_AGENTE_SESSOES", Path.home() / ".local/share/spook-agent/sessoes"))
DIR.mkdir(parents=True, exist_ok=True)
MAX_MSGS = 16  # ~8 trocas
_lock = threading.Lock()


def _arquivo(usuario: str) -> Path:
    seguro = "".join(c for c in (usuario or "anon") if c.isalnum() or c in "-_")[:40] or "anon"
    return DIR / f"{seguro}.json"


def carregar(usuario: str) -> list:
    with _lock:
        try:
            return json.loads(_arquivo(usuario).read_text())
        except Exception:  # noqa: BLE001
            return []


def salvar(usuario: str, mensagens: list) -> None:
    # nunca deixa a lista começar com "tool" (sem o assistant que a pediu)
    corte = mensagens[-MAX_MSGS:]
    while corte and corte[0].get("role") == "tool":
        corte = corte[1:]
    with _lock:
        _arquivo(usuario).write_text(json.dumps(corte, ensure_ascii=False))


def limpar(usuario: str) -> None:
    with _lock:
        _arquivo(usuario).unlink(missing_ok=True)
