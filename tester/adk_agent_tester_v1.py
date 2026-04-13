import argparse
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
import pandas as pd

load_dotenv()

# Configure logging level (override with env LOG_LEVEL=DEBUG/INFO/WARN/ERROR)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO), format="%(asctime)s %(levelname)s %(message)s")

logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent

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
    We try known endpoints in order; any non-5xx HTTP response means reachable.
    """
    base_url = base_url.rstrip("/")
    candidates = [f"{base_url}/openapi.json", f"{base_url}/docs", f"{base_url}/"]
    for url in candidates:
        try:
            r = requests.get(url, timeout=timeout_sec)
            if r.status_code < 500:
                if 200 <= r.status_code < 300:
                    logger.info(f"✅ ADK reachable: GET {url} -> {r.status_code}")
                else:
                    logger.info(
                        f"✅ ADK reachable (non-2xx but non-5xx): GET {url} -> {r.status_code}"
                    )
                return True
        except requests.RequestException as e:
            logger.debug(
                f"ADK health check: GET {url} failed ({type(e).__name__}: {e}), trying next URL"
            )
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
            except json.JSONDecodeError as e:
                logger.debug(
                    f"ensure_session: {r.status_code} body is not JSON ({e}); "
                    "will treat as failure if not success"
                )
            except (TypeError, ValueError) as e:
                logger.debug(
                    f"ensure_session: {r.status_code} JSON parsed but unexpected shape "
                    f"({type(e).__name__}: {e})"
                )
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
        except json.JSONDecodeError as e:
            logger.debug(f"ADK /run: response body is not JSON ({e}), using raw text")
            return r.text.strip(), []
        except (TypeError, ValueError) as e:
            logger.debug(
                f"ADK /run: JSON decoded but invalid structure ({type(e).__name__}: {e}), using raw text"
            )
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
        except (RuntimeError, requests.RequestException, OSError) as e:
            tc_df.loc[i, "adk_error"] = f"{type(e).__name__}: {e}"
            tc_df.loc[i, "answer"] = ""
        except Exception as e:
            logger.exception(f"Неожиданная ошибка при запросе к ADK (вопрос {q_num})")
            tc_df.loc[i, "adk_error"] = f"{type(e).__name__}: {e}"
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
        if isinstance(e, (OSError, PermissionError, ImportError, ValueError)):
            logger.warning(
                f"Не удалось сохранить parquet/csv ({type(e).__name__}: {e}). Сохраняем CSV рядом."
            )
        else:
            logger.warning(
                f"Неожиданная ошибка сохранения ответов ({type(e).__name__}: {e}). Сохраняем CSV рядом."
            )
        fallback = answers_file_path.with_suffix(".csv")
        tc_df.to_csv(fallback, index=False, encoding="utf-8-sig")
        answers_file_path = fallback

    logger.info(f"Файл с ответами сохранили для повторного использования: {answers_file_path}")
    return tc_df


def _iter_rows(df, desc: str):
    try:
        from tqdm import tqdm  # type: ignore

        return tqdm(df.iterrows(), total=df.shape[0], desc=desc)
    except ImportError as e:
        logger.debug(f"tqdm не установлен, прогресс-бар отключен: {e}")
        return df.iterrows()


def validate_evaluation_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Валидирует и нормализует результат оценки (как в prompt-manager tester)."""
    required_keys = ["accuracy", "completeness", "relevance", "meets_criteria", "overall_score", "explanation"]
    validated_result = dict(result or {})

    for key in required_keys:
        if key not in validated_result:
            if key == "meets_criteria":
                validated_result[key] = False
            elif key == "explanation":
                validated_result[key] = "Объяснение отсутствует"
            else:
                validated_result[key] = 0
        else:
            if key in ["accuracy", "completeness", "relevance", "overall_score"]:
                try:
                    score = float(validated_result[key])
                    validated_result[key] = max(0, min(10, score))
                except (ValueError, TypeError):
                    validated_result[key] = 0
            elif key == "meets_criteria":
                validated_result[key] = bool(validated_result[key])
            elif key == "explanation":
                validated_result[key] = str(validated_result[key]) if validated_result[key] else "Объяснение отсутствует"

    return validated_result


def create_default_evaluation(error_message: str) -> Dict[str, Any]:
    return {
        "accuracy": 0,
        "completeness": 0,
        "relevance": 0,
        "meets_criteria": False,
        "overall_score": 0,
        "explanation": f"Ошибка оценки: {error_message}",
    }

