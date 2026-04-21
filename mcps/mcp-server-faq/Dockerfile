# dockerfile Р?Р>С? Р?С<Р?РчС?РчР?РёС? Р? Р?С'Р?РчР>С?Р?Р?С?С'Рё
# Р?С?РїР?Р>С?Р·С?РчР? Р+Р°Р·Р?Р?С<Р№ Р?Р+С?Р°Р·
FROM python:3.11-slim AS base

WORKDIR /app
COPY pip.conf /etc/pip.conf

# Р?Р?РїРёС?С?РчР? requirements Р?Р>С? РєС?С?РёС?Р?Р?Р°Р?РёС? С?Р>Р?С?
COPY requirements.txt /app/requirements.txt

# Р?С?С'Р°Р?Р°Р?Р>РёР?Р°РчР? Python Р·Р°Р?РёС?РёР?Р?С?С'Рё С? РєС?С?РёС?Р?Р?Р°Р?РёРчР?
RUN pip install --no-cache-dir -r requirements.txt
# Р?Р?РїРёС?С?РчР? С'Р?Р>С?РєР? РєР?Р? РїС?РёР>Р?РРчР?РёС? 
COPY mcp_faq_v2.py /app/
COPY utils /app/utils

# Р?РчС?РчР?РчР?Р?С<Рч Р?РєС?С?РРчР?РёС?
ENV PYTHONUNBUFFERED=1 \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=7000 \
    IN_DOCKER=true

EXPOSE 7000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:7000/faq/status', timeout=5)"

# Р-Р°РїС?С?Рє С?РчС?Р?РчС?Р°
CMD ["python", "mcp_faq_v2.py"]
