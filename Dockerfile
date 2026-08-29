FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY servidor.py servidor_bluetooth.py pareamento.py ./

ENV PYTHONUNBUFFERED=1

EXPOSE 5000

CMD ["python", "servidor.py"]