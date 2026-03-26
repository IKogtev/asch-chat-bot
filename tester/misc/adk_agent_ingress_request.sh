#!/bin/sh

# 1) Ensure session exists
curl -i -X POST \
  "https://adk-agent-chatbot-dev.sandbox-2.wwwnstcloud.ru/apps/agent/users/tester/sessions/default" \
  -H "Content-Type: application/json" \
  -d "{}"

# 2) Run a message

curl -s -X POST \
  "https://adk-agent-chatbot-dev.sandbox-2.wwwnstcloud.ru/run" \
  -H "Content-Type: application/json" \
  -d '{
    "app_name": "agent",
    "user_id": "tester",
    "session_id": "default",
    "new_message": {
      "role": "user",
      "parts": [{"text": "Привет! Что ты умеешь?"}]
    }
  }'
