import argparse
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from dotenv import load_dotenv


SCRIPT_DIR = Path(__file__).resolve().parent


def _setup_logger():
    try:
        from utils.logger import setup_logger  # type: ignore

        return setup_logger("tester", "adk_agent_tester.log")
    except Exception:
        import logging

        logging.basicConfig(
            level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
            format="%(asctime)s %(levelname)s %(message)s",
        )
        return logging.getLogger("tester")


logger = _setup_logger()


def _try_import_pandas():
    try:
        import pandas as pd  # type: ignore

        return pd
    except Exception as e:
        raise RuntimeError(
            "Не найден pandas. Установите зависимости для тестера, например:\n"
            "  pip install -r tester/requirements-tester.txt\n"
            f"Исходная ошибка импорта: {e}"
        )


# === Config (similar to prompt-manager tester) ===
ASK_QUESTIONS = os.getenv("ASK_QUESTIONS", "1").strip() in ("1", "true", "yes")

ADK_API_BASE = os.getenv("ADK_API_BASE", "").strip()
ADK_APP_NAME = os.getenv("ADK_APP_NAME", "agent").strip()
ADK_TIMEOUT_SEC = int(os.getenv("ADK_TIMEOUT_SEC", "180"))

# Evaluator (OpenAI-compatible endpoint)
LLM_API_URL = os.getenv("LLM_API_URL", "https://dsrv1.llm.nstcloud.ru/v1").strip()
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_API_MODEL = os.getenv("LLM_API_MODEL", "Qwen/Qwen3-30B-A3B").strip()

# Test cases
TC_TASK_FILE_NAME = os.getenv("TC_TASK_FILE_NAME", "NST-cons use cases pack.xlsx")
TC_TASK_ABS_PATH = SCRIPT_DIR / TC_TASK_FILE_NAME


def check_adk_health(base_url: str, timeout_sec: int = 5) -> bool:
    """
    Lightweight reachability check for ADK runtime from this machine.
    We try known endpoints in order; any 200 means reachable.
    """
    base_url = base_url.rstrip("/")
    candidates = [f"{base_url}/openapi.json", f"{base_url}/docs", f"{base_url}/"]
    for url in candidates:
        try:
            r = requests.get(url, timeout=timeout_sec)
            if 200 <= r.status_code < 300:
                logger.info(f"✅ ADK reachable: GET {url} -> {r.status_code}")
                return True
        except Exception:
            continue
    logger.error(f"❌ ADK not reachable via {candidates}")
    return False


class AdkApiClient:
    """
    Minimal sync client matching bot_v6.py semantics (ensure_session + run).
    """

    def __init__(self, base_url: str, app_name: str, timeout_sec: int):
        self.base_url = base_url.rstrip("/")
        self.app_name = app_name
        self.timeout_sec = timeout_sec

    def ensure_session(self, user_id: str, session_id: str) -> None:
        url = f"{self.base_url}/apps/{self.app_name}/users/{user_id}/sessions/{session_id}"
        r = requests.post(url, json={}, timeout=min(self.timeout_sec, 30))
        if r.status_code in (200, 201):
            return
        # ADK sometimes answers 400/409 with "already exists"
        if r.status_code in (400, 409):
            try:
                data = r.json()
                detail = str(data.get("detail") or "").lower()
                if "exists" in detail or "already" in detail:
                    return
            except Exception:
                pass
        raise RuntimeError(f"ADK ensure_session failed: {r.status_code} {r.text[:300]}")

    def run(self, user_id: str, session_id: str, text: str) -> Tuple[str, List[Dict[str, Any]]]:
        url = f"{self.base_url}/run"
        payload = {
            "app_name": self.app_name,
            "user_id": user_id,
            "session_id": session_id,
            "new_message": {"role": "user", "parts": [{"text": text}]},
        }
        r = requests.post(url, json=payload, timeout=self.timeout_sec)
        if r.status_code != 200:
            raise RuntimeError(f"ADK run failed: {r.status_code} {r.text[:500]}")
        try:
            events = r.json()
            if not isinstance(events, list):
                events = [{"text": str(events)}]
        except Exception:
            return r.text.strip(), []
        answer = self._extract_model_text(events)
        return (answer or "Агент не вернул ответ"), events

    @staticmethod
    def _extract_model_text(events: list) -> str:
        if not events:
            return ""

        out: list[str] = []
        for event in events:
            if not isinstance(event, dict):
                continue

            # model_turn.parts
            if "model_turn" in event and isinstance(event["model_turn"], dict):
                parts = event["model_turn"].get("parts", []) or []
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    if part.get("thought") is True:
                        continue
                    txt = part.get("text")
                    if txt:
                        out.append(txt)

            # content.parts
            content = event.get("content")
            if isinstance(content, dict):
                if isinstance(content.get("text"), str):
                    out.append(content["text"])
                parts = content.get("parts", []) or []
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    if part.get("thought") is True:
                        continue
                    txt = part.get("text")
                    if txt:
                        out.append(txt)
            elif isinstance(content, str) and content.strip():
                out.append(content)

            # fallback
            if isinstance(event.get("text"), str):
                out.append(event["text"])

        return "\n".join(s.strip() for s in out if s and s.strip()).strip()


