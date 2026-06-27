"""Dialogue steering: CTA, smalltalk limits, clarification, soul smalltalk."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

DIALOG_STATE_KEYS = (
    "dialog_phase",
    "dialog_topic",
    "smalltalk_turns",
    "last_route",
    "last_cta",
    "pending_clarification",
    "session_intro_done",
    "smalltalk_kind",
)

_SMALLTALK_REDIRECT = (
    "Давайте к рабочим вопросам — документы или продукты АСЖ, что интересует?",
    "Вернёмся к делу? Могу помочь с материалами или продуктами АСЖ.",
    "Ок, по работе — что сейчас актуально: документы или продукт?",
)

_CAPABILITIES = (
    "Помогаю с документами и вопросами по продуктам АСЖ. Что сейчас нужно?",
    "Ищу материалы и отвечаю по продуктам АСЖ. С чего начнём?",
    "Документы, продукты, условия АСЖ — по этому на связи. Чем заняться?",
)

_INTRO_WITH_NAME = (
    "{name}, добрый день! Я Настя — помощник по продуктам и материалам АСЖ.",
    "{name}, здравствуйте! Настя на связи — помогу с продуктами и материалами АСЖ.",
    "Добрый день, {name}! Я Настя, ваш помощник по материалам АСЖ.",
)

_INTRO_NO_NAME = (
    "Добрый день! Я Настя — помощник по продуктам и материалам АСЖ.",
    "Здравствуйте! Настя на связи — помогу с продуктами и материалами АСЖ.",
    "Добрый день! Я Настя, ваш помощник по материалам АСЖ.",
)

_REPEAT_GREETING = ("Здравствуйте.", "Добрый день.", "На связи.")

_MEETING_GREETING = (
    "Здравствуйте. Чем помочь к созвону?",
    "На связи. К созвону что подготовить?",
)

_THANKS = (
    "Пожалуйста.",
    "Пожалуйста. Обращайтесь, если понадобится.",
    "Рада была помочь.",
)

_THANKS_AFTER_HELP = ("Рада была помочь.", "Рада, что пригодилось.", "Хорошо, что помогло.")

_FAREWELL_EVENING = ("Хорошего вечера!", "Хорошего вечера. До связи!", "И вам хорошего вечера!")

_FAREWELL_OTHER = ("До связи!", "На связи!", "Хорошего дня!")

_DEFER = (
    "Хорошо. Буду на связи, когда понадоблюсь.",
    "Конечно. Напишите, когда будете готовы продолжить.",
    "Принято. Обращайтесь, когда будет удобно.",
)

_CHITCHAT = (
    "Спасибо, всё в порядке. Чем помочь по работе?",
    "Всё хорошо, спасибо. Что сейчас актуально?",
    "Нормально, спасибо. По работе что нужно?",
)

_OTHER_SOCIAL = ("Чем могу помочь?", "Что сейчас нужно?", "С чем помочь?")

_KB_CTA = (
    "Если нужно — покажу карточку продукта или подберу документы.",
    "Могу прислать карточку или комплект материалов — скажите.",
    "Нужна карточка продукта или пакет документов — напишите.",
)

_DOC_CTA = (
    "Могу показать ещё документы или отправить файлы по номерам из списка.",
    "Если нужно — подберу другие документы или пришлю файлы.",
    "Скажите, если нужны ещё материалы или файлы по номерам.",
)

_VAGUE_KB_RE = re.compile(
    r"^\s*(?:расскаж(?:и|ите)|покаж(?:и|ите)|объясн(?:и|ите)|что\s+(?:такое|это))\s+"
    r"(?:про\s+)?(?:страхован(?:ие|ия)|продукт(?:ы)?|полис(?:ы)?)\s*[?.!]*\s*$",
    re.IGNORECASE,
)

_PRODUCT_CODE_RE = re.compile(r"\b\d{3,}\b")
_PRODUCT_NAME_RE = re.compile(
    r"\b(?:fort\s*knox|fn|накопительн\w*\s+страхован\w*)\b",
    re.IGNORECASE,
)

_SOCIAL_SMALLTALK_RE = re.compile(
    r"(?:"
    r"что\s+нового|как\s+дела|как\s+жизнь|"
    r"на\s+связи|до\s+завтра|до\s+понедельника|"
    r"хорошего\s+(?:вечера|дня|утра)|"
    r"на\s+сегодня\s*,?\s*всё|на\s+сегодня\s*,?\s*кажется\s*,?\s*всё|"
    r"позже\s+вернусь|вернусь\s+позже|"
    r"завтра\s+продолжим|продолжим\s+завтра|"
    r"тогда\s+начну|начну\s+с\s+простого|"
    r"^(?:ок|понял|понятно|ладно|хорошо)\s*[,.!]*\s*$|"
    r"^(?:ок\s+)?(?:спс|спасибо)\s*[!.]*\s*$|"
    r"этого\s+достаточно|на\s+сегодня"
    r")",
    re.IGNORECASE,
)

_GREETING_RE = re.compile(
    r"^\s*(?:привет|здравствуй|добр(?:ое|ый)\s+(?:утро|день|вечер)|hello|hi)\b",
    re.IGNORECASE,
)

_THANKS_RE = re.compile(
    r"(?:спасибо|благодар|пожалуйста\s*$|thanks)",
    re.IGNORECASE,
)

_CAPABILITIES_RE = re.compile(
    r"(?:"
    r"что\s+(?:ты\s+)?умеешь|что\s+умеешь|"
    r"что\s+(?:ты\s+)?можешь|чем\s+(?:ты\s+)?можешь\s+помочь|"
    r"чем\s+можешь\s+помочь|какие\s+(?:у\s+тебя\s+)?возможност|"
    r"на\s+что\s+(?:ты\s+)?способен|"
    r"чем\s+можешь\s+помочь\s+в\s+работе"
    r")",
    re.IGNORECASE,
)

_FAREWELL_RE = re.compile(
    r"(?:хорошего\s+(?:вечера|дня|утра)|на\s+связи|до\s+завтра|до\s+свидания)",
    re.IGNORECASE,
)

_DEFER_RE = re.compile(
    r"(?:"
    r"позже\s+вернусь|вернусь\s+позже|"
    r"на\s+сегодня\s*,?\s*(?:всё|кажется)|"
    r"завтра\s+продолжим|"
    r"тогда\s+начну|начну\s+с\s+простого|"
    r"этого\s+достаточно|на\s+сегодня\s*,?\s*кажется\s*,?\s*всё"
    r")",
    re.IGNORECASE,
)

_CLARIFICATION_ANSWER_RE = re.compile(
    r"^\s*(?:именно\s+)?(?:fort\s*knox|fn|"
    r"[\w\-]{2,30}(?:\s+[\w\-]{2,20}){0,2})\s*[?.!]*\s*$",
    re.IGNORECASE,
)


@dataclass
class DialogState:
    dialog_phase: str = "working"
    dialog_topic: str = ""
    smalltalk_turns: int = 0
    last_route: str = ""
    last_cta: str = ""
    pending_clarification: bool = False
    session_intro_done: bool = False


def _read_state(session_state: Dict[str, Any]) -> DialogState:
    return DialogState(
        dialog_phase=str(session_state.get("dialog_phase") or "working"),
        dialog_topic=str(session_state.get("dialog_topic") or ""),
        smalltalk_turns=int(session_state.get("smalltalk_turns") or 0),
        last_route=str(session_state.get("last_route") or ""),
        last_cta=str(session_state.get("last_cta") or ""),
        pending_clarification=bool(session_state.get("pending_clarification")),
        session_intro_done=bool(session_state.get("session_intro_done")),
    )


def _write_state(session_state: Dict[str, Any], state: DialogState) -> None:
    session_state["dialog_phase"] = state.dialog_phase
    session_state["dialog_topic"] = state.dialog_topic
    session_state["smalltalk_turns"] = state.smalltalk_turns
    session_state["last_route"] = state.last_route
    session_state["last_cta"] = state.last_cta
    session_state["pending_clarification"] = state.pending_clarification
    session_state["session_intro_done"] = state.session_intro_done


def _with_name(first_name: str, message: str) -> str:
    """Legacy helper — prefer explicit name only in first greeting."""
    name = (first_name or "").strip()
    if not name or name.lower() == "unknown":
        return message
    if message.startswith(f"{name},"):
        return message
    return f"{name}, {message[0].lower()}{message[1:]}" if message else message


def _maybe_name(
    first_name: str,
    message: str,
    *,
    use_name: bool,
) -> str:
    """Имя только когда явно разрешено (первое приветствие)."""
    if not use_name:
        return message
    return _with_name(first_name, message)


def _variant_index(seed: str, turn_index: int, count: int) -> int:
    if count <= 0:
        return 0
    acc = turn_index * 131
    for ch in seed or "":
        acc = (acc * 31 + ord(ch)) % (2**32)
    return acc % count


def _pick_variant(
    variants: tuple[str, ...],
    *,
    seed: str,
    turn_index: int = 0,
) -> str:
    return variants[_variant_index(seed, turn_index, len(variants))]


def is_social_smalltalk(user_text: str) -> bool:
    """Social/defer/farewell/chitchat — never needs_clarification."""
    text = (user_text or "").strip()
    if not text:
        return False
    if _CAPABILITIES_RE.search(text):
        return True
    if _GREETING_RE.search(text) or _THANKS_RE.search(text):
        return True
    if _FAREWELL_RE.search(text) or _DEFER_RE.search(text):
        return True
    return bool(_SOCIAL_SMALLTALK_RE.search(text))


def classify_smalltalk_kind(user_text: str) -> str:
    text = (user_text or "").strip()
    if _CAPABILITIES_RE.search(text):
        return "capabilities"
    if _GREETING_RE.search(text):
        return "greeting"
    if _THANKS_RE.search(text):
        return "thanks"
    if _FAREWELL_RE.search(text):
        return "farewell"
    if _DEFER_RE.search(text):
        return "defer"
    if re.search(r"что\s+нового|как\s+дела|как\s+жизнь", text, re.IGNORECASE):
        return "chitchat"
    return "other"


def should_clarify(dispatch: Dict[str, Any], user_text: str) -> bool:
    """Rule-based: vague product/insurance questions without entity."""
    if is_social_smalltalk(user_text):
        return False
    if dispatch.get("intent") != "kb_answer":
        return False
    text = (user_text or "").strip()
    if _PRODUCT_CODE_RE.search(text) or _PRODUCT_NAME_RE.search(text):
        return False
    if len(text.split()) <= 6 and _VAGUE_KB_RE.match(text):
        return True
    lower = text.lower()
    vague_phrases = (
        "расскажи про продукт",
        "расскажите про продукт",
        "расскажи про страхование",
        "расскажите про страхование",
        "что за продукт",
        "про продукт",
    )
    return any(lower.startswith(p) or lower == p.rstrip() for p in vague_phrases)


def adjust_dispatch(
    dispatch: Dict[str, Any],
    user_text: str,
    session_state: Dict[str, Any],
) -> Dict[str, Any]:
    """Fix common misroutes before leaf agents run."""
    out = dict(dispatch or {})
    text = (user_text or "").strip()
    if not text:
        return out

    followup = resolve_clarification_followup(session_state, user_text)
    if followup:
        return followup

    if is_social_smalltalk(text):
        out["route"] = "kb_answer"
        out["intent"] = "smalltalk"
        out["search_query"] = ""
        out["reason"] = f"smalltalk_{classify_smalltalk_kind(text)}"
        return out

    if out.get("intent") == "needs_clarification" and is_social_smalltalk(text):
        out["intent"] = "smalltalk"
        out["search_query"] = ""
    return out


def resolve_clarification_followup(
    session_state: Dict[str, Any],
    user_text: str,
) -> Optional[Dict[str, Any]]:
    """Short answer after vague product question — treat as kb_answer."""
    state = _read_state(session_state)
    if not state.pending_clarification:
        return None
    text = (user_text or "").strip()
    if not text or len(text.split()) > 6:
        return None
    if not _CLARIFICATION_ANSWER_RE.match(text) and not _PRODUCT_NAME_RE.search(text):
        return None
    if is_social_smalltalk(text):
        return None
    return {
        "status": "ok",
        "route": "kb_answer",
        "intent": "kb_answer",
        "reason": "clarification_followup",
        "search_query": text,
    }


def render_smalltalk_reply(
    session_state: Dict[str, Any],
    user_text: str,
    *,
    first_name: str = "",
) -> Optional[str]:
    """
    Deterministic warm smalltalk — fast, no repeat intro, «душа» без слащавости.
    Returns None if kb_answer LLM should handle (non-smalltalk).
    """
    kind = classify_smalltalk_kind(user_text)
    state = _read_state(session_state)
    name = (first_name or "").strip()
    seed = (user_text or "").strip().lower()
    turn_idx = state.smalltalk_turns

    if kind == "capabilities":
        return _pick_variant(_CAPABILITIES, seed=seed, turn_index=turn_idx)

    if kind == "greeting":
        if not state.session_intro_done:
            if re.search(r"созвон|встреч|клиент", user_text, re.IGNORECASE):
                return _pick_variant(_MEETING_GREETING, seed=seed, turn_index=turn_idx)
            if name:
                tpl = _pick_variant(_INTRO_WITH_NAME, seed=seed, turn_index=turn_idx)
                return tpl.format(name=name)
            return _pick_variant(_INTRO_NO_NAME, seed=seed, turn_index=turn_idx)
        return _pick_variant(_REPEAT_GREETING, seed=seed, turn_index=turn_idx)

    if kind == "thanks":
        if re.search(r"помог", user_text, re.IGNORECASE):
            return _pick_variant(_THANKS_AFTER_HELP, seed=seed, turn_index=turn_idx)
        return _pick_variant(_THANKS, seed=seed, turn_index=turn_idx)

    if kind == "farewell":
        if re.search(r"хорошего\s+вечера", user_text, re.IGNORECASE):
            return _pick_variant(_FAREWELL_EVENING, seed=seed, turn_index=turn_idx)
        return _pick_variant(_FAREWELL_OTHER, seed=seed, turn_index=turn_idx)

    if kind == "defer":
        if re.search(r"fort\s*knox|\bfn\b", user_text, re.IGNORECASE):
            return "Хорошо. Завтра помогу с Fort Knox."
        return _pick_variant(_DEFER, seed=seed, turn_index=turn_idx)

    if kind == "chitchat":
        return _pick_variant(_CHITCHAT, seed=seed, turn_index=turn_idx)

    if kind == "other" and is_social_smalltalk(user_text):
        return _pick_variant(_OTHER_SOCIAL, seed=seed, turn_index=turn_idx)

    return None


def mark_session_intro_done(session_state: Dict[str, Any], *, kind: str) -> None:
    state = _read_state(session_state)
    if kind == "greeting" and not state.session_intro_done:
        state.session_intro_done = True
    _write_state(session_state, state)
    session_state["smalltalk_kind"] = kind


def handle_smalltalk_limit(state: DialogState) -> Optional[str]:
    """Return redirect on 3rd consecutive off-topic smalltalk (after 2 prior)."""
    if state.smalltalk_turns >= 2:
        return _pick_variant(
            _SMALLTALK_REDIRECT,
            seed="redirect",
            turn_index=state.smalltalk_turns,
        )
    return None


def build_cta(
    state: DialogState,
    *,
    route: str,
    intent: str,
    content_message: str,
    content_mode: str = "text_answer",
) -> Optional[str]:
    """Rule-based CTA whitelist; at most one CTA, no repeat from last turn."""
    if content_mode == "no_data":
        return None

    route = str(route or "")
    intent = str(intent or "")
    message = (content_message or "").lower()

    cta: Optional[str] = None
    if route == "kb_answer" and intent == "kb_answer" and content_mode == "text_answer":
        if re.search(r"\b\d{3,}\b", content_message or "") or any(
            w in message for w in ("продукт", "полис", "страхован")
        ):
            cta = _pick_variant(
                _KB_CTA,
                seed=(content_message or "")[:80],
                turn_index=state.smalltalk_turns,
            )
    elif route == "doc_search" and intent == "doc_search":
        cta = _pick_variant(
            _DOC_CTA,
            seed=(content_message or "")[:80],
            turn_index=state.smalltalk_turns,
        )

    if cta and cta == state.last_cta:
        return None
    return cta


def update_dialog_state(
    session_state: Dict[str, Any],
    *,
    dispatch: Dict[str, Any],
    content_message: str,
    user_text: str = "",
    cta_used: Optional[str] = None,
    redirect_applied: bool = False,
) -> DialogState:
    state = _read_state(session_state)
    route = str(dispatch.get("route") or "")
    intent = str(dispatch.get("intent") or "")

    state.last_route = route
    chitchat_kinds = {"chitchat", "other"}
    kind = classify_smalltalk_kind(user_text or content_message) if intent == "smalltalk" else ""

    if route == "kb_answer" and intent == "smalltalk":
        if redirect_applied:
            state.smalltalk_turns = 0
        elif kind in chitchat_kinds or kind == "capabilities":
            state.smalltalk_turns += 1
        else:
            state.smalltalk_turns = 0
    else:
        state.smalltalk_turns = 0
        if route == "kb_answer" and intent == "kb_answer":
            state.dialog_topic = (content_message or "")[:120]

    if intent == "needs_clarification":
        state.pending_clarification = True
    elif route == "kb_answer" and intent == "kb_answer":
        state.pending_clarification = False
    elif intent == "smalltalk" and kind in ("defer", "farewell"):
        state.pending_clarification = False

    if intent == "smalltalk" and kind == "greeting":
        state.session_intro_done = True

    if cta_used:
        state.last_cta = cta_used
    elif route != state.last_route:
        state.last_cta = ""

    if intent == "smalltalk":
        state.dialog_phase = "greeting" if kind == "greeting" else "wrap_up" if kind == "farewell" else "working"
    else:
        state.dialog_phase = "working"

    _write_state(session_state, state)
    return state


def apply_steering(
    session_state: Dict[str, Any],
    *,
    dispatch: Dict[str, Any],
    user_text: str,
    draft_message: str,
    content_mode: str = "text_answer",
) -> str:
    """Apply smalltalk limit, CTA, update state; return final draft text."""
    state = _read_state(session_state)
    route = str(dispatch.get("route") or "")
    intent = str(dispatch.get("intent") or "")

    redirect = handle_smalltalk_limit(state) if intent == "smalltalk" else None
    if redirect:
        update_dialog_state(
            session_state,
            dispatch=dispatch,
            content_message=draft_message,
            user_text=user_text,
            redirect_applied=True,
        )
        return redirect

    cta = build_cta(
        state,
        route=route,
        intent=intent,
        content_message=draft_message,
        content_mode=content_mode,
    )
    result = draft_message
    if cta:
        result = f"{draft_message.rstrip()}\n\n{cta}"

    update_dialog_state(
        session_state,
        dispatch=dispatch,
        content_message=draft_message,
        user_text=user_text,
        cta_used=cta,
    )
    return result
