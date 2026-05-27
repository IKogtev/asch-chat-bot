FROM python:3.11-slim AS base

WORKDIR /app

COPY mcps/mcp-server-kbsearch/pip.conf /etc/pip.conf
COPY mcps/mcp-server-kbsearch/app/requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

ENV FASTEMBED_CACHE_PATH=/app/.cache/fastembed
RUN mkdir -p /app/.cache/fastembed \
    && python -c "from fastembed import SparseTextEmbedding; SparseTextEmbedding(model_name='Qdrant/bm25', language='russian')"

COPY mcps/mcp-server-kbsearch/app/mcp-server-kbsearch_v2.py /app/
COPY mcps/mcp-server-kbsearch/app/utils /app/utils/
COPY utils/qdrant_indexer.py utils/mcp_embedding.py utils/logger.py utils/__init__.py \
     utils/indexer.py utils/qdrant_hybrid.py utils/rrf.py utils/qdrant_search_filters.py \
     utils/search_profile.py /app/utils/

ENV PYTHONUNBUFFERED=1 \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=7001 \
    IN_DOCKER=true

EXPOSE 7001

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:7001/kb/status', timeout=5)"

CMD ["python", "mcp-server-kbsearch_v2.py"]
