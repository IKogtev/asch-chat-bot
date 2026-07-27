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


# Переменные окружения должны указывать на локальный Langfuse
# export LANGFUSE_HOST="http://localhost:3000"
# export LANGFUSE_PUBLIC_KEY="pk-lf-..."
# export LANGFUSE_SECRET_KEY="sk-lf-..."

langfuse = Langfuse()
dataset_name = "dialogs_scenaries_export.json"
input_file = f"agent/langfuse_files_scripts/{dataset_name}" # Файл, который грузите
new_dataset_name = "dialogs_scenaries_imported" # Имя, под которым датасет появится 

print(f"📖 Чтение файла {input_file}...")
with open(input_file, "r", encoding="utf-8") as f:
    data = json.load(f)

print(f"🚀 Создание датасета '{new_dataset_name}'...")
# Создаем сам датасет (если уже существует, метод просто вернет существующий или обновит описание)
langfuse.create_dataset(
    name=new_dataset_name,
    description=data.get("description", "Импортированный датасет")
)

print(f"📤 Загрузка {len(data['items'])} элементов...")
for i, item in enumerate(data["items"]):
    langfuse.create_dataset_item(
        dataset_name=new_dataset_name,
        input=item["input"],
        expected_output=item.get("expected_output"),
        metadata=item.get("metadata")
    )
    # Опционально: прогресс-бар для больших датасетов
    if (i + 1) % 10 == 0:
        print(f"  Обработано {i + 1}/{len(data['items'])} элементов...")

print(f"✅ Импорт завершен! Датасет '{new_dataset_name}' готов к использованию.")