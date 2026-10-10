#!/usr/bin/env python3
"""
Gera .pdf/.docx dos relatorios .txt que ainda nao tem documento e sobe pro Drive.
Resumivel: pula quem ja tem .pdf. Uso: backfill_documentos.py [limite]
"""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import relatorio_doc  # noqa: E402

PASTA = Path.home() / "pentestlink-servidor" / "relatorios"
DRIVE = "gdrive:SPOOK/relatorios"


def main():
    limite = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    pendentes = sorted(p for p in PASTA.glob("*.txt") if not p.with_suffix(".pdf").exists())
    if limite:
        pendentes = pendentes[:limite]
    print("pendentes: %d" % len(pendentes), flush=True)
    ok = falha = 0
    for i, txt in enumerate(pendentes, 1):
        try:
            texto = txt.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            print("[%d] leu mal %s: %s" % (i, txt.name, e), flush=True)
            falha += 1
            continue
        r = relatorio_doc.gerar_documentos(str(PASTA), txt.stem, texto)
        subidos = []
        for caminho in (r.get("pdf"), r.get("docx")):
            if not caminho:
                continue
            nome = os.path.basename(caminho)
            p = subprocess.run(["rclone", "copyto", caminho, "%s/%s" % (DRIVE, nome)],
                               capture_output=True, timeout=180)
            if p.returncode == 0:
                subidos.append(nome)
            else:
                print("   upload falhou %s: %s" % (nome, p.stderr.decode()[:120]), flush=True)
        if subidos:
            ok += 1
        else:
            falha += 1
            print("[%d] SEM DOCUMENTO %s (erros: %s)" % (i, txt.name, r.get("erros")), flush=True)
        if i % 5 == 0 or i == len(pendentes):
            print("  progresso %d/%d  ok=%d falha=%d" % (i, len(pendentes), ok, falha), flush=True)
    print("FIM ok=%d falha=%d" % (ok, falha), flush=True)


if __name__ == "__main__":
    main()