def load_test_cases(file_name: Path):
    pd = _try_import_pandas()
    df = pd.read_excel(file_name)
    if "№" not in df.columns or df["№"].isnull().any():
        logger.info("!!! Перенумерация вопросов ...")
        df["№"] = df.index + 1
    return df


def initialize_session(client: AdkApiClient, user_id: str, session_id: str) -> str:
    logger.info("🚀 Инициализация сессии с ADK агентом...")
    client.ensure_session(user_id=user_id, session_id=session_id)
    init_q = "Привет! Ты готов отвечать на вопросы ?"
    ans, _events = client.run(user_id=user_id, session_id=session_id, text=init_q)
    logger.info(f"💬 Ответ на инициализацию: {ans[:120]}..." if len(ans) > 120 else f"💬 Ответ: {ans}")
    return session_id


def interrogate_agent(
    client: AdkApiClient,
    *,
    user_id: str,
    session_id: str,
    tc_df,
    answers_file_path: Path,
    ask_questions: bool,
):
    pd = _try_import_pandas()

    if not ask_questions:
        if answers_file_path.exists():
            logger.info(f"Загружаем данные из файла: {answers_file_path}")
            if answers_file_path.suffix.lower() == ".parquet":
                return pd.read_parquet(answers_file_path)
            if answers_file_path.suffix.lower() == ".csv":
                return pd.read_csv(answers_file_path)
        raise RuntimeError(f"Файл с ответами не найден: {answers_file_path}")

    logger.info(f"Задаем вопросы. Всего вопросов в списке тестирования: {len(tc_df)}")

    tc_df["answer"] = ""
    tc_df["response_time"] = 0.0
    tc_df["adk_error"] = ""

    for i, row in _iter_rows(tc_df, desc="Обработка вопросов"):
        question = str(row.get("Вопросы", "")).strip()
        q_num = row.get("№")
        if not question:
            tc_df.loc[i, "adk_error"] = "Пустой вопрос"
            continue

        logger.info(f"\nВопрос {q_num}: {question}")
        start_time = time.time()
        try:
            answer, _events = client.run(user_id=user_id, session_id=session_id, text=question)
            tc_df.loc[i, "answer"] = answer
        except Exception as e:
            tc_df.loc[i, "adk_error"] = str(e)
            tc_df.loc[i, "answer"] = ""
        finally:
            tc_df.loc[i, "response_time"] = round(time.time() - start_time, 2)

        logger.info(
            f"Ответ: {str(tc_df.loc[i, 'answer'])[:120]}..." if len(str(tc_df.loc[i, "answer"])) > 120 else f"Ответ: {tc_df.loc[i, 'answer']}"
        )
        logger.info(f"⏱️ Время ответа: {tc_df.loc[i, 'response_time']} сек.")

    # cache answers similar to prompt-manager (parquet), with CSV fallback
    try:
        if answers_file_path.suffix.lower() == ".parquet":
            tc_df.to_parquet(answers_file_path)
        else:
            tc_df.to_csv(answers_file_path, index=False, encoding="utf-8-sig")
    except Exception as e:
        logger.warning(f"Не удалось сохранить parquet/csv ({e}). Сохраняем CSV рядом.")
        fallback = answers_file_path.with_suffix(".csv")
        tc_df.to_csv(fallback, index=False, encoding="utf-8-sig")
        answers_file_path = fallback

    logger.info(f"Файл с ответами сохранили для повторного использования: {answers_file_path}")
    return tc_df


