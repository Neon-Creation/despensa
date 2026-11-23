FROM python:3.11-slim

# Dependências nativas para pyzbar/zbar + curl (usado pelo healthcheck)
RUN apt-get update && apt-get install -y \
    libzbar0 \
    curl \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Cache de layers: requirements primeiro
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código da API
COPY api/ ./api/
COPY comum.py config.py ./

# Variável para volume persistente
ENV DATABASE_PATH=/data/despensa.db

EXPOSE 8000

ENTRYPOINT ["/app/api/docker-entrypoint.sh"]
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
