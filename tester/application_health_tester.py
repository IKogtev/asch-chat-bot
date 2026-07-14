"""
Application health tester: two-sheet Excel (questions + triggers) -> ADK -> trigger check + LLM eval -> report.

Reuses interrogation and evaluation from adk_agent_tester.py; adds deterministic trigger matching
against the Triggers sheet (malfunction / error replies from the Telegram bot).
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Tuple, Union

import pandas as pd
from dotenv import load_dotenv

from adk_agent_tester import (
    ADK_API_BASE,
    ADK_APP_NAME,
    ADK_TIMEOUT_SEC,
    ASK_QUESTIONS,
    DEFAULT_ADK_TEST_USER_ID,
    LLM_API_KEY,
    LLM_API_MODEL,
    LLM_API_URL,
    SCRIPT_DIR,
    AdkApiClient,
    build_adk_profile_state_delta,
    check_adk_health,
    evaluate_all,
    format_adk_environment_label,
    init_evaluator,
    interrogate_agent,
    load_test_cases,
    parse_adk_user_id,
)

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO), format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MIN_SIGNATURE_LEN = 20
STABLE_PREFIX_LEN = 40

TRIGGER_CAUSE_COL = "Причина"
TRIGGER_TEXT_COL = "Ответ сервиса"

QUESTIONS_SHEET_CANDIDATES = ("Questions", "Вопросы")
TRIGGERS_SHEET_CANDIDATES = ("Триггеры", "Triggers")


@dataclass
class TriggerHit:
    причина: str
    matched_fragment: str
    trigger_template: str


def _resolve_sheet(
    xl: pd.ExcelFile,
    *,
    preferred: Tuple[str, ...],
    fallback: Union[int, None] = 0,
    required: bool = False,
) -> Union[str, int]:
    names = xl.sheet_names
    lower_map = {n.lower(): n for n in names}
    for candidate in preferred:
        if candidate in names:
            return candidate
        if candidate.lower() in lower_map:
            return lower_map[candidate.lower()]
    if fallback is not None:
        if isinstance(fallback, int):
            if 0 <= fallback < len(names):
                return fallback
        elif fallback in names:
            return fallback
    if required:
        raise ValueError(
            f"Не найден лист из {preferred!r}. Доступные листы: {names}"
        )
    raise ValueError(f"Не удалось определить лист. Доступные: {names}")


def load_triggers(file_name: Path, sheet_name: Union[str, int]) -> pd.DataFrame:
    df = pd.read_excel(file_name, sheet_name=sheet_name)
    missing = [c for c in (TRIGGER_CAUSE_COL, TRIGGER_TEXT_COL) if c not in df.columns]
    if missing:
        raise ValueError(
            f"Лист триггеров {sheet_name!r} должен содержать колонки {TRIGGER_CAUSE_COL!r} и {TRIGGER_TEXT_COL!r}. "
            f"Найдены: {list(df.columns)}"
        )
    return df.dropna(subset=[TRIGGER_TEXT_COL]).reset_index(drop=True)


def load_test_workbook(
    path: Path,
    *,
    questions_sheet: Optional[str] = None,
    triggers_sheet: Optional[str] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    xl = pd.ExcelFile(path)
    q_sheet: Union[str, int]
    if questions_sheet:
        q_sheet = questions_sheet
    else:
        q_sheet = _resolve_sheet(xl, preferred=QUESTIONS_SHEET_CANDIDATES, fallback=0)

    t_sheet: Union[str, int]
    if triggers_sheet:
        t_sheet = triggers_sheet
    else:
        t_sheet = _resolve_sheet(xl, preferred=TRIGGERS_SHEET_CANDIDATES, fallback=None, required=True)

    questions_df = load_test_cases(path, sheet_name=q_sheet)
    triggers_df = load_triggers(path, t_sheet)
    logger.info(
        "Загружено: вопросов=%s (лист %r), триггеров=%s (лист %r)",
        len(questions_df),
        q_sheet,
        len(triggers_df),
        t_sheet,
    )
    return questions_df, triggers_df


def normalize_text(text: str) -> str:
    s = (text or "").strip().lstrip("\ufeff")
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def strip_user_echo(text: str) -> str:
    s = text or ""
    s = re.sub(r"Ваш запрос\s*:\s*«[^»]*»\s*$", "", s, flags=re.IGNORECASE | re.MULTILINE)
    s = re.sub(r"Ваш запрос\s*:\s*…\s*$", "", s, flags=re.IGNORECASE | re.MULTILINE)
    s = re.sub(r"«…»\s*$", "", s)
    return normalize_text(s)


def _extract_code_literals(template: str) -> List[str]:
    literals: List[str] = []
    for m in re.finditer(r'""([^"]+)""', template):
        lit = m.group(1).replace("\\n", "\n").replace('\\"', '"')
        lit = strip_user_echo(lit)
        if len(lit) >= MIN_SIGNATURE_LEN:
            literals.append(lit)
    if not literals:
        for m in re.finditer(r'"([^"]{20,})"', template):
            lit = strip_user_echo(m.group(1).replace("\\n", "\n"))
            if len(lit) >= MIN_SIGNATURE_LEN:
                literals.append(lit)
    return literals


def build_trigger_signatures(template: str) -> List[str]:
    raw = (template or "").strip()
    if not raw:
        return []

    if '""' in raw or "timeout_msg" in raw or '"""' in raw:
        return _extract_code_literals(raw)

    sig = strip_user_echo(raw)
    return [sig] if sig and len(sig) >= MIN_SIGNATURE_LEN else []


