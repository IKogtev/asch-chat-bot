def extract_bot_action(events: list) -> dict | None:
    if not events:
        return None

    for event in reversed(events):
        if not isinstance(event, dict):
            continue

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
            if not isinstance(delta, dict):
                continue
            action = delta.get("_bot_action")
            if isinstance(action, dict) and action.get("type"):
                return action

    return None