def _iter_rows(df, desc: str):
    try:
        from tqdm import tqdm  # type: ignore

        return tqdm(df.iterrows(), total=df.shape[0], desc=desc)
    except Exception:
        return df.iterrows()


def validate_evaluation_result(result: Dict[str, Any]) -> Dict[str, Any]:
    required_keys = ["accuracy", "completeness", "relevance", "meets_criteria", "overall_score", "explanation"]
    validated = dict(result or {})

    for key in required_keys:
        if key not in validated:
            if key == "meets_criteria":
                validated[key] = False
            elif key == "explanation":
                validated[key] = "Объяснение отсутствует"
            else:
                validated[key] = 0

    for key in ["accuracy", "completeness", "relevance", "overall_score"]:
        try:
            score = float(validated[key])
            validated[key] = max(0, min(10, score))
        except Exception:
            validated[key] = 0

    validated["meets_criteria"] = bool(validated.get("meets_criteria"))
    validated["explanation"] = str(validated.get("explanation") or "Объяснение отсутствует")
    return validated


def create_default_evaluation(error_message: str) -> Dict[str, Any]:
    return {
        "accuracy": 0,
        "completeness": 0,
        "relevance": 0,
        "meets_criteria": False,
        "overall_score": 0,
        "explanation": f"Ошибка оценки: {error_message}",
    }


EVALUATION_SYSTEM_PROMPT = """
You are an expert evaluator of AI assistant responses.

Your task: assess how well the assistant’s reply matches the reference answer and success criteria.

You will receive:
1. user_question
2. reference_answer
3. success_criteria
4. assistant_answer

Evaluate using:
- accuracy (0–10): how precisely it matches the reference
- completeness (0–10): how fully the topic is covered
- relevance (0–10): how well it fits the question
- meets_criteria (true/false): meets success criteria or not
- overall_score (0–10): overall quality
- explanation: brief 1–2 sentence justification

⚠️ OUTPUT RULES (critical):
1. Respond with **ONLY valid JSON**, no text before or after.
2. Start directly with `{` and end with `}`.
3. No markdown, comments, or quotes around JSON.
4. Answer in Russian.

Example of valid output:
{"accuracy": 8, "completeness": 7, "relevance": 9, "meets_criteria": true, "overall_score": 8, "explanation": "Ответ соответствует вопросы, есть все необходимые детали."}
""".strip()


