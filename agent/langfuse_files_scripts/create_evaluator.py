import requests
import os
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)
if not os.getenv("LANGFUSE_PUBLIC_KEY"):
    print("⚠️ ВНИМАНИЕ: LANGFUSE_PUBLIC_KEY не найден в .env файле!")
else:
    print("✅ Переменные окружения успешно загружены из .env")


class LangfuseEvaluatorCreator:
    """Автоматическое создание LLM-as-a-Judge evaluator через API"""
    
    def __init__(self, host: str, public_key: str, secret_key: str):
        self.host = host.rstrip('/')
        self.public_key = public_key
        self.secret_key = secret_key
        self.auth = (public_key, secret_key)
        self.base_url = f"{self.host}/api/public/unstable"

    def get_llm_connections(self):
        """Получает список доступных LLM-коннекций в проекте"""
        print("🔍 Получаю список LLM-коннекций...")
        
        response = requests.get(
            f"{self.host}/api/public/llm-connections",
            auth=self.auth
        )
        
        if response.status_code == 200:
            response_data = response.json()
            connections = response_data.get("data", [])
            print(f"✅ Найдено {len(connections)} LLM-коннекций.")
            
            # Показываем все доступные коннекции
            for conn in connections:
                custom_models = conn.get('customModels', [])
                models_str = ", ".join(custom_models) if custom_models else "default"
                print(f"   - Provider: {conn.get('provider')}, Adapter: {conn.get('adapter')}, Models: {models_str}, ID: {conn.get('id')}")
            
            # Возвращаем первую попавшуюся (или можно фильтровать по provider/model)
            if connections:
                return connections[0]  # Берем первую доступную
            else:
                print("⚠️ В проекте нет настроенных LLM-коннекций!")
                return None
        else:
            print(f"❌ Ошибка получения LLM-коннекций: {response.status_code}")
            print(response.text)
            return None
    
    def create_llm_judge_evaluator(self) -> dict:
        """
        Создает evaluator для оценки точности ответов агента.
        Соответствует конфигурации на скриншотах пользователя.
        """
        llm_conn = self.get_llm_connections()
        if not llm_conn:
            print("❌ Невозможно создать evaluator без LLM-коннекции.")
            return {}
        provider = llm_conn.get('provider')
        custom_models = llm_conn.get('customModels', [])
        model = custom_models[0] if custom_models else "Qwen3-30B-A3B"
        
        print(f"📝 Использую LLM: {provider} / {model}")
        # Шаг 1: Создаем evaluator
        evaluator_payload = {
            "name": "Accuracy Evaluator",  # Имя evaluator
            "description": "Evaluates the quality and accuracy of AI agent responses",
            "prompt": '''You are an expert QA evaluator for an AI agent. Your task is to evaluate the quality and accuracy of the agent's response.

<context>
<user_input>{{input}}</user_input>
<expected_answer>{{expected_output}}</expected_answer>
<actual_agent_answer>{{output}}</actual_agent_answer>
</context>

Evaluate the <actual_agent_answer> by comparing it to the <expected_answer>, while keeping the context of the <user_input> in mind.

Scoring criteria (strictly a float from 0.0 to 1.0):
- 1.0: Perfect. Semantically identical to expected, all key facts are correct, fully addresses the user input.
- 0.7 - 0.9: Good. Mostly correct, minor omissions or slight phrasing differences, but conveys the same core meaning.
- 0.4 - 0.6: Partial. Misses key information, contains minor hallucinations, or only partially addresses the input.
- 0.1 - 0.3: Poor. Mostly incorrect, misses the main point, or contains significant hallucinations.
- 0.0: Completely wrong, irrelevant, harmful, or blocked by safety filters when it shouldn't have been.''',
        "variables": ["input", "expected_output", "output"],
        "outputDefinition": {
            "dataType": "NUMERIC",
            "reasoning": {
                "description": "Detailed reasoning for the evaluation score"
            },
            "score": {
                "description": "Evaluation score from 0.0 to 1.0"
            }
        },
        "modelConfig": {
            "provider": provider,
            "model": model
        }
    }
        
        print("📝 Создаю evaluator...")
        response = requests.post(
            f"{self.base_url}/evaluators",
            json=evaluator_payload,
            auth=self.auth,
            headers={"Content-Type": "application/json"}
        )
        
        if response.status_code not in [200, 201]:
            print(f"❌ Response: {response.text}")
            raise Exception(f"Failed to create evaluator: {response.status_code} - {response.text}")
        
        evaluator_data = response.json()
        evaluator_id = evaluator_data.get("id")
        
        print(f"✅ Evaluator создан успешно! ID: {evaluator_id}")
        print(f"   Name: {evaluator_data.get('name')}")
        print(f"   Variables: {evaluator_data.get('variables')}")
        
        return {
            "evaluator": evaluator_data
        }
    
    def list_evaluators(self):
        """Получить список всех evaluators"""
        response = requests.get(
            f"{self.base_url}/evaluators",
            auth=self.auth
        )
        return response.json()

def main():
    """функция запуска"""
    
    # Получаем переменные окружения
    host = os.getenv("LANGFUSE_HOST", "http://localhost:3000")
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    
    if not public_key or not secret_key:
        print("❌ Ошибка: Установите переменные окружения:")
        print("   export LANGFUSE_PUBLIC_KEY='pk-lf-...'")
        print("   export LANGFUSE_SECRET_KEY='sk-lf-...'")
        print("   export LANGFUSE_HOST='http://localhost:3000' (опционально)")
        return
    
    # Создаем клиент
    creator = LangfuseEvaluatorCreator(
        host=host,
        public_key=public_key,
        secret_key=secret_key
    )
    
    try:
        print("🚀 Автоматическое создание evaluator... , evaluation rule надо будет самим делать по инструкции\n")
        
        # Создаем evaluator и rule
        result = creator.create_llm_judge_evaluator()
        
        print("\n" + "="*60)
        print("✅ ГОТОВО!Evaluator успешно создан и настроен.")
        print("="*60)
        print(f"\nEvaluator ID: {result['evaluator']['id']}")
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")


if __name__ == "__main__":
    main()