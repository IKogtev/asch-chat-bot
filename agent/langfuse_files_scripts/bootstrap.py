import requests
import os
import json
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv
from langfuse import Langfuse

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
    
    def create_llm_judge_evaluator(self) -> dict:
        """
        Создает evaluator для оценки точности ответов агента.
        Соответствует конфигурации на скриншотах пользователя.
        """
        
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
        
        # Шаг 2: Создаем evaluation rule для experiments (dataset runs)
        # rule_payload = {
        #     "name": "Accuracy Evaluation Rule",

        #     "target": "experiment",

        #     "evaluator": {
        #         "name": "Accuracy Evaluator",
        #         "scope": "project"
        #     },

        #     "enabled": True,

        #     "sampling": 1,

        #     "delay": 30000,

        #     "mapping": [
        #         {
        #             "variable": "input",
        #             "source": "input"
        #         },
        #         {
        #             "variable": "expected_output",
        #             "source": "expected_output"
        #         },
        #         {
        #             "variable": "output",
        #             "source": "output"
        #         }
        #     ]
        # }
        rule_payload = {
            "name": "Accuracy Evaluation Rule - Legacy",
            "target": "observation",  # Это автоматически дает "Experiments" и "Low-level SDK Methods" в UI
            
            "evaluator": {
                "name": "Accuracy Evaluator",
                "scope": "project"
            },
            
            "enabled": True,
            "samplingRate": 1.0,  # 100%
            "delaySeconds": 30,   # 30 секунд
            "config": {
                "type": "experiment",
                "method": "sdk"  # Это соответствует "Low-level SDK methods" в UI
            },
            # Эти короткие строки API Langfuse автоматически развернет в UI 
            # в "Object: Dataset item / Trace" и соответствующие поля
            "mapping": [
            {
                "variable": "input",
                "source": "input"
            },
            {
                "variable": "expected_output",
                "source": "metadata",  # Для observation expected_output должен быть в metadata
                "jsonPath": "$.expected_output"  # Извлекаем из metadata
            },
            {
                "variable": "output",
                "source": "output"
            }
        ]
            # "mapping": [
            #    {
            #         "variable": "input",
            #         "source": "input",
            #         "objectType": "dataset_item",
            #         "objectField": "input"
            #     },
            #     {
            #         "variable": "expected_output",
            #         "source": "expected_output",
            #         "objectType": "dataset_item",
            #         "objectField": "expectedOutput"
            #     },
            #     {
            #         "variable": "output",
            #         "source": "output",
            #         "objectType": "trace",
            #         "objectField": "output"
            #     }
            # ]
            # "mapping": [
            #     {
            #         "variable": "input",
            #         "source": "dataset_item.input"
            #     },
            #     {
            #         "variable": "expected_output",
            #         "source": "dataset_item.expectedOutput"
            #     },
            #     {
            #         "variable": "output",
            #         "source": "trace.output"
            #     }
            # ]
        }
        
        print("\n📝 Создаю evaluation rule...")
        rule_response = requests.post(
            f"{self.base_url}/evaluation-rules",
            json=rule_payload,
            auth=self.auth,
            headers={"Content-Type": "application/json"}
        )
        
        if rule_response.status_code not in [200, 201]:
            raise Exception(f"Failed to create evaluation rule: {rule_response.status_code} - {rule_response.text}")
        
        rule_data = rule_response.json()
        print(f"✅ Evaluation rule создан успешно! ID: {rule_data.get('id')}")
        print(f"   Name: {rule_data.get('name')}")
        print(f"   Target: {rule_data.get('target')}")
        sampling_val = rule_data.get('samplingRate') or rule_data.get('sampling') or 1.0
        print(f"   Sampling Rate: {sampling_val * 100}%")
        print(f"   Delay: {rule_data.get('delaySeconds')} seconds")
        return {
            "evaluator": evaluator_data,
            "rule": rule_data
        }
    
    def list_evaluators(self):
        """Получить список всех evaluators"""
        response = requests.get(
            f"{self.base_url}/evaluators",
            auth=self.auth
        )
        return response.json()
    
    def list_evaluation_rules(self):
        """Получить список всех evaluation rules"""
        response = requests.get(
            f"{self.base_url}/evaluation-rules",
            auth=self.auth
        )
        return response.json()


def main():
    """Пример использования"""
    
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
        print("🚀 Автоматическое создание evaluator и evaluation rule...\n")
        
        # Создаем evaluator и rule
        result = creator.create_llm_judge_evaluator()
        
        print("\n" + "="*60)
        print("✅ ГОТОВО!Evaluator успешно создан и настроен.")
        print("="*60)
        print(f"\nEvaluator ID: {result['evaluator']['id']}")
        print(f"Evaluation Rule ID: {result['rule']['id']}")
        print("\nТеперь evaluator будет автоматически выполняться на:")
        print("  - Новых dataset run items")
        print("  - Существующих dataset run items")
        print(f"  - Sampling: 100%")
        print(f"  - Delay: 30 seconds")
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")


if __name__ == "__main__":
    main()