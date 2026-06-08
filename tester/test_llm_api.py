import os
import requests
from dotenv import load_dotenv
load_dotenv()  # loads .env from cwd; run from `tester/` or pass a path:
# load_dotenv(Path(__file__).resolve().parent / ".env")
base = os.environ["LLM_API_URL"]
api_key = os.environ["LLM_API_KEY"]
model = os.environ["LLM_API_MODEL"]
url = f"{base}/chat/completions"
headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json",
}
payload = {
    "model": model,
    "messages": [{"role": "user", "content": "Say hello in one short sentence."}],
}
r = requests.post(url, headers=headers, json=payload, timeout=10)
r.raise_for_status()
print(r.json()["choices"][0]["message"]["content"])
