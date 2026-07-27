FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

# System deps for Plotly/Kaleido (Chromium runtime libs)
RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends \
      curl \
      libnss3 libatk-bridge2.0-0 libcups2 libxcomposite1 libxdamage1 libxfixes3 \
      libxrandr2 libgbm1 libxkbcommon0 libpango-1.0-0 libcairo2 libasound2 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy tester scripts + requirements (build context should be repo root: `asch-chat-bot/`)
COPY adk_agent_tester.py application_health_tester.py send_health_alert_telegram.py requirements.txt ping_nastya_bot.xlsx ./

# Install Python dependencies
RUN python -m pip install --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt && \
    plotly_get_chrome -y || true

CMD ["python3", "/app/adk_agent_tester.py"]