def init_evaluator(
    llm_api_url: str,
    llm_api_key: str,
    llm_api_model: str,
) -> Tuple[Optional[ChatOpenAI], Optional[ChatPromptTemplate], Optional[Any]]:
    """Инициализация модели и промпта оценщика (LangChain как в prompt-manager)."""
    if not llm_api_key:
        logging.warning("LLM_API_KEY не задан. Автооценка будет пропущена.")
        return None, None, None

    evaluator_model = ChatOpenAI(
        base_url=llm_api_url,
        api_key=llm_api_key,
        model=llm_api_model,
        temperature=0.0,
        max_tokens=2000,
        request_timeout=30,
        max_retries=2,
        model_kwargs={"response_format": {"type": "json_object"}},
    )

    evaluation_system_prompt = """
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
    2. Start directly with `{{` and end with `}}`.
    3. No markdown, comments, or quotes around JSON.
    4. Answer in Russian.

    Example of valid output:
    {{"accuracy": 8, "completeness": 7, "relevance": 9, "meets_criteria": true, "overall_score": 8, "explanation": "Ответ соответствует вопросы, есть все необходимые детали."}}
    """

    evaluation_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", evaluation_system_prompt),
            (
                "human",
                """
Evaluate the following AI assistant's response and return the result ONLY in JSON format:

Question: {question}

Reference answer: {reference_answer}

Success criteria: {requirements}

AI assistant's answer: {gpt_answer}
""",
            ),
        ]
    )

    json_parser = JsonOutputParser()
    evaluation_chain = evaluation_prompt | evaluator_model | json_parser
    logger.info("✅ GPT-контроллер для оценки ответов инициализирован")
    return evaluator_model, evaluation_prompt, evaluation_chain


