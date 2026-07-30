import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_config_module(monkeypatch):
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "agent" / "config.py"

    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *args, **kwargs: None

    logger_stub = types.ModuleType("utils.logger")
    logger_stub.setup_logger = lambda *args, **kwargs: types.SimpleNamespace(
        info=lambda *a, **k: None,
        debug=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
    )

    lite_llm_stub = types.ModuleType("google.adk.models.lite_llm")
    lite_llm_stub.LiteLlm = type(
        "LiteLlm",
        (),
        {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)},
    )

    monkeypatch.setitem(sys.modules, "dotenv", dotenv_stub)
    monkeypatch.setitem(sys.modules, "utils.logger", logger_stub)
    monkeypatch.setitem(sys.modules, "google.adk.models.lite_llm", lite_llm_stub)

    spec = importlib.util.spec_from_file_location("agent.config_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
def test_dialog_memory_max_turns_default(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_DIALOG_MEMORY_MAX_TURNS", raising=False)

    config = _load_config_module(monkeypatch)

    assert config.AGENT_DIALOG_MEMORY_MAX_TURNS == 3


@pytest.mark.unit
def test_dialog_memory_max_turns_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DIALOG_MEMORY_MAX_TURNS", "5")

    config = _load_config_module(monkeypatch)

    assert config.AGENT_DIALOG_MEMORY_MAX_TURNS == 5


@pytest.mark.unit
def test_owasp_generation_settings_read_environment(monkeypatch) -> None:
    monkeypatch.setenv("ROOT_TEMPERATURE", "0.8")
    monkeypatch.setenv("OWASP_TEMPERATURE", "0.2")
    monkeypatch.setenv("OWASP_TOP_P", "0.8")
    monkeypatch.setenv("OWASP_MAX_OUTPUT_TOKENS", "128")
    monkeypatch.setenv("OWASP_ENABLE_THINKING", "false")

    config = _load_config_module(monkeypatch)

    assert config.OWASP_TEMPERATURE == 0.2
    assert config.OWASP_TOP_P == 0.8
    assert config.OWASP_MAX_OUTPUT_TOKENS == 128
    assert config.OWASP_ENABLE_THINKING is False


@pytest.mark.unit
def test_owasp_model_disables_thinking(monkeypatch) -> None:
    monkeypatch.setenv("OWASP_ENABLE_THINKING", "false")
    config = _load_config_module(monkeypatch)

    common_model = config.build_common_model()
    format_model = config.build_format_model()
    owasp_model = config.build_owasp_model()

    assert common_model.extra_body["chat_template_kwargs"]["enable_thinking"] is True
    assert common_model.extra_body["thinking_token_budget"] == 2048
    assert format_model.extra_body == {
        "chat_template_kwargs": {"enable_thinking": False},
    }
    assert owasp_model.extra_body == {
        "chat_template_kwargs": {"enable_thinking": False},
    }
