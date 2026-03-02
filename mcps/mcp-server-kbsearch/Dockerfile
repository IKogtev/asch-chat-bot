# Базовый образ Python
FROM python:3.11-slim AS base

# Создание рабочей директории
WORKDIR /app

# Копирование requirements.txt
COPY app/requirements-server.txt .

# Установка Python зависимостей
RUN pip install --no-cache-dir -r requirements-server.txt

# Копирование кода приложения
COPY app/mcp-server-kbsearch_v2.py /app/
COPY app/utils /app/utils

# Переменные окружения по умолчанию
ENV PYTHONUNBUFFERED=1 \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=7001 \
    IN_DOCKER=true

# Открытие порта
EXPOSE 7001

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:7001/kb/status', timeout=5)"

# Запуск сервера
CMD ["python", "mcp-server-kbsearch_v2.py"]
