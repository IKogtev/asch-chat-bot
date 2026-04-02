import requests

BASE = "https://adk-agent-chatbot-dev.sandbox-2.wwwnstcloud.ru"
APP = "agent"
USER = "tester"
SESSION = "default"
# 1) ensure session exists
# r = requests.post(f"{BASE}/apps/{APP}/users/{USER}/sessions/{SESSION}", json={}, timeout=20)
# r.raise_for_status()

# 2) run a message
payload = {
    "app_name": APP,
    "user_id": USER,
    "session_id": SESSION,
    "new_message": {"role": "user", "parts": [{"text": "Привет! Что ты умеешь?"}]},
}
r = requests.post(f"{BASE}/run", json=payload, timeout=120)
r.raise_for_status()
events = r.json()
print(type(events), "events:", len(events))
print(events[:1])
