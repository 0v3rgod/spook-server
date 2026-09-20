#!/usr/bin/env python3

import json
import subprocess
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

BASE = Path.home() / "pentestlink-servidor"
RECEBIDOS = BASE / "recebidos"
COMPARTILHADOS = BASE / "compartilhados"
NOTIFICACOES = BASE / "notificacoes.jsonl"
BATERIA_CELULAR = Path("/tmp/bateria_celular.txt")


class PentestLinkApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="com.taylor.pentestlink.notebook")
        self.bateria = Gtk.Label(label="--%")
        self.status = Gtk.Label(label="Aguardando celular")
        self.arquivos = Gtk.ListBox()
        self.notificacoes = Gtk.ListBox()

    def do_activate(self):
        janela = Gtk.ApplicationWindow(application=self)
        janela.set_title("SPOOK")
        janela.set_default_size(900, 620)

        raiz = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        raiz.set_margin_top(20)
        raiz.set_margin_bottom(20)
        raiz.set_margin_start(20)
        raiz.set_margin_end(20)

        cabecalho = Gtk.Box(spacing=12)
        titulo = Gtk.Label(label="SPOOK")
        titulo.add_css_class("title-1")
        titulo.set_hexpand(True)
        titulo.set_halign(Gtk.Align.START)
        atualizar = Gtk.Button(label="Atualizar")
        atualizar.connect("clicked", lambda *_: self.atualizar())
        cabecalho.append(titulo)
        cabecalho.append(atualizar)
        raiz.append(cabecalho)

        grade_status = Gtk.Grid(column_spacing=16, row_spacing=8)
        grade_status.attach(Gtk.Label(label="Celular", xalign=0), 0, 0, 1, 1)
        grade_status.attach(self.status, 1, 0, 1, 1)
        grade_status.attach(Gtk.Label(label="Bateria", xalign=0), 0, 1, 1, 1)
        grade_status.attach(self.bateria, 1, 1, 1, 1)
        raiz.append(grade_status)

        paginas = Gtk.Notebook()
        paginas.append_page(self.criar_pagina_arquivos(), Gtk.Label(label="Arquivos"))
        paginas.append_page(self.criar_pagina_notificacoes(), Gtk.Label(label="Notificações"))
        paginas.append_page(self.criar_pagina_midia(), Gtk.Label(label="Mídia"))
        raiz.append(paginas)

        janela.set_child(raiz)
        janela.present()
        self.atualizar()
        GLib.timeout_add_seconds(3, self.atualizar)

    def criar_pagina_arquivos(self):
        caixa = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        caixa.append(Gtk.Label(label="Arquivos recebidos do celular e itens disponíveis para download.", xalign=0))
        rolagem = Gtk.ScrolledWindow()
        rolagem.set_vexpand(True)
        rolagem.set_child(self.arquivos)
        caixa.append(rolagem)
        return caixa

    def criar_pagina_notificacoes(self):
        caixa = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        caixa.append(Gtk.Label(label="Notificações recebidas do celular.", xalign=0))
        rolagem = Gtk.ScrolledWindow()
        rolagem.set_vexpand(True)
        rolagem.set_child(self.notificacoes)
        caixa.append(rolagem)
        return caixa

    def criar_pagina_midia(self):
        caixa = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        caixa.set_valign(Gtk.Align.START)
        caixa.append(Gtk.Label(label="Controles de mídia deste notebook.", xalign=0))
        linha = Gtk.Box(spacing=8)
        for texto, comando in [
            ("Anterior", ["playerctl", "previous"]),
            ("Play/Pause", ["playerctl", "play-pause"]),
            ("Próxima", ["playerctl", "next"]),
            ("Volume −", ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-5%"]),
            ("Volume +", ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+5%"]),
        ]:
            botao = Gtk.Button(label=texto)
            botao.connect("clicked", self.executar, comando)
            linha.append(botao)
        caixa.append(linha)
        return caixa

    def executar(self, _, comando):
        try:
            subprocess.run(comando, timeout=3, check=False)
        except OSError:
            pass

    def limpar_lista(self, lista):
        filho = lista.get_first_child()
        while filho:
            proximo = filho.get_next_sibling()
            lista.remove(filho)
            filho = proximo

    def atualizar(self):
        nivel = BATERIA_CELULAR.read_text().strip() if BATERIA_CELULAR.exists() else "--"
        self.bateria.set_label(f"{nivel}%")
        self.status.set_label("Conectado recentemente" if BATERIA_CELULAR.exists() else "Aguardando conexão")

        self.limpar_lista(self.arquivos)
        for pasta, descricao in [(RECEBIDOS, "Recebido"), (COMPARTILHADOS, "Compartilhado")]:
            for arquivo in sorted(pasta.glob("*"), key=lambda item: item.stat().st_mtime, reverse=True):
                if arquivo.is_file():
                    linha = Gtk.Label(label=f"{descricao}  ·  {arquivo.name}", xalign=0)
                    linha.set_margin_top(6)
                    linha.set_margin_bottom(6)
                    self.arquivos.append(linha)

        self.limpar_lista(self.notificacoes)
        if NOTIFICACOES.exists():
            linhas = NOTIFICACOES.read_text(encoding="utf-8").splitlines()[-50:]
            for linha in reversed(linhas):
                try:
                    aviso = json.loads(linha)
                    texto = f"{aviso.get('timestamp', '')}  ·  {aviso.get('app', '')}\n{aviso.get('titulo', '')}\n{aviso.get('texto', '')}"
                    item = Gtk.Label(label=texto, xalign=0)
                    item.set_wrap(True)
                    item.set_margin_top(8)
                    item.set_margin_bottom(8)
                    self.notificacoes.append(item)
                except json.JSONDecodeError:
                    continue
        return True


if __name__ == "__main__":
    PentestLinkApp().run(None)
