import os
import json
from pathlib import Path
from dotenv import load_dotenv
from langfuse import Langfuse

env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)
if not os.getenv("LANGFUSE_PUBLIC_KEY"):
    print("⚠️ ВНИМАНИЕ: LANGFUSE_PUBLIC_KEY не найден в .env файле!")
else:
    print("✅ Переменные окружения успешно загружены из .env")

# Убедитесь, что переменные окружения настроены на ваш локальный Langfuse
# export LANGFUSE_HOST="http://localhost:3000" (или ваш IP)
# export LANGFUSE_PUBLIC_KEY="pk-lf-..."
# export LANGFUSE_SECRET_KEY="sk-lf-..."

langfuse = Langfuse()
dataset_name = "dialogs_scenaries" # Имя вашего датасета

print(f"📥 Загрузка датасета '{dataset_name}'...")
dataset = langfuse.get_dataset(dataset_name)

export_data = {
    "name": dataset.name,
    "description": dataset.description or "",
    "items": []
}

for item in dataset.items:
    export_data["items"].append({
        "input": item.input,
        "expected_output": item.expected_output,
        "metadata": item.metadata or {} # Сохраняем conversation_id и turn!
    })

output_file = f"agent/langfuse_files_scripts/{dataset_name}_export.json"
with open(output_file, "w", encoding="utf-8") as f:
    json.dump(export_data, f, ensure_ascii=False, indent=2)

print(f"✅ Успешно экспортировано {len(export_data['items'])} элементов в файл: {output_file}")
print("📤 Теперь отправьте этот файл вашему коллеге.")