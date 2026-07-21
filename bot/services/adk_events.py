TIMING_STATE_DELTA_KEY = "_timing"


def _iter_state_deltas(event: dict):
    candidates = []
    actions = event.get("actions")
    if isinstance(actions, dict):
        candidates.extend(
            [
                actions.get("stateDelta"),
                actions.get("state_delta"),
                actions.get("state_delta_json"),
            ]
        )

    candidates.extend(
        [
            event.get("stateDelta"),
            event.get("state_delta"),
        ]
    )

    for delta in candidates:
        if isinstance(delta, dict):
            yield delta


def extract_bot_action(events: list) -> dict | None:
    if not events:
        return None

    for event in reversed(events):
        if not isinstance(event, dict):
            continue

        for delta in _iter_state_deltas(event):
            action = delta.get("_bot_action")
            if isinstance(action, dict) and action.get("type"):
                return action

    return None


def extract_timing(events: list) -> dict | None:
    """Плоские тайминги стадий из stateDelta финального root-события."""
    if not events:
        return None

    for event in reversed(events):
        if not isinstance(event, dict):
            continue

        for delta in _iter_state_deltas(event):
            timing = delta.get(TIMING_STATE_DELTA_KEY)
            if isinstance(timing, dict) and timing:
                return dict(timing)

    return None
