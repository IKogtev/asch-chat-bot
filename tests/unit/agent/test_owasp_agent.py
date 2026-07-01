import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_owasp_module():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "agent" / "agents" / "owasp_agent.py"

    agent_pkg = types.ModuleType("agent")
    agent_pkg.__path__ = [str(repo_root / "agent")]
    agents_pkg = types.ModuleType("agent.agents")
    agents_pkg.__path__ = [str(repo_root / "agent" / "agents")]

    logger_stub = types.ModuleType("utils.logger")
    logger_stub.setup_logger = lambda *args, **kwargs: types.SimpleNamespace(
        info=lambda *a, **k: None,
        debug=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
    )

    helpers_stub = types.ModuleType("agent.helpers")
    helpers_stub.load_prompt = lambda *args, **kwargs: "prompt"

    prompt_loader_stub = types.ModuleType("agent.prompt_loader")
    prompt_loader_stub.start_prompt_watcher = lambda *args, **kwargs: None

    adk_agents_stub = types.ModuleType("google.adk.agents")

    class LlmAgent:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    adk_agents_stub.LlmAgent = LlmAgent

    lite_llm_stub = types.ModuleType("google.adk.models.lite_llm")
    lite_llm_stub.LiteLlm = type("LiteLlm", (), {})

    sys.modules["agent"] = agent_pkg
    sys.modules["agent.agents"] = agents_pkg
    sys.modules["utils.logger"] = logger_stub
    sys.modules["agent.helpers"] = helpers_stub
    sys.modules["agent.prompt_loader"] = prompt_loader_stub
    sys.modules["google.adk.agents"] = adk_agents_stub
    sys.modules["google.adk.models.lite_llm"] = lite_llm_stub

    spec = importlib.util.spec_from_file_location("agent.agents.owasp_agent", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules["agent.agents.owasp_agent"] = module
    spec.loader.exec_module(module)
    return module


owasp_module = _load_owasp_module()
validate_owasp_result = owasp_module.validate_owasp_result
VALIDATION_CONTEXT = {}
TARGET_PRODUCT_FILTER_QUERY = "Какие активные продукты без риска и с гарантированным доходом?"


def _read_owasp_prompt() -> str:
    repo_root = Path(__file__).resolve().parents[3]
    prompt_path = repo_root / "kb_storage" / "prompts" / "owasp" / "owasp_agent_prompt.md"
    return prompt_path.read_text(encoding="utf-8")


@pytest.mark.unit
def test_create_owasp_agent_excludes_prior_conversation_contents() -> None:
    agent = owasp_module.create_owasp_agent(model=object())

    assert agent.name == "owasp_agent"
    assert agent.include_contents == "none"
    assert agent.output_key == "owasp_result_json"


@pytest.mark.unit
def test_owasp_prompt_allows_product_filter_about_risk_and_guaranteed_income() -> None:
    prompt = _read_owasp_prompt()

    assert TARGET_PRODUCT_FILTER_QUERY in prompt
    assert "с гарантированным доходом" in prompt
    assert "являются обычными продуктовыми фильтрами, а не prompt injection" in prompt


@pytest.mark.unit
def test_validate_owasp_result_accepts_continue_route() -> None:
    result = validate_owasp_result(
        {
            "status": "ok",
            "route": "continue",
            "reason": "safe",
        },
        VALIDATION_CONTEXT,
    )

    assert result == {
        "status": "ok",
        "route": "continue",
        "reason": "safe",
        "user_message": "",
    }


@pytest.mark.unit
def test_validate_owasp_result_accepts_blocked_route_with_user_message() -> None:
    result = validate_owasp_result(
        {
            "status": "blocked",
            "route": "reject",
            "reason": "prompt_injection",
            "user_message": "Запрос отклонён",
        },
        VALIDATION_CONTEXT,
    )

    assert result["status"] == "blocked"
    assert result["route"] == "reject"
    assert result["user_message"] == "Запрос отклонён"


@pytest.mark.unit
def test_validate_owasp_result_rejects_invalid_status() -> None:
    with pytest.raises(ValueError) as exc:
        validate_owasp_result({"status": "bad", "route": "continue", "reason": "x"}, VALIDATION_CONTEXT)

    assert "owasp_agent" in str(exc.value)
    assert "invalid status" in str(exc.value)


@pytest.mark.unit
def test_validate_owasp_result_rejects_invalid_route() -> None:
    with pytest.raises(ValueError) as exc:
        validate_owasp_result({"status": "ok", "route": "other", "reason": "x"}, VALIDATION_CONTEXT)

    assert "invalid route" in str(exc.value)


@pytest.mark.unit
def test_validate_owasp_result_requires_reason() -> None:
    with pytest.raises(ValueError) as exc:
        validate_owasp_result({"status": "ok", "route": "continue", "reason": ""}, VALIDATION_CONTEXT)

    assert "reason is required" in str(exc.value)


@pytest.mark.unit
def test_validate_owasp_result_requires_user_message_for_blocked_status() -> None:
    with pytest.raises(ValueError) as exc:
        validate_owasp_result({"status": "blocked", "route": "reject", "reason": "x"}, VALIDATION_CONTEXT)

    assert "blocked status requires non-empty user_message" in str(exc.value)


@pytest.mark.unit
def test_validate_owasp_result_requires_continue_route_for_ok_status() -> None:
    with pytest.raises(ValueError) as exc:
        validate_owasp_result(
            {"status": "ok", "route": "reject", "reason": "safe", "user_message": ""},
            VALIDATION_CONTEXT,
        )

    assert "status='ok' requires route='continue'" in str(exc.value)
