from pathlib import Path
import threading
import time
from .config import PROMPTS_DIR

def start_prompt_watcher(prompt_file: str, agent, logger):
    """
    Надёжный watcher через polling (без watchdog)
    """
    agent_name = prompt_file.split("_agent")[0]
    prompt_path = PROMPTS_DIR / agent_name / prompt_file

    logger.info(f"👀 Watching: {prompt_path}")

    def watch():
        last_mtime = 0

        while True:
            try:
                if prompt_path.exists():
                    mtime = prompt_path.stat().st_mtime

                    if mtime != last_mtime:
                        last_mtime = mtime

                        new_prompt = prompt_path.read_text(encoding="utf-8")
                        agent.instruction = new_prompt

                        logger.warning(f"🔥 PROMPT UPDATED: {prompt_file}")

                time.sleep(1)

            except Exception as e:
                logger.error(f"Watcher error: {e}")
                time.sleep(2)

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()

    # держим ссылку
    agent._prompt_watcher_thread = thread