"""
Ferramentas de acesso à web (busca e leitura de página).

O conteúdo baixado é DADO EXTERNO: volta embrulhado em <untrusted_data> pelo
`execute()` do tools.py e o system prompt proíbe obedecer instruções vindas dele.
"""
import html
import re
import urllib.parse

import requests
from pydantic import BaseModel, Field

from agent.tools import tool

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
TIMEOUT = 20


class BuscaArgs(BaseModel):
    consulta: str = Field(description="termo de busca")
    limite: int = Field(default=5, description="quantidade de resultados (1-10)")


@tool("web_search", "Busca na web (DuckDuckGo) e devolve título, URL e resumo", BuscaArgs, risk="high")
def web_search(a: BuscaArgs):
    limite = max(1, min(int(a.limite or 5), 10))
    r = requests.post(
        "https://html.duckduckgo.com/html/",
        data={"q": a.consulta},
        headers={"User-Agent": UA},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    corpo = r.text
    titulos = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', corpo, re.S)
    resumos = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', corpo, re.S)

    def limpa(t: str) -> str:
        return html.unescape(re.sub(r"<[^>]+>", "", t)).strip()

    saida = []
    for i, (url, titulo) in enumerate(titulos[:limite]):
        link = html.unescape(url)
        if "uddg=" in link:
            link = urllib.parse.unquote(link.split("uddg=")[1].split("&")[0])
        resumo = limpa(resumos[i]) if i < len(resumos) else ""
        saida.append(f"{i + 1}. {limpa(titulo)}\n   {link}\n   {resumo[:300]}")
    return "\n".join(saida) or "nenhum resultado"


class FetchArgs(BaseModel):
    url: str = Field(description="URL http(s) para ler")
    max_chars: int = Field(default=6000)


@tool("fetch_url", "Baixa uma URL e devolve o texto limpo da página", FetchArgs, risk="high")
def fetch_url(a: FetchArgs):
    if not a.url.startswith(("http://", "https://")):
        raise ValueError("use http(s)://")
    r = requests.get(a.url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    tipo = r.headers.get("content-type", "")
    if "html" not in tipo:
        return r.text[: a.max_chars]
    corpo = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", r.text)
    corpo = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", corpo)
    texto = html.unescape(re.sub(r"<[^>]+>", " ", corpo))
    texto = re.sub(r"[ \t\xa0]+", " ", texto)
    texto = re.sub(r"\n\s*\n+", "\n\n", texto)
    return texto.strip()[: a.max_chars]
