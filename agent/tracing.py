"""
Log de cada passo do agent (LLM calls, tool calls, resultados) em JSONL.
"""
import json
import time
import uuid
import pathlib

LOG_DIR = pathlib.Path("traces")
LOG_DIR.mkdir(exist_ok=True)

class Trace:
    def __init__(self, task: str):
        self.id = uuid.uuid4().hex[:8]
        self.path = LOG_DIR / f"{self.id}.jsonl"
        self.t0 = time.time()
        self.log("start", task=task)
    
    def log(self, kind: str, **data):
        with self.path.open("a") as f:
            f.write(json.dumps({
                "t": round(time.time() - self.t0, 2),
                "kind": kind,
                **data
            }, ensure_ascii=False, default=str) + "\n")