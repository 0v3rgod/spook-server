# SPOOK Server (Arch Linux)

Servidor Flask + Socket.IO que roda no notebook Arch do Taylor. Funções:

1. **Bridge IA** — executa tool-calls vindos do relay (scans nmap/nikto/gobuster, comandos sistema, info)
2. **Comandos/Métricas/Terminal** — socket namespace padrão para o app/overlay
3. **Overlay GTK4** — GUI Wayland (status, chat, pad, IA, notificações do celular)
4. **Relatórios** — gera `.docx` (OOXML manual) com tema do terminal e sobe pro Drive
5. **Pareamento** — Ed25519 + token de sessão compartilhado com relay/app

## Componentes

| Arquivo | Descrição |
|---------|-----------|
| `servidor.py` | Flask + Socket.IO principal (porta 5000, Tailscale `100.78.69.110`) |
| `spook-overlay.py` | GTK4/Wayland overlay (systemd user `spook-overlay.service`) |
| `hub_store.py` | Armazenamento local-first + sync rclone (chat, tasks, media, finance, pad, relatorios) |
| `spook_pad.py` | Lógica do PAD (notas + tasks cross-device, merge 3 vias, calendário) |
| `agent/` | Agente ReAct local (Ollama) com ferramentas pentest, memória Chroma |
| `pareamento.py` | Crypto Ed25519, token de sessão |
| `relatorio_doc.py` | Gera `.docx` (OOXML zip) com cores/fonte do terminal |

## Requisitos

- Python 3.14+ (Arch)
- rclone configurado com `gdrive:` (mesmo remote do relay)
- Tailscale ativo
- `ydotoold` rodando (`/tmp/.ydotool_socket`) para input sintético
- Ollama (opcional, para agente local): `ollama serve` + `ollama pull qwen2.5-coder:7b`

## Variáveis de ambiente

```bash
# .env (opcional, para sobrescrever defaults)
SPOOK_REMOTE=gdrive:SPOOK
SPOOK_DADOS=/tmp
SPOOK_CHAT_DIR=spook_chat_local
SPOOK_TASKS_DIR=spook_tasks_local
SPOOK_MEDIA_DIR=/tmp/spook_media_local
SPOOK_RELATORIOS_DIR=/home/taylor/pentestlink-servidor/relatorios
SPOOK_CORS_ORIGINS=http://127.0.0.1:5000,http://100.78.69.110:5000
```

## Como rodar

```bash
cd ~/pentestlink-servidor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Teste rápido
python servidor.py
# Health: curl http://100.78.69.110:5000/status  →  {"status":"online"}
```

## Systemd (produção)

```ini
# ~/.config/systemd/user/pentestlink.service
[Unit]
Description=SPOOK Server (Flask + Socket.IO)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/taylor/pentestlink-servidor
Environment=PYTHONUNBUFFERED=1
Environment=XDG_RUNTIME_DIR=/run/user/1000
ExecStart=/home/taylor/pentestlink-servidor/.venv/bin/python servidor.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now pentestlink.service
# Logs: journalctl --user -u pentestlink.service -f
```

```ini
# ~/.config/systemd/user/spook-overlay.service
[Unit]
Description=SPOOK Overlay (GTK4 Wayland)
After=graphical-session.target
Requires=graphical-session.target

[Service]
Type=simple
Environment=GDK_BACKEND=wayland
Environment=PYTHONUNBUFFERED=1
Environment=XDG_RUNTIME_DIR=/run/user/1000
ExecStart=/usr/bin/python3 /home/taylor/.local/bin/spook-overlay
Restart=on-failure
RestartSec=10
TimeoutStopSec=5

[Install]
WantedBy=graphical-session.target
```

```bash
# Só inicia em sessão gráfica (Wayland/Hyprland)
systemctl --user start --no-block spook-overlay.service
```

## Bridge IA (relay → Arch)

O relay (namespace `/ia`) recebe tool-calls da IA (Groq) e encaminha via Socket.IO para o Arch:

| Evento (relay → Arch) | Handler no Arch | Executa |
|----------------------|-----------------|---------|
| `ia_executar_scan` | `on_ia_executar_scan` | `executar_scan_ia(scan, alvo, portas)` |
| `ia_executar_comando` | `on_ia_executar_comando` | `executar_comando_ia(comando)` |
| `ia_obter_info` | `on_ia_obter_info` | `coletar_info_servidor()` + `coletar_info_celular()` |

O Arch devolve o resultado via `ia_executar_scan|comando|info` (namespace default) pro relay, que continua a conversa com a IA e emite `chat_ia_resposta` final.

### Catálogo de scans (SCANS_CMD_IA)

`LIVE HOST`, `NETWORK SCANNER`, `COMMON PORTS`, `FULL PORTS`, `SERVICES`, `OS DETECTION`, `COMPLETE SCAN`, `VULN SCAN`, `VULN TARGETED`, `BANNER GRAB`, `HTTP HEADERS`, `HTTP RESPONSE`, `WHATWEB`, `GOBUSTER DIRS`, `NIKTO`, `DNS RECORDS`, `DNS ANY`, `SUBDOMAINS`, `SMB ENUM`, `FTP ANON`, `SSL/TLS INFO`, `WHOIS`, `TRACEROUTE`, `MY NETWORK`, `LOCAL ENUM`, `SUID FIND`, `CAPABILITIES`, `TERMUX SERVICES`, `WIFI INFO`, `TCP CONNECT TEST`, `LOG ANALYZER`, `HASH ID`, `JOHN`, `JOHN SHOW`, `HASHCAT MD5`, `AUTO PENTEST`.

### Catálogo de comandos (CATALOGO_COMANDOS)

`sistema`: Lock Screen, Suspend, **Shutdown**, **Reboot**, Reload WM, Airplane Mode, Radio On  
`captura`: Screenshot  
`diagnostics`: Network Interfaces, Local Routes, DNS Configuration, Listening Ports, Tailscale Status  
`info`: Public IP  
`midia`: Play/Pause, Next, Prev, Volume Up/Down, Brightness Up/Down  

**Comandos com `"confirmar": true` (Suspend, Shutdown, Reboot) são recusados pela IA** — o app pede confirmação na tela; a IA não consegue confirmar.

## Segurança

- Token de pareamento em `~/.pentestlink/token_sessao.txt` (igual no relay/app)
- `validar_conexao_socket`: `hmac.compare_digest` (tempo constante), não loga token
- `/ia` namespace **exige token** (antes era aberto)
- `shlex.quote(alvo)` em scans (alvo vem da IA = texto não confiável)
- CORS restrito por `SPOOK_CORS_ORIGINS` (env)
- Overlay passa token no connect do `/ia`

## Hub Store (local-first + Drive sync)

- Espelho local em `/tmp/spook_chat_local`, `/tmp/spook_tasks_local`, etc.
- Outbox pendente em `/tmp/spook_pending.json` (flock)
- Tasks: **um arquivo por task** em `gdrive:SPOOK/tasks/<id>.json` (evita race condition)
- Lapides de chat (`_apagados_chat.json`) impedem ressurreição de msg apagada
- Finance: espelho `spook_finance_local` + outbox `spook_finance_pending.json`

## Logs

```bash
# Servidor
journalctl --user -u pentestlink.service -f
# Overlay
journalctl --user -u spook-overlay.service -f
# Arquivo
tail -f /tmp/server.log
```

## Licença

MIT — uso pessoal/estudo.
