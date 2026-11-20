FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# servidor.py importa pareamento (topo) e hub_store (topo), localizacao
# (import tardio, dentro dos handlers) e o pacote agent/ (agente ReAct local,
# importado com try/except — sem ele o servidor sobe degradado).
COPY servidor.py servidor_bluetooth.py pareamento.py hub_store.py localizacao.py ./
COPY agent/ ./agent/

ENV PYTHONUNBUFFERED=1

EXPOSE 5000

CMD ["python", "servidor.py"]
