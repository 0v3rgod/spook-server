"""
Memória curta (compactação) + longa (vetorial Chroma).
"""
import json
import time
import uuid
import chromadb
from pathlib import Path
from agent.llm import chat

DB_DIR = Path("./agent_memory").resolve()
DB_DIR.mkdir(parents=True, exist_ok=True)

db = chromadb.PersistentClient(str(DB_DIR))
col = db.get_or_create_collection("memory")

def remember(text: str, kind: str = "fact"):
    """Salva um fato na memória de longo prazo."""
    col.add(
        documents=[text],
        ids=[uuid.uuid4().hex],
        metadatas=[{"kind": kind, "t": time.time()}]
    )

def recall(query: str, k: int = 4) -> list[str]:
    """Busca fatos relevantes."""
    if col.count() == 0:
        return []
    k = min(k, col.count())
    return col.query(query_texts=[query], n_results=k)["documents"][0]

def approx_tokens(msgs: list) -> int:
    return len(json.dumps(msgs, ensure_ascii=False)) // 4

def compact(messages: list, limit: int = 6000, keep: int = 6) -> list:
    """
    Resume o meio da conversa quando ultrapassa o limite de tokens.
    Cuidado: não corta entre tool_call e tool_result.
    """
    if approx_tokens(messages) < limit or len(messages) <= keep + 2:
        return messages
    cut = len(messages) - keep
    while cut < len(messages) and messages[cut].get("role") == "tool":
        cut -= 1
    old, recent = messages[1:cut], messages[cut:]
    summary = chat([
        {"role": "system", "content": "Resuma preservando fatos, decisões, caminhos de arquivos e pendências."},
        {"role": "user", "content": json.dumps(old, ensure_ascii=False)}
    ]).choices[0].message.content
    return [
        messages[0],
        {"role": "system", "content": f"Resumo do que já aconteceu:\n{summary}"},
        *recent
    ]