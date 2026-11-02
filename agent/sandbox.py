"""
Execução de código Python em container Docker isolado (sem rede, limites de recursos).
"""
import subprocess
import tempfile
import pathlib
from pydantic import BaseModel
from agent.tools import tool

class PyArgs(BaseModel):
    code: str

@tool("run_python_sandbox", "Executa Python isolado (sem rede, 256MB RAM, 10s CPU, 64 pids)", PyArgs, risk="high")
def run_python_sandbox(a: PyArgs) -> str:
    with tempfile.TemporaryDirectory() as d:
        pathlib.Path(d, "main.py").write_text(a.code)
        r = subprocess.run([
            "docker", "run", "--rm",
            "--network=none",
            "--memory=256m",
            "--cpus=0.5",
            "--pids-limit=64",
            "--read-only",
            "--tmpfs", "/tmp",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "-v", f"{d}:/work:ro",
            "-w", "/work",
            "python:3.12-slim",
            "python", "main.py"
        ], capture_output=True, text=True, timeout=15)
        return (r.stdout + r.stderr) or "(sem saída)"