def evaluate_answer(
    question: str,
    reference_answer: str,
    requirements: str,
    gpt_answer: str,
    evaluator_model: Any,
    evaluation_prompt: Any,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """
    Оценивает ответ чат-агента с помощью GPT с retry-логикой (LangChain),
    максимально близко к prompt-manager/tester/chat_agent_tester_v2.py.
    """
    if not all([question, reference_answer, requirements, gpt_answer]):
        return create_default_evaluation("Один или несколько обязательных параметров пусты")

    for attempt in range(max_retries):
        try:
            raw_response = (evaluation_prompt | evaluator_model).invoke(
                {
                    "question": question,
                    "reference_answer": reference_answer,
                    "requirements": requirements,
                    "gpt_answer": gpt_answer,
                }
            )

            response_text = raw_response.content if hasattr(raw_response, "content") else str(raw_response)
            response_text = str(response_text).strip()
            if not response_text or response_text in ("[]", "null"):
                time.sleep(1)
                continue

            json_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", response_text, re.DOTALL)
            if not json_match:
                time.sleep(1)
                continue

            json_str = json_match.group(0)
            try:
                result = json.loads(json_str)
            except json.JSONDecodeError as e:
                logger.debug(
                    f"evaluate_answer: matched segment is not valid JSON "
                    f"(попытка {attempt + 1}/{max_retries}): {e}"
                )
                time.sleep(1)
                continue

            if isinstance(result, dict) and len(result) > 0:
                expected_keys = ["accuracy", "completeness", "relevance", "overall_score"]
                if any(k in result for k in expected_keys):
                    return validate_evaluation_result(result)
                time.sleep(1)
                continue

            time.sleep(1)
        except Exception as e:
            msg = str(e)
            if "401" in msg or "Unauthorized" in msg:
                logging.error(
                    "LLM вернул 401 Unauthorized. Проверьте переменные окружения "
                    "LLM_API_KEY / LLM_API_URL / LLM_API_MODEL."
                )
                return create_default_evaluation("LLM 401 Unauthorized: отсутствует или неверный ключ/endpoint")
            logging.warning(
                f"Ошибка запроса к LLM ({type(e).__name__}, попытка {attempt + 1}/{max_retries}): {msg}"
            )
            time.sleep(2)
            continue

    return create_default_evaluation(f"Не удалось получить корректный ответ после {max_retries} попыток")


def evaluate_all(tc_df, evaluator_model: Any, evaluation_prompt: Any):
    """Оценивает ответы агента и добавляет метрики в датафрейм (как в prompt-manager)."""
    try:
        from tqdm import tqdm  # type: ignore

        iterator = tqdm(tc_df.iterrows(), total=tc_df.shape[0], desc="Оценка ответов")
    except ImportError as e:
        logger.debug(f"tqdm не установлен, прогресс-бар оценки отключен: {e}")
        iterator = tc_df.iterrows()

    for i, row in iterator:
        question = row.get("Вопросы")
        reference_answer = row.get("Ожидаемые ответы")
        requirements = row.get("Критерий успеха")
        gpt_answer = row.get("answer")
        q_num = row.get("№")

        logger.info(
            f"\nОценка вопроса {q_num}: {str(question)[:50]}..." if len(str(question)) > 50 else f"\nОценка вопроса {q_num}: {question}"
        )

        try:
            evaluation = evaluate_answer(
                question=str(question or ""),
                reference_answer=str(reference_answer or ""),
                requirements=str(requirements or ""),
                gpt_answer=str(gpt_answer or ""),
                evaluator_model=evaluator_model,
                evaluation_prompt=evaluation_prompt,
                max_retries=3,
            )

            if not isinstance(evaluation, dict):
                logger.warning(f"⚠️ Evaluation не является словарем: {type(evaluation)}, значение: {evaluation}")
                evaluation = create_default_evaluation("Некорректный тип результата оценки")

            required_keys = ["accuracy", "completeness", "relevance", "meets_criteria", "overall_score", "explanation"]
            for key in required_keys:
                if key not in evaluation:
                    logger.warning(f"⚠️ Отсутствует ключ '{key}' в результате оценки")
                    evaluation[key] = 0 if key != "explanation" else "Отсутствует значение"
                    if key == "meets_criteria":
                        evaluation[key] = False

            tc_df.loc[i, "accuracy"] = evaluation.get("accuracy", 0)
            tc_df.loc[i, "completeness"] = evaluation.get("completeness", 0)
            tc_df.loc[i, "relevance"] = evaluation.get("relevance", 0)
            tc_df.loc[i, "meets_criteria"] = evaluation.get("meets_criteria", False)
            tc_df.loc[i, "overall_score"] = evaluation.get("overall_score", 0)
            tc_df.loc[i, "explanation"] = evaluation.get("explanation", "")
        except Exception as e:
            logger.error(f"❌ Ошибка при оценке вопроса {q_num} ({type(e).__name__}): {e}")
            tc_df.loc[i, "accuracy"] = 0
            tc_df.loc[i, "completeness"] = 0
            tc_df.loc[i, "relevance"] = 0
            tc_df.loc[i, "meets_criteria"] = False
            tc_df.loc[i, "overall_score"] = 0
            tc_df.loc[i, "explanation"] = f"Ошибка оценки: {str(e)}"

    return tc_df


def save_report_and_plot(tc_df, base_filename: str, output_dir: Path) -> Tuple[Path, Optional[Path]]:
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
        except Exception as e:
            logger.warning(
                f"Не удалось сохранить PNG отчёта ({type(e).__name__}: {e}), пробуем HTML"
            )
            html_path = output_dir / f"REPORT_{base_filename}_{timestamp}.html"
            try:
                fig.write_html(str(html_path), include_plotlyjs="cdn", full_html=True)
                image_path = html_path
            except (OSError, PermissionError, ValueError) as e2:
                logger.warning(
                    f"Не удалось сохранить HTML визуализации ({type(e2).__name__}: {e2})"
                )
                image_path = None
            except Exception as e2:
                logger.warning(
                    f"Неожиданная ошибка сохранения HTML ({type(e2).__name__}: {e2})"
                )
                image_path = None
    except ImportError as e:
        logger.info(f"Plotly не установлен, график пропущен: {e}")
        image_path = None
    except (ValueError, KeyError, TypeError) as e:
        logger.warning(f"Некорректные данные для графика ({type(e).__name__}: {e})")
        image_path = None
    except Exception as e:
        logger.warning(f"Не удалось построить визуализацию ({type(e).__name__}: {e})")
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

    evaluator_model, evaluation_prompt, _ = init_evaluator(
        LLM_API_URL,
        LLM_API_KEY,
        LLM_API_MODEL,
    )

    if evaluator_model and evaluation_prompt:
        tc_df = evaluate_all(tc_df, evaluator_model, evaluation_prompt)
    else:
        logger.warning("LLM_API_KEY не задан или оценщик не инициализирован. Автооценка будет пропущена.")
        tc_df["accuracy"] = 0
        tc_df["completeness"] = 0
        tc_df["relevance"] = 0
        tc_df["meets_criteria"] = False
        tc_df["overall_score"] = 0
        tc_df["explanation"] = "Автооценка пропущена: нет оценщика"

    report_path, image_path = save_report_and_plot(tc_df, base_filename=excel_path.stem, output_dir=output_dir)
    logger.info(f"Отчет сохранен: {report_path}")
    if image_path:
        logger.info(f"Визуализация сохранена: {image_path}")


if __name__ == "__main__":
    main()
