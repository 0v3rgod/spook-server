#!/usr/bin/env python3

import json
import os
import socket
import threading

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

from pareamento import obter_token_sessao

UUID_PENTESTLINK = "12345678-1234-1234-1234-123456789012"
CAMINHO_PERFIL = "/org/pentestlink/profile"


class PerfilPentestLink(dbus.service.Object):
    def __init__(self, bus):
        super().__init__(bus, CAMINHO_PERFIL)
        self.rodando = True

    def responder(self, cliente, dados):
        cliente.sendall((json.dumps(dados, ensure_ascii=False) + "\n").encode("utf-8"))

    def processar(self, mensagem, autenticado):
        tipo = mensagem.get("tipo")
        if tipo == "autenticar":
            token = mensagem.get("token", "")
            if token and token == obter_token_sessao():
                return {"ok": True, "tipo": "autenticado"}, True
            return {"ok": False, "erro": "Token inválido"}, False

        if not autenticado:
            return {"ok": False, "erro": "Autenticação necessária"}, False

        if tipo == "status":
            return {
                "ok": True,
                "tipo": "status",
                "mensagem": "Notebook conectado por Bluetooth",
            }, True

        if tipo == "diagnosticos":
            return {
                "ok": True,
                "tipo": "diagnosticos",
                "itens": [
                    "Interfaces de rede",
                    "Rotas locais",
                    "Configuração DNS",
                    "Portas locais em escuta",
                    "Status Tailscale",
                ],
            }, True

        return {"ok": False, "erro": "Mensagem não suportada"}, True

    def lidar_cliente(self, cliente, caminho):
        autenticado = False
        buffer = b""
        try:
            cliente.settimeout(60)
            while self.rodando:
                dados = cliente.recv(4096)
                if not dados:
                    break
                buffer += dados
                while b"\n" in buffer:
                    linha, buffer = buffer.split(b"\n", 1)
                    if not linha:
                        continue
                    try:
                        mensagem = json.loads(linha.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        self.responder(cliente, {"ok": False, "erro": "JSON inválido"})
                        continue
                    resposta, autenticado = self.processar(mensagem, autenticado)
                    self.responder(cliente, resposta)
        except (OSError, socket.timeout) as erro:
            print(f"[BLUETOOTH] Cliente {caminho}: {erro}")
        finally:
            try:
                cliente.close()
            except OSError:
                pass
            print(f"[BLUETOOTH] Cliente desconectado: {caminho}")

    @dbus.service.method("org.bluez.Profile1", in_signature="oha{sv}", out_signature="")
    def NewConnection(self, path, fd, properties):
        print(f"[BLUETOOTH] NewConnection: path={path}, fd={fd}, properties={properties}")
        try:
            nfd = fd.take()
            cliente = socket.fromfd(nfd, socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            os.close(nfd)
            print(f"[BLUETOOTH] Nova conexão aceita: {path}")
            threading.Thread(target=self.lidar_cliente, args=(cliente, str(path)), daemon=True).start()
        except Exception as e:
            print(f"[BLUETOOTH ERRO] NewConnection falhou: {e}")
            import traceback
            traceback.print_exc()

    @dbus.service.method("org.bluez.Profile1", in_signature="o", out_signature="")
    def RequestDisconnection(self, path):
        print(f"[BLUETOOTH] Desconexão solicitada: {path}")

    @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
    def Release(self):
        print("[BLUETOOTH] Perfil liberado")
        self.rodando = False


def iniciar():
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    perfil = PerfilPentestLink(bus)
    manager = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"), "org.bluez.ProfileManager1")
    opcoes = {
        "Name": "SPOOK",
        "Role": "server",
        "Channel": dbus.UInt16(22),
        "RequireAuthentication": True,
        "RequireAuthorization": False,
        "AutoConnect": False,
    }
    try:
        manager.UnregisterProfile(CAMINHO_PERFIL)
    except dbus.exceptions.DBusException:
        pass
    manager.RegisterProfile(CAMINHO_PERFIL, UUID_PENTESTLINK, opcoes)
    print("[BLUETOOTH] Perfil SPOOK registrado no BlueZ")
    GLib.MainLoop().run()


if __name__ == "__main__":
    try:
        iniciar()
    except KeyboardInterrupt:
        print("[BLUETOOTH] Encerrado")
