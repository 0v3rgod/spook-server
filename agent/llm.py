"""
Cliente Ollama (API OpenAI-compatível) com retry, fallback de modelos e
parser de tool-calls textuais (alguns modelos devolvem a chamada como texto
em vez de `tool_calls` estruturado).
"""
import json
import os
import re
import uuid

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODELS = [
    m.strip()
    for m in os.getenv("OLLAMA_MODELS", "qwen2.5-coder:7b").split(",")
    if m.strip()
]

client = OpenAI(base_url=f"{OLLAMA_HOST}/v1", api_key=os.getenv("OLLAMA_API_KEY", "ollama"))


class _Fn:
    """Objeto compatível com `message.tool_calls[i].function`."""

    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class _Call:
    """Objeto compatível com `message.tool_calls[i]`."""

    def __init__(self, name: str, arguments: str):
        self.id = f"call_{uuid.uuid4().hex[:12]}"
        self.type = "function"
        self.function = _Fn(name, arguments)

    def model_dump(self, **_):
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.function.name, "arguments": self.function.arguments},
        }


def _extrai_json(texto: str):
    """Extrai o primeiro objeto JSON balanceado do texto."""
    texto = texto.strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```[a-zA-Z]*\s*", "", texto)
        texto = re.sub(r"```\s*$", "", texto).strip()
    i = texto.find("{")
    if i < 0:
        return None
    prof = 0
    em_str = False
    esc = False
    for j in range(i, len(texto)):
        ch = texto[j]
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            em_str = not em_str
            continue
        if em_str:
            continue
        if ch == "{":
            prof += 1
        elif ch == "}":
            prof -= 1
            if prof == 0:
                try:
                    return json.loads(texto[i : j + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _normaliza_chamada(obj, nomes_validos: set):
    """Converte formatos usuais de tool-call em (nome, argumentos_json)."""
    if not isinstance(obj, dict):
        return None
    if "tool_call" in obj and isinstance(obj["tool_call"], dict):
        obj = obj["tool_call"]
    nome = obj.get("name") or obj.get("tool") or obj.get("function")
    args = obj.get("arguments", obj.get("parameters", obj.get("args", {})))
    if isinstance(nome, dict):  # formato {"function": {"name": ..., "arguments": ...}}
        args = nome.get("arguments", args)
        nome = nome.get("name")
    if not isinstance(nome, str) or not nome:
        return None
    if nomes_validos and nome not in nomes_validos:
        return None
    if isinstance(args, str):
        try:
            json.loads(args)
            args_str = args
        except json.JSONDecodeError:
            args_str = json.dumps({"input": args})
    else:
        args_str = json.dumps(args or {}, ensure_ascii=False)
    return nome, args_str


def resgata_tool_calls(resp, tools: list | None):
    """
    Se o modelo devolveu a chamada como TEXTO, transforma em `tool_calls`
    estruturado (in-place). Devolve True se alterou.
    """
    if not tools:
        return False
    msg = resp.choices[0].message
    if getattr(msg, "tool_calls", None):
        return False
    texto = (msg.content or "").strip()
    if not texto or "{" not in texto:
        return False

    nomes = {t["function"]["name"] for t in tools}
    obj = _extrai_json(texto)
    if obj is None:
        return False

    # Lista de chamadas {"tool_calls": [...]}
    candidatos = []
    if "tool_calls" in obj and isinstance(obj["tool_calls"], list):
        candidatos = obj["tool_calls"]
    elif isinstance(obj.get("tool_call"), list):
        candidatos = obj["tool_call"]
    elif isinstance(obj.get("actions"), list):
        candidatos = obj["actions"]
    else:
        candidatos = [obj]

    calls = []
    for c in candidatos:
        normalizada = _normaliza_chamada(c, nomes)
        if normalizada:
            calls.append(_Call(*normalizada))
    if not calls:
        return False

    msg.tool_calls = calls
    msg.content = None
    return True


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=20), reraise=True)
def _call(model: str, messages: list, tools: list = None, **kw):
    return client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools or None,
        temperature=kw.pop("temperature", 0.2),
        **kw,
    )


def chat(messages: list, tools: list = None, **kw):
    """Tenta cada modelo em ordem até um funcionar."""
    ultimo = None
    for modelo in OLLAMA_MODELS:
        try:
            resp = _call(modelo, messages, tools, **kw)
            resgata_tool_calls(resp, tools)
            return resp
        except Exception as e:  # noqa: BLE001
            ultimo = e
    raise ultimo


def modelo_atual() -> str:
    return OLLAMA_MODELS[0]


def health() -> bool:
    try:
        return client.models.list() is not None
    except Exception:  # noqa: BLE001
        return False