def _signature_matches_answer(norm_answer: str, signature: str, raw_signature: str) -> Optional[str]:
    if not norm_answer or not signature:
        return None

    norm_sig = normalize_text(signature)
    if len(norm_sig) < MIN_SIGNATURE_LEN:
        return None

    stripped_raw = strip_user_echo(raw_signature)
    wildcard_re = r"«…»|(?<![.])…(?![.])"
    has_wildcards = bool(re.search(wildcard_re, stripped_raw))

    if has_wildcards:
        first_wildcard = re.search(wildcard_re, stripped_raw)
        if first_wildcard:
            prefix = normalize_text(stripped_raw[: first_wildcard.start()])
            if len(prefix) >= MIN_SIGNATURE_LEN and prefix in norm_answer:
                return prefix[:80]

        parts = re.split(wildcard_re, stripped_raw)
        segments = [normalize_text(p) for p in parts if normalize_text(p) and len(normalize_text(p)) >= 15]
        if not segments:
            return None
        pos = 0
        for seg in segments:
            idx = norm_answer.find(seg, pos)
            if idx < 0:
                return None
            pos = idx + len(seg)
        return segments[0][:80] if segments else norm_sig[:80]

    prefix_len = min(STABLE_PREFIX_LEN, len(norm_sig))
    needle = norm_sig if len(norm_sig) <= STABLE_PREFIX_LEN else norm_sig[:prefix_len]
    if needle in norm_answer:
        return needle
    if len(norm_sig) > STABLE_PREFIX_LEN and norm_sig in norm_answer:
        return norm_sig[:80]
    return None


def match_triggers(answer: str, triggers_df: pd.DataFrame) -> List[TriggerHit]:
    norm_answer = normalize_text(answer)
    if not norm_answer:
        return []

    hits: List[TriggerHit] = []
    for _, row in triggers_df.iterrows():
        причина = str(row.get(TRIGGER_CAUSE_COL, "") or "").strip()
        template = str(row.get(TRIGGER_TEXT_COL, "") or "").strip()
        if not template:
            continue

        for signature in build_trigger_signatures(template):
            fragment = _signature_matches_answer(norm_answer, signature, template)
            if fragment is not None:
                hits.append(
                    TriggerHit(
                        причина=причина,
                        matched_fragment=fragment,
                        trigger_template=template[:200],
                    )
                )
                break

    return hits


def check_all_triggers(tc_df: pd.DataFrame, triggers_df: pd.DataFrame) -> pd.DataFrame:
    tc_df = tc_df.copy()
    tc_df["trigger_matched"] = False
    tc_df["trigger_cause"] = ""
    tc_df["trigger_matched_text"] = ""

    for i, row in tc_df.iterrows():
        answer = str(row.get("answer", "") or "")
        question = str(row.get("Вопросы", "") or "")
        hits = match_triggers(answer, triggers_df)

        if hits:
            hit = hits[0]
            tc_df.loc[i, "trigger_matched"] = True
            tc_df.loc[i, "trigger_cause"] = hit.причина
            tc_df.loc[i, "trigger_matched_text"] = hit.matched_fragment
            answer_preview = answer[:200] + ("..." if len(answer) > 200 else "")
            logger.warning(
                "TRIGGER HIT | Вопрос: %s | Ответ: %s | Причина: %s",
                question,
                answer_preview,
                hit.причина,
            )

    total_hits = int(tc_df["trigger_matched"].sum())
    logger.info("Срабатываний триггеров: %s из %s вопросов", total_hits, len(tc_df))
    return tc_df