def evaluate_answer(
    *,
    question: str,
    reference_answer: str,
    requirements: str,
    assistant_answer: str,
    max_retries: int = 3,
) -> Dict[str, Any]:
    if not all([question, reference_answer, requirements, assistant_answer]):
        return create_default_evaluation("Один или несколько обязательных параметров пусты")

    if not LLM_API_KEY:
        return create_default_evaluation("LLM_API_KEY не задан (автооценка отключена)")

    url = f"{LLM_API_URL.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": LLM_API_MODEL,
        "temperature": 0.0,
        "max_tokens": 2000,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": EVALUATION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Evaluate the following AI assistant's response and return the result ONLY in JSON format:\n\n"
                    f"Question: {question}\n\n"
                    f"Reference answer: {reference_answer}\n\n"
                    f"Success criteria: {requirements}\n\n"
                    f"AI assistant's answer: {assistant_answer}\n"
                ),
            },
        ],
    }

    for attempt in range(max_retries):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=30)
            if r.status_code != 200:
                msg = f"HTTP {r.status_code}: {r.text[:200]}"
                if "401" in msg or "Unauthorized" in msg:
                    return create_default_evaluation("LLM 401 Unauthorized: отсутствует или неверный ключ/endpoint")
                time.sleep(1)
                continue

            data = r.json()
            content = (
                (((data.get("choices") or [{}])[0].get("message") or {}).get("content"))  # type: ignore[union-attr]
                or ""
            )
            content = str(content).strip()
            if not content or content in ("[]", "null"):
                time.sleep(1)
                continue

            m = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", content, re.DOTALL)
            if not m:
                time.sleep(1)
                continue

            try:
                obj = json.loads(m.group(0))
            except Exception:
                time.sleep(1)
                continue

            if isinstance(obj, dict) and obj:
                return validate_evaluation_result(obj)
            time.sleep(1)
        except Exception as e:
            time.sleep(2)
            if attempt == max_retries - 1:
                return create_default_evaluation(str(e))

    return create_default_evaluation(f"Не удалось получить корректный ответ после {max_retries} попыток")


def evaluate_all(tc_df):
    for i, row in _iter_rows(tc_df, desc="Оценка ответов"):
        question = str(row.get("Вопросы", "")).strip()
        reference_answer = str(row.get("Ожидаемые ответы", "")).strip()
        requirements = str(row.get("Критерий успеха", "")).strip()
        assistant_answer = str(row.get("answer", "")).strip()
        q_num = row.get("№")

        logger.info(f"\nОценка вопроса {q_num}: {question[:60]}..." if len(question) > 60 else f"\nОценка вопроса {q_num}: {question}")
        ev = evaluate_answer(
            question=question,
            reference_answer=reference_answer,
            requirements=requirements,
            assistant_answer=assistant_answer,
            max_retries=3,
        )

        tc_df.loc[i, "accuracy"] = ev.get("accuracy", 0)
        tc_df.loc[i, "completeness"] = ev.get("completeness", 0)
        tc_df.loc[i, "relevance"] = ev.get("relevance", 0)
        tc_df.loc[i, "meets_criteria"] = ev.get("meets_criteria", False)
        tc_df.loc[i, "overall_score"] = ev.get("overall_score", 0)
        tc_df.loc[i, "explanation"] = ev.get("explanation", "")
    return tc_df


def save_report_and_plot(tc_df, base_filename: str, output_dir: Path) -> Tuple[Path, Optional[Path]]:
    pd = _try_import_pandas()
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_df = tc_df.groupby("Use case").agg(
        accuracy=("accuracy", "mean"),
        completeness=("completeness", "mean"),
        relevance=("relevance", "mean"),
        meets_criteria=("meets_criteria", "min"),
        overall_score=("overall_score", "mean"),
    ).reset_index()

    summary_df.columns = [
        "Код use case",
        "Точность (сред)",
        "Полнота (сред)",
        "Релевантность (сред)",
        "Соответствие всем критериям",
        "Общая оценка (сред)",
    ]

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    report_path = output_dir / f"REPORT_{base_filename}_{timestamp}.xlsx"

    report_df = tc_df.rename(
        columns={
            "Use case": "Код use case",
            "answer": "Ответ чат-бота",
            "response_time": "Время ответа (сек)",
            "accuracy": "Точность",
            "completeness": "Полнота",
            "relevance": "Релевантность",
            "meets_criteria": "Соответствие критериям",
            "overall_score": "Общая оценка",
            "explanation": "Объяснение",
        }
    )

    with pd.ExcelWriter(report_path, engine="xlsxwriter") as writer:
        summary_df.to_excel(writer, sheet_name="Итоги тестирования", index=False)

        columns = [
            "Код use case",
            "№",
            "Вопросы",
            "Ожидаемые ответы",
            "Критерий успеха",
            "Ответ чат-бота",
            "Время ответа (сек)",
            "Точность",
            "Полнота",
            "Релевантность",
            "Соответствие критериям",
            "Общая оценка",
            "Объяснение",
        ]
        for use_case, use_case_df in report_df.groupby("Код use case"):
            sheet = f"Детали {str(use_case)[:20]}"
            use_case_df[columns].to_excel(writer, sheet_name=sheet, index=False)

    image_path: Optional[Path] = None
    try:
        import plotly.graph_objects as go  # type: ignore

        fig = go.Figure()
        use_cases = summary_df["Код use case"].tolist()
        overall_values = summary_df["Общая оценка (сред)"].tolist()
        if use_cases:
            fig.add_trace(
                go.Scatterpolar(
                    r=overall_values + [overall_values[0]],
                    theta=use_cases + [use_cases[0]],
                    fill="toself",
                    name="Общая оценка",
                )
            )

        image_path = output_dir / f"REPORT_{base_filename}_{timestamp}.png"
        try:
            fig.write_image(str(image_path), scale=0.7)
        except Exception:
            html_path = output_dir / f"REPORT_{base_filename}_{timestamp}.html"
            fig.write_html(str(html_path), include_plotlyjs="cdn", full_html=True)
            image_path = html_path
    except Exception:
        image_path = None

    return report_path, image_path


