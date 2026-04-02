import argparse
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from dotenv import load_dotenv


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent


def _setup_logger():
    try:
        # prefer project logger (rotating file + console)
        from utils.logger import setup_logger  # type: ignore

        return setup_logger("tester", "adk_tester.log")
    except Exception:
        import logging

        logging.basicConfig(
            level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
            format="%(asctime)s | %(levelname)s | %(message)s",
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


def _coalesce(*values: Any) -> Any:
    for v in values:
        if v is None:
            continue
        if isinstance(v, float) and str(v) == "nan":
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return v
    return None


def _safe_str(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x.strip()
    return str(x).strip()


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    text = text.strip()
    m = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


@dataclass
class EvaluatorConfig:
    api_base: str
    api_key: str
    model: str
    timeout_sec: int = 30
    max_retries: int = 3


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
{"accuracy": 8, "completeness": 7, "relevance": 9, "meets_criteria": true, "overall_score": 8, "explanation": "Ответ соответствует вопросу, есть все необходимые детали."}
""".strip()


def validate_evaluation_result(result: Dict[str, Any]) -> Dict[str, Any]:
    required_keys = ["accuracy", "completeness", "relevance", "meets_criteria", "overall_score", "explanation"]
    out = dict(result or {})

    for key in required_keys:
        if key not in out:
            if key == "meets_criteria":
                out[key] = False
            elif key == "explanation":
                out[key] = "Объяснение отсутствует"
            else:
                out[key] = 0

    for key in ["accuracy", "completeness", "relevance", "overall_score"]:
        try:
            score = float(out.get(key, 0))
            out[key] = max(0.0, min(10.0, score))
        except Exception:
            out[key] = 0.0

    out["meets_criteria"] = bool(out.get("meets_criteria", False))
    out["explanation"] = _safe_str(out.get("explanation")) or "Объяснение отсутствует"
    return out


def evaluate_answer_litellm(
    cfg: EvaluatorConfig,
    *,
    question: str,
    reference_answer: str,
    requirements: str,
    assistant_answer: str,
) -> Dict[str, Any]:
    if not cfg.api_key:
        return validate_evaluation_result({"explanation": "LLM_API_KEY не задан. Автооценка пропущена."})

    if not all([_safe_str(question), _safe_str(reference_answer), _safe_str(requirements), _safe_str(assistant_answer)]):
        return validate_evaluation_result({"explanation": "Один или несколько обязательных параметров пусты"})

    try:
        import litellm  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "Не найден litellm. Установите зависимости (в проекте он обычно уже есть).\n"
            f"Исходная ошибка импорта: {e}"
        )

    messages = [
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
    ]

    last_err: Optional[str] = None
    for attempt in range(1, cfg.max_retries + 1):
        try:
            resp = litellm.completion(
                model=cfg.model,
                messages=messages,
                api_base=cfg.api_base,
                api_key=cfg.api_key,
                temperature=0.0,
                timeout=cfg.timeout_sec,
                max_retries=0,
                response_format={"type": "json_object"},
            )
            content = ""
            try:
                content = resp["choices"][0]["message"]["content"]  # type: ignore[index]
            except Exception:
                content = str(resp)

            obj = _extract_json_object(content)
            if not obj:
                last_err = f"Некорректный JSON ответ: {content[:200]}"
                time.sleep(1)
                continue

            return validate_evaluation_result(obj)
        except Exception as e:
            last_err = str(e)
            if "401" in last_err or "Unauthorized" in last_err:
                return validate_evaluation_result({"explanation": "LLM 401 Unauthorized: проверьте LLM_API_KEY / LLM_API_URL / LLM_API_MODEL"})
            logger.warning(f"Ошибка запроса к LLM (попытка {attempt}/{cfg.max_retries}): {last_err}")
            time.sleep(2)

    return validate_evaluation_result({"explanation": f"Ошибка оценки: {last_err or 'неизвестно'}"})


class AdkApiClientSync:
    def __init__(self, *, base_url: str, app_name: str, timeout_sec: int = 120):
        self.base_url = base_url.rstrip("/")
        self.app_name = app_name
        self.timeout_sec = timeout_sec

    def ensure_session(self, *, user_id: str, session_id: str) -> None:
        url = f"{self.base_url}/apps/{self.app_name}/users/{user_id}/sessions/{session_id}"
        resp = requests.post(url, json={}, timeout=min(self.timeout_sec, 30))
        if resp.status_code in (200, 201):
            return
        if resp.status_code in (400, 409):
            # ADK sometimes returns "already exists" here
            try:
                data = resp.json()
                detail = _safe_str(data.get("detail")).lower()
                if "exists" in detail or "already" in detail:
                    return
            except Exception:
                pass
        raise RuntimeError(f"ADK ensure_session failed: {resp.status_code} {resp.text[:300]}")

    def delete_session(self, *, user_id: str, session_id: str) -> None:
        url = f"{self.base_url}/apps/{self.app_name}/users/{user_id}/sessions/{session_id}"
        resp = requests.delete(url, timeout=min(self.timeout_sec, 30))
        if resp.status_code in (200, 204, 404):
            return
        raise RuntimeError(f"ADK delete_session failed: {resp.status_code} {resp.text[:300]}")

    def run(self, *, user_id: str, session_id: str, text: str) -> Tuple[str, List[Dict[str, Any]]]:
        url = f"{self.base_url}/run"
        payload = {
            "app_name": self.app_name,
            "user_id": user_id,
            "session_id": session_id,
            "new_message": {"role": "user", "parts": [{"text": text}]},
        }
        resp = requests.post(url, json=payload, timeout=self.timeout_sec)
        if resp.status_code != 200:
            raise RuntimeError(f"ADK run failed: {resp.status_code} {resp.text[:500]}")

        try:
            events = resp.json()
            if not isinstance(events, list):
                events = [{"text": str(events)}]
        except Exception:
            return resp.text.strip(), []

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

            if "model_turn" in event and isinstance(event["model_turn"], dict):
                parts = event["model_turn"].get("parts", []) or []
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    if part.get("thought") is True:
                        continue
                    text = part.get("text")
                    if text:
                        out.append(text)

            if "content" in event:
                content = event["content"]
                if isinstance(content, str):
                    out.append(content)
                elif isinstance(content, dict):
                    if "text" in content and isinstance(content["text"], str):
                        out.append(content["text"])
                    parts = content.get("parts", []) or []
                    for part in parts:
                        if not isinstance(part, dict):
                            continue
                        if part.get("thought") is True:
                            continue
                        text = part.get("text")
                        if text:
                            out.append(text)

            if "text" in event and isinstance(event["text"], str):
                out.append(event["text"])

            if "message" in event and isinstance(event["message"], dict):
                msg = event["message"]
                content = msg.get("content")
                if isinstance(content, str):
                    out.append(content)
                elif isinstance(content, dict):
                    if "text" in content and isinstance(content["text"], str):
                        out.append(content["text"])
                    parts = content.get("parts", []) or []
                    for part in parts:
                        if not isinstance(part, dict):
                            continue
                        if part.get("thought") is True:
                            continue
                        text = part.get("text")
                        if text:
                            out.append(text)

        final = "\n".join(s.strip() for s in out if s and s.strip()).strip()
        return final


def load_test_cases(excel_path: Path):
    pd = _try_import_pandas()
    df = pd.read_excel(excel_path)

    # Normalize expected columns (keep Russian names as canonical)
    col_map = {}
    for c in df.columns:
        c_str = str(c).strip()
        if c_str.lower() in {"#", "no", "num", "номер", "№"}:
            col_map[c] = "№"
        elif c_str.lower() in {"use case", "usecase", "use_case", "кейс", "код use case"}:
            col_map[c] = "Use case"
        elif c_str.lower() in {"вопрос", "вопросы", "question", "questions"}:
            col_map[c] = "Вопросы"
        elif c_str.lower() in {"ожидаемые ответы", "эталонный ответ", "reference answer", "reference"}:
            col_map[c] = "Ожидаемые ответы"
        elif c_str.lower() in {"критерий успеха", "success criteria", "requirements", "criteria"}:
            col_map[c] = "Критерий успеха"

    if col_map:
        df = df.rename(columns=col_map)

    missing = [c for c in ["Use case", "Вопросы", "Ожидаемые ответы", "Критерий успеха"] if c not in df.columns]
    if missing:
        raise RuntimeError(f"В Excel отсутствуют обязательные колонки: {missing}. Найдены колонки: {list(df.columns)}")

    if "№" not in df.columns or df["№"].isnull().any():
        df["№"] = list(range(1, len(df) + 1))

    return df


def save_report(tc_df, *, base_filename: str, output_dir: Path) -> Path:
    pd = _try_import_pandas()
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_df = tc_df.groupby("Use case").agg(
        accuracy=("accuracy", "mean"),
        completeness=("completeness", "mean"),
        relevance=("relevance", "mean"),
        meets_criteria=("meets_criteria", "min"),
        overall_score=("overall_score", "mean"),
        n=("№", "count"),
    ).reset_index()

    summary_df = summary_df.rename(
        columns={
            "Use case": "Код use case",
            "accuracy": "Точность (сред)",
            "completeness": "Полнота (сред)",
            "relevance": "Релевантность (сред)",
            "meets_criteria": "Соответствие всем критериям",
            "overall_score": "Общая оценка (сред)",
            "n": "Количество вопросов",
        }
    )

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

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    report_path = output_dir / f"REPORT_{base_filename}_{timestamp}.xlsx"

    try:
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
    except Exception as e:
        logger.warning(f"Не удалось сохранить отчет через xlsxwriter ({e}). Пытаемся через openpyxl.")
        with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
            summary_df.to_excel(writer, sheet_name="Итоги тестирования", index=False)
            report_df.to_excel(writer, sheet_name="Детали", index=False)

    return report_path


def iter_rows(df) -> Iterable[Tuple[int, Any]]:
    try:
        from tqdm import tqdm  # type: ignore

        return tqdm(df.iterrows(), total=df.shape[0], desc="Обработка вопросов")
    except Exception:
        return df.iterrows()


def main() -> None:
    env_local = SCRIPT_DIR / ".env"
    if env_local.exists():
        load_dotenv(env_local, override=True)
    load_dotenv(override=True)

    parser = argparse.ArgumentParser(description="Evaluator for Telegram bot (Google ADK agent) via ADK HTTP API.")
    parser.add_argument(
        "--excel",
        default=os.getenv("TEST_CASE_XLSX", str(SCRIPT_DIR / "NST-cons use cases pack.xlsx")),
        help="Path to Excel test cases file.",
    )
    parser.add_argument(
        "--out",
        default=os.getenv("TEST_OUTPUT_DIR", str(SCRIPT_DIR / "artifacts")),
        help="Directory to store artifacts (answers, reports).",
    )
    parser.add_argument(
        "--user-id",
        default=os.getenv("ADK_TEST_USER_ID", "tester"),
        help="ADK user_id for the test session.",
    )
    parser.add_argument(
        "--session-id",
        default=os.getenv("ADK_TEST_SESSION_ID", ""),
        help="ADK session_id. If empty, a timestamped id is generated.",
    )
    parser.add_argument(
        "--reset-session",
        action="store_true",
        default=os.getenv("ADK_RESET_SESSION", "0").strip() in ("1", "true", "yes"),
        help="Delete & recreate ADK session before the run.",
    )
    parser.add_argument(
        "--save-events",
        action="store_true",
        default=os.getenv("ADK_SAVE_EVENTS", "0").strip() in ("1", "true", "yes"),
        help="Save raw ADK events for each question to artifacts/events/.",
    )
    args = parser.parse_args()

    excel_path = Path(args.excel).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    adk_base = os.getenv("ADK_API_BASE", "").strip()
    adk_app = os.getenv("ADK_APP_NAME", "agent").strip()
    if not adk_base:
        raise RuntimeError("ADK_API_BASE не задан (например: http://adk-agent:8000).")

    session_id = args.session_id.strip() or f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    user_id = str(args.user_id).strip()

    logger.info(f"Excel: {excel_path}")
    logger.info(f"ADK: base={adk_base}, app={adk_app}, user_id={user_id}, session_id={session_id}")
    logger.info(f"Artifacts dir: {out_dir}")

    tc_df = load_test_cases(excel_path)

    client = AdkApiClientSync(base_url=adk_base, app_name=adk_app, timeout_sec=int(os.getenv("ADK_TIMEOUT_SEC", "180")))

    if args.reset_session:
        try:
            client.delete_session(user_id=user_id, session_id=session_id)
        except Exception as e:
            logger.warning(f"Не удалось удалить сессию (продолжаем): {e}")

    client.ensure_session(user_id=user_id, session_id=session_id)

    events_dir = out_dir / "events"
    if args.save_events:
        events_dir.mkdir(parents=True, exist_ok=True)

    # Ask the agent
    tc_df["answer"] = ""
    tc_df["response_time"] = 0.0
    tc_df["adk_error"] = ""

    for i, row in iter_rows(tc_df):
        q_num = row.get("№")
        question = _safe_str(row.get("Вопросы"))
        if not question:
            tc_df.loc[i, "adk_error"] = "Пустой вопрос"
            continue

        start = time.time()
        try:
            answer, events = client.run(user_id=user_id, session_id=session_id, text=question)
            tc_df.loc[i, "answer"] = answer
            if args.save_events:
                (events_dir / f"q_{int(q_num)}.json").write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            tc_df.loc[i, "adk_error"] = str(e)
            tc_df.loc[i, "answer"] = ""
        finally:
            tc_df.loc[i, "response_time"] = round(time.time() - start, 2)

    # Evaluate
    llm_api_url = os.getenv("LLM_API_URL", "https://dsrv1.llm.nstcloud.ru/v1").strip()
    llm_api_key = os.getenv("LLM_API_KEY", "").strip()
    llm_api_model = os.getenv("LLM_API_MODEL", "Qwen/Qwen3-30B-A3B").strip()

    if not llm_api_url or not llm_api_model:
        logger.warning("LLM_API_URL/LLM_API_MODEL не заданы. Автооценка будет пропущена.")
        tc_df["accuracy"] = 0.0
        tc_df["completeness"] = 0.0
        tc_df["relevance"] = 0.0
        tc_df["meets_criteria"] = False
        tc_df["overall_score"] = 0.0
        tc_df["explanation"] = "Автооценка пропущена: нет LLM_API_URL/LLM_API_MODEL"
    else:
        cfg = EvaluatorConfig(api_base=llm_api_url, api_key=llm_api_key, model=llm_api_model)
        tc_df["accuracy"] = 0.0
        tc_df["completeness"] = 0.0
        tc_df["relevance"] = 0.0
        tc_df["meets_criteria"] = False
        tc_df["overall_score"] = 0.0
        tc_df["explanation"] = ""

        for i, row in iter_rows(tc_df):
            question = _safe_str(row.get("Вопросы"))
            reference_answer = _safe_str(row.get("Ожидаемые ответы"))
            requirements = _safe_str(row.get("Критерий успеха"))
            assistant_answer = _safe_str(row.get("answer"))

            ev = evaluate_answer_litellm(
                cfg,
                question=question,
                reference_answer=reference_answer,
                requirements=requirements,
                assistant_answer=assistant_answer,
            )

            tc_df.loc[i, "accuracy"] = ev["accuracy"]
            tc_df.loc[i, "completeness"] = ev["completeness"]
            tc_df.loc[i, "relevance"] = ev["relevance"]
            tc_df.loc[i, "meets_criteria"] = ev["meets_criteria"]
            tc_df.loc[i, "overall_score"] = ev["overall_score"]
            tc_df.loc[i, "explanation"] = ev["explanation"]

    # Save artifacts
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    base_filename = excel_path.stem

    answers_csv = out_dir / f"answers_{base_filename}_{timestamp}.csv"
    tc_df.to_csv(answers_csv, index=False, encoding="utf-8-sig")
    logger.info(f"Answers CSV saved: {answers_csv}")

    report_path = save_report(tc_df, base_filename=base_filename, output_dir=out_dir)
    logger.info(f"Report saved: {report_path}")


if __name__ == "__main__":
    main()

