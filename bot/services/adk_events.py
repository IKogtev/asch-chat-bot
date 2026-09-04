TIMING_STATE_DELTA_KEY = "_timing"
PRODUCT_DIALOG_CONTEXT_KEY = "_product_dialog_context"


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


def extract_selected_product(events: list) -> dict[str, str] | None:
    """Выбранный продукт из финального состояния root-агента.

    Плоский last_product не используется: он переживает сам выбор
    (kb_answer снимает только _product_dialog_context) и подсовывает
    продукт из более раннего хода.
    """
    if not events:
        return None

    for event in reversed(events):
        if not isinstance(event, dict):
            continue

        for delta in _iter_state_deltas(event):
            context = delta.get(PRODUCT_DIALOG_CONTEXT_KEY)
            if isinstance(context, dict):
                return _normalize_product(context.get("selected_product"))

    return None


def _normalize_product(value) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    code = str(value.get("code") or value.get("product_code") or "").strip()
    name = str(value.get("name") or value.get("product_name") or "").strip()
    if not (code or name):
        return None
    return {"code": code, "name": name}


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