def apply_trigger_override_to_evaluation(tc_df: pd.DataFrame) -> pd.DataFrame:
    tc_df = tc_df.copy()
    if "meets_criteria" not in tc_df.columns:
        tc_df["meets_criteria"] = True
    if "explanation" not in tc_df.columns:
        tc_df["explanation"] = ""

    for i, row in tc_df.iterrows():
        if not row.get("trigger_matched"):
            continue
        cause = str(row.get("trigger_cause", "") or "")
        tc_df.loc[i, "meets_criteria"] = False
        prefix = f"Срабатывание триггера: {cause}."
        existing = str(row.get("explanation", "") or "")
        if existing and not existing.startswith(prefix):
            tc_df.loc[i, "explanation"] = f"{prefix} {existing}"
        elif not existing:
            tc_df.loc[i, "explanation"] = prefix

    return tc_df


def save_health_check_report(
    tc_df: pd.DataFrame,
    *,
    base_filename: str,
    output_dir: Path,
    adk_api_base: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    trigger_hits = tc_df[tc_df["trigger_matched"] == True]  # noqa: E712

    summary_rows = []
    if "Use case" in tc_df.columns:
        for use_case, group in tc_df.groupby("Use case"):
            summary_rows.append(
                {
                    "Код use case": use_case,
                    "Всего вопросов": len(group),
                    "Срабатываний триггеров": int(group["trigger_matched"].sum()),
                    "Соответствие критериям (LLM)": int(group.get("meets_criteria", pd.Series(dtype=bool)).sum()),
                    "Общая оценка (сред)": round(float(group.get("overall_score", pd.Series(dtype=float)).mean() or 0), 2),
                }
            )
    else:
        summary_rows.append(
            {
                "Код use case": "all",
                "Всего вопросов": len(tc_df),
                "Срабатываний триггеров": int(tc_df["trigger_matched"].sum()),
                "Соответствие критериям (LLM)": int(tc_df.get("meets_criteria", pd.Series(dtype=bool)).sum()),
                "Общая оценка (сред)": round(float(tc_df.get("overall_score", pd.Series(dtype=float)).mean() or 0), 2),
            }
        )
    summary_df = pd.DataFrame(summary_rows)

    report_datetime = datetime.now()
    timestamp = report_datetime.strftime("%Y-%m-%d_%H-%M")
    report_datetime_display = report_datetime.strftime("%Y-%m-%d %H:%M")
    adk_environment_display = format_adk_environment_label(adk_api_base)
    report_path = output_dir / f"HEALTH_CHECK_REPORT_{base_filename}_{timestamp}.xlsx"

    if "answer_raw" not in tc_df.columns:
        tc_df = tc_df.copy()
        tc_df["answer_raw"] = tc_df["answer"]

    rename_map = {
        "Use case": "Код use case",
        "Dialog tag": "Dialog tag",
        "answer": "Ответ ADK Agent",
        "answer_raw": "Ответ ADK Agent (raw)",
        "adk_session_id": "ADK session id",
        "response_time": "Время ответа (сек)",
        "trigger_matched": "Срабатывание триггера",
        "trigger_cause": "Причина (триггер)",
        "trigger_matched_text": "Совпавший фрагмент",
        "accuracy": "Точность",
        "completeness": "Полнота",
        "relevance": "Релевантность",
        "meets_criteria": "Соответствие критериям",
        "overall_score": "Общая оценка",
        "explanation": "Объяснение",
    }
    report_df = tc_df.rename(columns=rename_map)

    detail_columns = [
        "Код use case",
        "Dialog tag",
        "№",
        "Вопросы",
        "Ожидаемые ответы",
        "Критерий успеха",
        "ADK session id",
        "Ответ ADK Agent",
        "Ответ ADK Agent (raw)",
        "Время ответа (сек)",
        "Срабатывание триггера",
        "Причина (триггер)",
        "Совпавший фрагмент",
        "Точность",
        "Полнота",
        "Релевантность",
        "Соответствие критериям",
        "Общая оценка",
        "Объяснение",
    ]
    detail_columns = [c for c in detail_columns if c in report_df.columns]

    trigger_report_df = report_df[report_df.get("Срабатывание триггера", False) == True]  # noqa: E712
    trigger_sheet_cols = [c for c in ("№", "Вопросы", "Ответ ADK Agent", "Причина (триггер)", "Совпавший фрагмент") if c in trigger_report_df.columns]

    with pd.ExcelWriter(report_path, engine="xlsxwriter") as writer:
        meta = pd.DataFrame(
            [
                {"Параметр": "Файл", "Значение": base_filename},
                {"Параметр": "ADK environment", "Значение": adk_environment_display},
                {"Параметр": "Сгенерировано", "Значение": report_datetime_display},
                {"Параметр": "Всего вопросов", "Значение": len(tc_df)},
                {"Параметр": "Срабатываний триггеров", "Значение": len(trigger_report_df)},
            ]
        )
        meta.to_excel(writer, sheet_name="Итоги тестирования", index=False)
        summary_df.to_excel(writer, sheet_name="Сводка по use case", index=False)

        if not trigger_report_df.empty and trigger_sheet_cols:
            trigger_report_df[trigger_sheet_cols].to_excel(
                writer, sheet_name="Срабатывания триггеров", index=False
            )

        if "Код use case" in report_df.columns:
            for use_case, use_case_df in report_df.groupby("Код use case"):
                sheet = f"Детали {str(use_case)[:20]}"
                use_case_df[detail_columns].to_excel(writer, sheet_name=sheet, index=False)
        else:
            report_df[detail_columns].to_excel(writer, sheet_name="Детали", index=False)

    return report_path


def main() -> int:
    env_local = SCRIPT_DIR / ".env"
    if env_local.exists():
        load_dotenv(env_local, override=True)
    load_dotenv(override=True)

    parser = argparse.ArgumentParser(
        description="Application health tester (Excel questions + triggers -> ADK -> trigger check + LLM eval -> report)."
    )
    parser.add_argument(
        "--excel",
        default=None,
        help="Excel with questions and triggers sheets (or set TC_TASK_FILE_NAME)",
    )
    parser.add_argument("--out", default=str(SCRIPT_DIR))
    parser.add_argument("--questions-sheet", default=None, help="Override questions sheet name")
    parser.add_argument("--triggers-sheet", default=None, help="Override triggers sheet name")
    parser.add_argument(
        "--user-id",
        default=os.getenv("ADK_TEST_USER_ID", DEFAULT_ADK_TEST_USER_ID),
        type=parse_adk_user_id,
    )
    parser.add_argument(
        "--session-id",
        default=os.getenv("ADK_TEST_SESSION_ID"),
        help="ADK session_id for legacy files without Dialog tag column",
    )
    parser.add_argument(
        "--fake-first-name",
        default=os.getenv("ADK_TEST_FIRST_NAME"),
    )
    args = parser.parse_args()

    if not ADK_API_BASE:
        raise RuntimeError(
            "ADK_API_BASE не задан.\n"
            "Для k8s извне это должен быть URL Ingress (например https://adk-agent-...);\n"
            "внутри кластера может быть http://adk-agent:8000."
        )

    user_id = str(args.user_id)
    session_id = (args.session_id or "").strip() or None

    excel_input = (args.excel or os.getenv("TC_TASK_FILE_NAME", "")).strip()
    if not excel_input:
        parser.error("--excel is required (or set TC_TASK_FILE_NAME)")

    excel_path = Path(excel_input).expanduser()
    if not excel_path.is_absolute():
        excel_path = (SCRIPT_DIR / excel_path).resolve()
    else:
        excel_path = excel_path.resolve()
    output_dir = Path(args.out).expanduser().resolve()

    if not excel_path.exists():
        raise FileNotFoundError(f"Excel не найден: {excel_path}")

    if not check_adk_health(ADK_API_BASE, timeout_sec=5):
        raise RuntimeError("ADK agent недоступен по ADK_API_BASE (проверьте Ingress/Firewall/DNS).")

    tc_df, triggers_df = load_test_workbook(
        excel_path,
        questions_sheet=args.questions_sheet,
        triggers_sheet=args.triggers_sheet,
    )

    profile = build_adk_profile_state_delta(
        first_name=args.fake_first_name.strip() if args.fake_first_name else None
    )
    logger.info("ADK stateDelta profile keys: %s", sorted(profile.keys()))
    client = AdkApiClient(
        ADK_API_BASE,
        ADK_APP_NAME,
        timeout_sec=ADK_TIMEOUT_SEC,
        profile_state_delta=profile,
    )

    answers_file_path = SCRIPT_DIR / f"health_check_answers_{excel_path.stem}.parquet"
    tc_df = interrogate_agent(
        client,
        user_id=user_id,
        session_id=session_id,
        tc_df=tc_df,
        answers_file_path=answers_file_path,
        ask_questions=ASK_QUESTIONS,
    )

    tc_df = check_all_triggers(tc_df, triggers_df)

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

    tc_df = apply_trigger_override_to_evaluation(tc_df)

    report_path = save_health_check_report(
        tc_df,
        base_filename=excel_path.stem,
        output_dir=output_dir,
        adk_api_base=ADK_API_BASE,
    )
    logger.info("Отчет сохранен: %s", report_path)

    trigger_count = int(tc_df["trigger_matched"].sum())
    if trigger_count > 0:
        logger.error("Тест завершен с ошибкой: %s срабатываний триггеров", trigger_count)
        return 1

    logger.info("Тест завершен успешно: срабатываний триггеров нет")
    return 0


if __name__ == "__main__":
    sys.exit(main())
