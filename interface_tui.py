#!/usr/bin/env python3

import requests
import json
from pathlib import Path
from datetime import datetime
import subprocess
import sys

ARCH_IP = "100.78.69.110"
SERVER_URL = f"http://{ARCH_IP}:5000"

class InterfacePentestLink:
    def __init__(self):
        self.token = None
        self.conectado = False
        self.bateria_notebook = 0
        self.bateria_celular = 0
        self.comandos = {}
        
    def limpar(self):
        subprocess.run(["clear"], check=False)
    
    def header(self):
        print("=" * 60)
        print("┃ SPOOK - INTERFACE NOTEBOOK")
        print("=" * 60)
        status = "█ ONLINE" if self.conectado else "░ OFFLINE"
        print(f"Status: {status} | Bateria Notebook: {self.bateria_notebook}% | Celular: {self.bateria_celular}%")
        print("-" * 60)
    
    def menu_principal(self):
        self.limpar()
        self.header()
        print("\n[1] Conectar Celular (Pareamento)")
        print("[2] Comandos")
        print("[3] Arquivos")
        print("[4] Terminal Remoto")
        print("[5] Clipboard")
        print("[6] Configurações")
        print("[0] Sair")
        print()
        return input("Opção: ").strip()
    
    def menu_comandos(self):
        self.limpar()
        self.header()
        print("\nCOMARDOS DISPONÍVEIS:\n")
        
        if not self.comandos:
            print("Nenhum comando carregado")
            input("Pressione ENTER...")
            return
        
        for idx, (categoria, cmds) in enumerate(self.comandos.items(), 1):
            print(f"\n[{idx}] {categoria.upper()}")
            for sub_idx, cmd in enumerate(cmds, 1):
                print(f"    [{sub_idx}] {cmd['nome']}")
        
        print("\n[0] Voltar")
        input("Pressione ENTER...")
    
    def pareamento(self):
        self.limpar()
        self.header()
        print("\nPAREAMENTO COM CELULAR")
        print("-" * 60)
        print("Verifique o código SAS no notebook e no celular")
        print()
        
        try:
            resp = requests.get(f"{SERVER_URL}/status", timeout=3)
            if resp.status_code == 200:
                print("✓ Servidor conectado")
                print("Aguardando pareamento do celular...")
                input("Pressione ENTER quando pareado...")
                self.conectado = True
            else:
                print("✗ Servidor não respondeu")
        except Exception as e:
            print(f"✗ Erro: {e}")
        
        input("Pressione ENTER...")
    
    def arquivos(self):
        self.limpar()
        self.header()
        print("\nARQUIVOS")
        print("-" * 60)
        pasta_recebidos = Path.home() / "pentestlink-servidor" / "recebidos"
        pasta_compartilhados = Path.home() / "pentestlink-servidor" / "compartilhados"
        
        print("\nRECEBIDOS:")
        if pasta_recebidos.exists():
            for f in sorted(pasta_recebidos.iterdir()):
                print(f"  {f.name}")
        
        print("\nCOMPARTILHADOS:")
        if pasta_compartilhados.exists():
            for f in sorted(pasta_compartilhados.iterdir()):
                print(f"  {f.name}")
        
        print("\n[1] Enviar arquivo")
        print("[0] Voltar")
        input("Pressione ENTER...")
    
    def terminal_remoto(self):
        self.limpar()
        self.header()
        print("\nTERMINAL REMOTO")
        print("-" * 60)
        print("Conexão com terminal do celular")
        print("[Não implementado ainda]")
        input("Pressione ENTER...")
    
    def clipboard(self):
        self.limpar()
        self.header()
        print("\nCLIPBOARD")
        print("-" * 60)
        print("[Monitoramento automático ativo]")
        input("Pressione ENTER...")
    
    def config(self):
        self.limpar()
        self.header()
        print("\nCONFIGURAÇÕES")
        print("-" * 60)
        print(f"IP Servidor: {ARCH_IP}:5000")
        print(f"Token: {self.token[:20]}..." if self.token else "Nenhum token")
        print("\n[1] Revogar pareamento")
        print("[0] Voltar")
        input("Pressione ENTER...")
    
    def executar(self):
        while True:
            opcao = self.menu_principal()
            
            if opcao == "1":
                self.pareamento()
            elif opcao == "2":
                self.menu_comandos()
            elif opcao == "3":
                self.arquivos()
            elif opcao == "4":
                self.terminal_remoto()
            elif opcao == "5":
                self.clipboard()
            elif opcao == "6":
                self.config()
            elif opcao == "0":
                print("\nEncerrando...")
                break
            else:
                print("Opção inválida")
                input("Pressione ENTER...")

if __name__ == "__main__":
    app = InterfacePentestLink()
    app.executar()
