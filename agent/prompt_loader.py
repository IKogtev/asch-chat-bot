from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import threading

class PromptManager(FileSystemEventHandler):
    """Менеджер для автоматической перезагрузки промпта при изменении файла"""
    
    def __init__(self, prompt_path: Path, agent, logger):
        self.prompt_path = prompt_path
        self.agent = agent
        self.logger = logger
        self._load_prompt()

    def _load_prompt(self):
        """Внутренний метод для чтения файла"""
        try:
            new_content = self.prompt_path.read_text(encoding="utf-8")
            self.agent.instruction = new_content
            if hasattr(self.agent, '_instruction'):
                self.agent._instruction = new_content
                
            self.logger.info(f"🔄 Промпт успешно обновлен из файла: {self.prompt_path.name}")
        except Exception as e:
            self.logger.error(f"❌ Ошибка при автоматической загрузке промпта: {e}")

    def on_modified(self, event):
        if not event.is_directory and Path(event.src_path).resolve() == self.prompt_path.resolve():
            self._load_prompt()

def load_system_prompt(prompt_file: Path, logger) -> str:
    """Загрузить системный промпт из файла"""
    try:
        system_prompt = prompt_file.read_text(encoding="utf-8")
        logger.info(f"Системный промпт загружен из {prompt_file}")
        return system_prompt
    except Exception as e:
        logger.error(f"Ошибка загрузки промпта: {e}")
        return "Ты полезный ассистент в Telegram."

def start_prompt_watcher(prompt_file: Path, agent, logger):
    """Запустить слежение за изменениями файла промпта"""
    event_handler = PromptManager(prompt_file, agent, logger)
    observer = Observer()
    observer.schedule(event_handler, path=str(prompt_file.parent), recursive=False)
    threading.Thread(target=observer.start, daemon=True).start()
    logger.info(f"✓ Запущено слежение за промптом: {prompt_file}")