"""
Checkpoint: salva estado para retomar após falha.
"""
import json
import pathlib
import uuid

CK_DIR = pathlib.Path("checkpoints")
CK_DIR.mkdir(exist_ok=True)

def save(run_id: str, state: dict):
    (CK_DIR / f"{run_id}.json").write_text(json.dumps(state, ensure_ascii=False, default=str))

def load(run_id: str):
    p = CK_DIR / f"{run_id}.json"
    return json.loads(p.read_text()) if p.exists() else None

def new_id() -> str:
    return uuid.uuid4().hex[:8]