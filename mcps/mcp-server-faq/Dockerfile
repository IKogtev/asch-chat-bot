# dockerfile для вынесения сервиса в отдельный контейнер
# Используем базовый образ Python
FROM python:3.11-slim AS base

WORKDIR /app
COPY pip.conf /etc/pip.conf

# Копируем requirements для кэширования слоя
COPY requirements.txt /app/requirements.txt

# Устанавливаем Python-зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем только код приложения
COPY mcp_faq_v2.py /app/
COPY utils /app/utils

# Переменные окружения
ENV PYTHONUNBUFFERED=1 \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=7000 \
    IN_DOCKER=true

EXPOSE 7000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:7000/faq/status', timeout=5)"

# Запуск сервера
CMD ["python", "mcp_faq_v2.py"]
