FROM python:3.11-slim

WORKDIR /app

# Копируем pip.conf для ускорения установки
COPY pip.conf /etc/pip.conf

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Кэш BM25 (Qdrant/bm25) в образе — без обращения к HF при старте контейнера
ENV FASTEMBED_CACHE_PATH=/app/.cache/fastembed
RUN mkdir -p /app/.cache/fastembed \
    && python -c "from fastembed import SparseTextEmbedding; SparseTextEmbedding(model_name='Qdrant/bm25', language='russian')"

# Copy application code
COPY app/ ./app/

# Expose port
EXPOSE 5000

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5000", "--timeout-keep-alive", "300"]
