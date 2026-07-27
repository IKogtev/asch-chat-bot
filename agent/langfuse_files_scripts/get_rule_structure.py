import requests
import os
import json
from pathlib import Path
from dotenv import load_dotenv
import requests

env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)
if not os.getenv("LANGFUSE_PUBLIC_KEY"):
    print("⚠️ ВНИМАНИЕ: LANGFUSE_PUBLIC_KEY не найден в .env файле!")
else:
    print("✅ Переменные окружения успешно загружены из .env")
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "http://localhost:3000").rstrip('/')
PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY")
SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY")

def get_all_versions_of_rules():
    """
    Пробует получить evaluation rules через разные версии API
    """
    auth = (PUBLIC_KEY, SECRET_KEY)
    
    # Пробуем разные эндпоинты
    endpoints = [
        f"{LANGFUSE_HOST}/api/public/v1/evaluation-rules",
        f"{LANGFUSE_HOST}/api/public/v2/evaluation-rules", 
        f"{LANGFUSE_HOST}/api/public/unstable/evaluation-rules",  # Уже знаем, что возвращает 1 правило
    ]
    
    for endpoint in endpoints:
        print(f"\n🔍 Пробую эндпоинт: {endpoint}")
        try:
            response = requests.get(endpoint, auth=auth)
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Успешно! Найдено {len(data) if isinstance(data, list) else len(data.get('data', []))} правил.")
                
                # Обработка ответа
                rules = data if isinstance(data, list) else data.get('data', [])
                
                for rule in rules:
                    rule_name = rule.get('name', 'Unknown')
                    rule_id = rule.get('id', 'Unknown')
                    print(f"   - {rule_name} (ID: {rule_id})")
                    
                    # Если это ваше legacy правило, выводим его полностью
                    if 'accuracy' in rule_name.lower():
                        print(f"\n🎯 НАЙДЕНО LEGACY ПРАВИЛО '{rule_name}':")
                        print(json.dumps(rule, indent=2, ensure_ascii=False))
                        
                        # Сохраняем в файл
                        with open('legacy_rule_template.json', 'w', encoding='utf-8') as f:
                            json.dump(rule, f, indent=2, ensure_ascii=False)
                        print(f"\n💾 Структура сохранена в 'legacy_rule_template.json'")
                        
                        return rule
                        
            elif response.status_code == 404:
                print(f"   ❌ 404 - эндпоинт не найден.")
            else:
                print(f"   ❌ {response.status_code} - {response.text[:100]}...")
                
        except Exception as e:
            print(f"   ❌ Ошибка подключения: {e}")
    
    print("\n⚠️ Ни один из эндпоинтов не вернул ваше Legacy правило.")
    print("💡 Это означает, что оно может быть привязано к evaluator'у напрямую или использовать уникальный internal API.")
    return None

if __name__ == "__main__":
    get_all_versions_of_rules()