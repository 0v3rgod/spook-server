#!/bin/bash

cd /home/taylor/pentestlink-servidor

python3 servidor_bluetooth.py >> /tmp/server_bluetooth.log 2>&1 &

while true; do
    echo "[$(date)] Iniciando servidor..."
    python3 servidor.py 2>&1 | tee -a /tmp/server.log
    echo "[$(date)] Servidor caiu, reiniciando em 5 segundos..."
    sleep 5
done