def main() -> None:
    env_local = SCRIPT_DIR / ".env"
    if env_local.exists():
        load_dotenv(env_local, override=True)
    load_dotenv(override=True)

    if not ADK_API_BASE:
        raise RuntimeError(
            "ADK_API_BASE не задан.\n"
            "Для k8s извне это должен быть URL Ingress (например https://adk-agent-...);\n"
            "внутри кластера может быть http://adk-agent:8000."
        )

    parser = argparse.ArgumentParser(description="ADK agent tester (Excel -> ask -> evaluate -> report).")
    parser.add_argument("--excel", default=str(TC_TASK_ABS_PATH))
    parser.add_argument("--out", default=str(SCRIPT_DIR))
    parser.add_argument("--user-id", default=os.getenv("ADK_TEST_USER_ID", "tester"))
    parser.add_argument("--session-id", default=os.getenv("ADK_TEST_SESSION_ID", f"test_{int(time.time())}"))
    args = parser.parse_args()

    excel_path = Path(args.excel).expanduser().resolve()
    output_dir = Path(args.out).expanduser().resolve()

    if not check_adk_health(ADK_API_BASE, timeout_sec=5):
        raise RuntimeError("ADK agent недоступен по ADK_API_BASE (проверьте Ingress/Firewall/DNS).")

    tc_df = load_test_cases(excel_path)

    client = AdkApiClient(ADK_API_BASE, ADK_APP_NAME, timeout_sec=ADK_TIMEOUT_SEC)
    session_id = initialize_session(client, user_id=str(args.user_id), session_id=str(args.session_id))

    answers_file_path = SCRIPT_DIR / ("answers_" + excel_path.stem + (".parquet" if ASK_QUESTIONS else ".parquet"))
    tc_df = interrogate_agent(
        client,
        user_id=str(args.user_id),
        session_id=session_id,
        tc_df=tc_df,
        answers_file_path=answers_file_path,
        ask_questions=ASK_QUESTIONS,
    )

    if not LLM_API_KEY:
        logger.warning("LLM_API_KEY не задан. Автооценка будет пропущена (будет отчет только с ответами).")
        tc_df["accuracy"] = 0
        tc_df["completeness"] = 0
        tc_df["relevance"] = 0
        tc_df["meets_criteria"] = False
        tc_df["overall_score"] = 0
        tc_df["explanation"] = "Автооценка пропущена: нет LLM_API_KEY"
    else:
        tc_df = evaluate_all(tc_df)

    report_path, image_path = save_report_and_plot(tc_df, base_filename=excel_path.stem, output_dir=output_dir)
    logger.info(f"Отчет сохранен: {report_path}")
    if image_path:
        logger.info(f"Визуализация сохранена: {image_path}")


if __name__ == "__main__":
    main()

