import importlib.util
import sys
from pathlib import Path

import pytest


def _load_module(name: str, rel_path: str):
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / rel_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


manager = _load_module("agent.dialogue.manager", "agent/dialogue/manager.py")
fact_guard = _load_module("agent.dialogue.fact_guard", "agent/dialogue/fact_guard.py")


@pytest.mark.unit
def test_should_clarify_vague_product_query() -> None:
    dispatch = {"route": "kb_answer", "intent": "kb_answer"}
    assert manager.should_clarify(dispatch, "расскажи про продукт") is True
    assert manager.should_clarify(dispatch, "что такое ГСС") is False
    assert manager.should_clarify(dispatch, "покажи 8914") is False


@pytest.mark.unit
def test_should_not_clarify_social_phrases() -> None:
    dispatch = {"route": "kb_answer", "intent": "kb_answer"}
    assert manager.should_clarify(dispatch, "что нового?") is False
    assert manager.should_clarify(dispatch, "на связи завтра") is False
    assert manager.should_clarify(dispatch, "позже вернусь с вопросом") is False


@pytest.mark.unit
def test_adjust_dispatch_social_to_smalltalk() -> None:
    dispatch = {
        "route": "kb_answer",
        "intent": "needs_clarification",
        "search_query": "",
    }
    out = manager.adjust_dispatch(dispatch, "что нового?", {})
    assert out["intent"] == "smalltalk"


@pytest.mark.unit
def test_render_smalltalk_no_repeat_intro() -> None:
    state = {"session_intro_done": True}
    reply = manager.render_smalltalk_reply(state, "привет", first_name="Дмитрий")
    assert reply in manager._REPEAT_GREETING
    assert "Дмитрий" not in reply
    assert "Настя" not in reply


@pytest.mark.unit
def test_render_thanks_without_name() -> None:
    reply = manager.render_smalltalk_reply({}, "спасибо", first_name="Дмитрий")
    assert "Дмитрий" not in reply
    assert reply in manager._THANKS


@pytest.mark.unit
def test_render_smalltalk_first_greeting_has_soul() -> None:
    reply = manager.render_smalltalk_reply({}, "Доброе утро", first_name="Дмитрий")
    assert "Настя" in reply
    assert "АСЖ" in reply


@pytest.mark.unit
def test_capabilities_variants_differ() -> None:
    a = manager.render_smalltalk_reply({}, "что ты умеешь", first_name="Дмитрий")
    b = manager.render_smalltalk_reply({"smalltalk_turns": 1}, "чем можешь помочь", first_name="Дмитрий")
    assert a in manager._CAPABILITIES
    assert b in manager._CAPABILITIES


@pytest.mark.unit
def test_render_smalltalk_defer() -> None:
    reply = manager.render_smalltalk_reply({}, "позже вернусь", first_name="Дмитрий")
    assert "на связи" in reply.lower() or "продолжить" in reply.lower()


@pytest.mark.unit
def test_is_social_farewell() -> None:
    assert manager.is_social_smalltalk("Хорошего вечера") is True
    assert manager.is_social_smalltalk("на связи завтра") is True


@pytest.mark.unit
def test_render_defer_with_fort_knox() -> None:
    reply = manager.render_smalltalk_reply(
        {},
        "Завтра продолжим — клиент по Fort Knox",
        first_name="Дмитрий",
    )
    assert "Fort Knox" in reply


@pytest.mark.unit
def test_clarification_followup_fort_knox() -> None:
    session = {"pending_clarification": True}
    out = manager.resolve_clarification_followup(session, "Fort Knox")
    assert out is not None
    assert out["intent"] == "kb_answer"
    assert "Fort Knox" in out["search_query"]


@pytest.mark.unit
def test_smalltalk_limit_redirect() -> None:
    state = manager.DialogState(smalltalk_turns=2)
    assert manager.handle_smalltalk_limit(state) is not None
    state.smalltalk_turns = 1
    assert manager.handle_smalltalk_limit(state) is None


@pytest.mark.unit
def test_cta_not_repeated_from_last_turn() -> None:
    session_state = {"last_cta": manager.build_cta(
        manager.DialogState(),
        route="kb_answer",
        intent="kb_answer",
        content_message="продукт Fort Knox 8914",
    ) or ""}
    state = manager._read_state(session_state)
    cta = manager.build_cta(
        state,
        route="kb_answer",
        intent="kb_answer",
        content_message="продукт Fort Knox 8914",
    )
    assert cta is None


@pytest.mark.unit
def test_fact_guard_rejects_injected_number() -> None:
    draft = "Продукт доступен для оформления."
    voiced = "Продукт 8914 доступен для оформления."
    assert fact_guard.validate_voice(draft, voiced) == draft


@pytest.mark.unit
def test_fact_guard_accepts_safe_rephrase() -> None:
    draft = "Продукт 8914 доступен для оформления."
    voiced = "Оформить продукт 8914 можно в стандартном порядке."
    assert fact_guard.validate_voice(draft, voiced) == voiced


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected_subset"),
    [
        ("код 8914 и 2832", {"8914", "2832"}),
        ("ставка 5%", {"5%"}),
        ("до 70 лет", {"до 70 лет"}),
    ],
)
def test_extract_anchors(text, expected_subset) -> None:
    anchors = fact_guard.extract_anchors(text)
    assert expected_subset.issubset(anchors)
