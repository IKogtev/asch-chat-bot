FROM python:3.11-slim

WORKDIR /app
# установка поддержки для формата .doc чтобы могли с ним работать
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libreoffice-writer \
    && rm -rf /var/lib/apt/lists/*
# Копируем pip.conf для ускорения установки
COPY mcps/kb-manager/pip.conf /etc/pip.conf

# Copy requirements first for better caching
COPY mcps/kb-manager/requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Кэш BM25 (Qdrant/bm25) в образе — без обращения к HF при старте контейнера
ENV FASTEMBED_CACHE_PATH=/app/.cache/fastembed
RUN mkdir -p /app/.cache/fastembed \
    && python -c "from fastembed import SparseTextEmbedding; SparseTextEmbedding(model_name='Qdrant/bm25', language='russian')"

# Copy application code and shared logger
COPY mcps/kb-manager/app/ ./app/
COPY utils/logger.py utils/__init__.py utils/qdrant_hybrid.py utils/rrf.py ./utils/

ENV PYTHONPATH=/app

# Expose port
EXPOSE 5000

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5000", "--timeout-keep-alive", "300"]
