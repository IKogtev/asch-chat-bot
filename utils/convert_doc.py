import os
import shutil
from pathlib import Path
import win32com.client as win32

# Ограничиваем пути, так как работаем на Windows
# Скрипт запускается относительно C:\Users\Igor\Desktop\project_links\Job NNT\asch-chat-bot\
BASE_DIR = Path(__file__).resolve().parent.parent

# Задаем абсолютные пути на основе структуры проекта
KB_DIR = BASE_DIR / "kb_storage" / "manager" / "kb"
BACKUP_DIR = BASE_DIR / "kb_storage" / "backup"

def convert_doc_to_docx():
    # Проверяем существование целевой папки с базой знаний
    if not KB_DIR.exists():
        print(f"[Ошибка] Папка не найдена: {KB_DIR}")
        return

    # Создаем папку backup, если её ещё нет
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[Старт] Сканирование папки: {KB_DIR}")
    print(f"[Старт] Папка для бэкапов: {BACKUP_DIR}\n" + "-"*50)

    # Инициализируем MS Word в фоновом режиме через COM-интерфейс
    word = win32.gencache.EnsureDispatch('Word.Application')
    word.Visible = False  # Скрываем окно Word

    analytics_report = {}
    total_converted = 0

    try:
        # Рекурсивный обход папок (используем os.walk, так как он удобен для этой задачи)
        for root, dirs, files in os.walk(str(KB_DIR)):
            for file in files:
                # Фильтруем файлы .doc и отсекаем временные файлы Word (~$...)
                if file.lower().endswith('.doc') and not file.startswith('~$'):
                    doc_path = os.path.abspath(os.path.join(root, file))
                    
                    # Имя и путь для нового .docx
                    file_name_without_ext = os.path.splitext(file)[0]
                    docx_name = f"{file_name_without_ext}.docx"
                    docx_path = os.path.abspath(os.path.join(root, docx_name))

                    print(f"[Конвертация] {file} -> {docx_name}")

                    try:
                        # 1. Открываем старый .doc и пересохраняем в .docx
                        doc = word.Documents.Open(doc_path)
                        # 16 — константа wdFormatXMLDocument (.docx)
                        doc.SaveAs2(docx_path, FileFormat=16)
                        doc.Close()

                        # 2. Переносим исходный .doc в backup
                        backup_destination = BACKUP_DIR / file
                        # Если файл с таким именем уже есть в бэкапе, перестрахуемся от перезаписи
                        if backup_destination.exists():
                            backup_destination = BACKUP_DIR / f"{file_name_without_ext}_backup.doc"

                        shutil.move(doc_path, str(backup_destination))

                        # 3. Собираем относительный путь для красивой аналитики
                        relative_root = os.path.relpath(root, str(KB_DIR))
                        folder_key = f"kb/{relative_root}" if relative_root != "." else "kb/"
                        
                        if folder_key not in analytics_report:
                            analytics_report[folder_key] = []
                        analytics_report[folder_key].append(file)
                        
                        total_converted += 1

                    except Exception as e:
                        print(f"[Ошибка] Не удалось обработать файл {file}: {e}")
                        if 'doc' in locals() and doc:
                            doc.Close(False)

    finally:
        # Гарантированно тушим процесс Word, чтобы он не висел мертвым грузом в системе
        word.Quit()

    # Печать красивого отчета аналитики
    print("\n" + "="*20 + " АНАЛИТИКА " + "="*20)
    if total_converted == 0:
        print("Файлы формата .doc во вложенных папках не найдены.")
    else:
        print(f"Всего успешно обработано файлов: {total_converted}\n")
        for folder, processed_files in analytics_report.items():
            print(f"Папка: {folder}")
            print(f"  └─ Найдено .doc и создано аналогичных .docx: {len(processed_files)} шт.")
            for f in processed_files:
                print(f"      • {f}")
            print()
    print("="*51)

if __name__ == "__main__":
    convert_doc_to_docx()