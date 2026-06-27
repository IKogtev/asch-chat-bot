"""Unit tests for dialogue run metrics parsing."""
from tester.dialogues.metrics import analyze_turn_events, parse_dispatcher


def test_parse_dispatcher_route_intent() -> None:
    events = [
        {
            "author": "dispatcher_agent",
            "content": {
                "parts": [
                    {
                        "text": '{"route":"kb_answer","intent":"smalltalk","search_query":""}',
                    }
                ]
            },
            "usageMetadata": {
                "promptTokenCount": 100,
                "candidatesTokenCount": 20,
                "totalTokenCount": 120,
            },
            "timestamp": 1000.0,
            "modelVersion": "test-model",
        }
    ]
    route, intent, query = parse_dispatcher(events)
    assert route == "kb_answer"
    assert intent == "smalltalk"
    assert query is None


def test_analyze_turn_events_aggregates_tokens_and_agents() -> None:
    events = [
        {
            "author": "owasp_agent",
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5, "totalTokenCount": 15},
            "timestamp": 1.0,
            "modelVersion": "m1",
        },
        {
            "author": "dispatcher_agent",
            "content": {"parts": [{"text": '{"route":"doc_search","intent":"doc_search"}'}]},
            "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 10, "totalTokenCount": 30},
            "timestamp": 2.5,
            "modelVersion": "m1",
        },
        {"author": "root_agent", "content": {"parts": [{"text": "ok"}]}, "timestamp": 3.0},
    ]
    metrics = analyze_turn_events(events, wall_ms=2500.0)
    assert metrics.route == "doc_search"
    assert metrics.intent == "doc_search"
    assert metrics.tokens.total == 45
    assert metrics.wall_ms == 2500.0
    assert len(metrics.agents) == 2
    assert metrics.agents[1].latency_ms == 1500.0
