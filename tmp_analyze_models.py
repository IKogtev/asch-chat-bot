import csv
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

path = Path(r"c:\Users\Ivan\Documents\Alpha_ins\Nastya_models_timing\data-1784031915397.csv")
rows = []
with path.open(encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for r in reader:
        for k, v in list(r.items()):
            if v in ("", "NULL", None):
                r[k] = None
            elif k not in (
                "created_at",
                "user_id",
                "session_id",
                "model",
                "turn_id",
                "route",
            ):
                try:
                    r[k] = int(float(v))
                except (TypeError, ValueError):
                    pass
        # В экспорте pgAdmin перепутаны имена: первая колонка после owasp = owasp out
        r["owasp_output_tokens"] = r.pop("dispatcher_output_tokens", None)
        r["dispatcher_output_tokens"] = r.pop("dispatcher_output_tokens-2", None)
        rows.append(r)


def group_stats(values):
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    p90 = s[int(0.9 * (n - 1))] if n > 1 else s[0]
    return {
        "n": n,
        "mean": round(mean(s), 1),
        "median": round(median(s), 1),
        "p90": p90,
    }


def leaf_output_col(route):
    return {
        "kb_answer": "k_a_output_tokens",
        "doc_search": "d_s_output_tokens",
        "product_selection": "p_s_output_tokens",
    }.get(route)


def total_output_tokens(r):
    parts = [
        r.get("owasp_output_tokens"),
        r.get("dispatcher_output_tokens"),
        r.get("k_a_output_tokens"),
        r.get("d_s_output_tokens"),
        r.get("p_s_output_tokens"),
    ]
    vals = [v for v in parts if v is not None]
    return sum(vals) if vals else None


models = sorted({r["model"] for r in rows})
routes = sorted({r["route"] for r in rows if r["route"]})

route_cols = {
    "kb_answer": [
        ("total_ms", "total"),
        ("owasp_ms", "owasp"),
        ("owasp_output_tokens", "owasp_out"),
        ("dispatcher_ms", "disp"),
        ("dispatcher_output_tokens", "disp_out"),
        ("k_a_ms", "leaf"),
        ("k_a_ttft_ms", "ttft"),
        ("k_a_input_tokens", "in_tok"),
        ("k_a_output_tokens", "out_tok"),
        ("k_a_tool_calls", "tools"),
        ("k_a_model_turns", "turns"),
    ],
    "doc_search": [
        ("total_ms", "total"),
        ("owasp_ms", "owasp"),
        ("owasp_output_tokens", "owasp_out"),
        ("dispatcher_ms", "disp"),
        ("dispatcher_output_tokens", "disp_out"),
        ("d_s_ms", "leaf"),
        ("d_s_ttft_ms", "ttft"),
        ("d_s_input_tokens", "in_tok"),
        ("d_s_output_tokens", "out_tok"),
        ("d_s_tool_calls", "tools"),
        ("d_s_model_turns", "turns"),
    ],
    "product_selection": [
        ("total_ms", "total"),
        ("owasp_ms", "owasp"),
        ("owasp_output_tokens", "owasp_out"),
        ("dispatcher_ms", "disp"),
        ("dispatcher_output_tokens", "disp_out"),
        ("p_s_ms", "leaf"),
        ("p_s_ttft_ms", "ttft"),
        ("p_s_input_tokens", "in_tok"),
        ("p_s_output_tokens", "out_tok"),
        ("p_s_tool_calls", "tools"),
        ("p_s_model_turns", "turns"),
    ],
}

print("OVERVIEW")
for m in models:
    mr = [r for r in rows if r["model"] == m]
    print(f"{m}: {len(mr)}")
    rc = defaultdict(int)
    for r in mr:
        rc[r["route"] or "NULL"] += 1
    for route, c in sorted(rc.items(), key=lambda x: -x[1]):
        print(f"  {route}: {c}")

print("\nOVERALL TOKENS")
for m in models:
    mr = [r for r in rows if r["model"] == m]
    total_out = [total_output_tokens(r) for r in mr if total_output_tokens(r) is not None]
    owasp_out = [r["owasp_output_tokens"] for r in mr if r.get("owasp_output_tokens") is not None]
    disp_out = [r["dispatcher_output_tokens"] for r in mr if r.get("dispatcher_output_tokens") is not None]
    print(m)
    print("  total_output_tokens", group_stats(total_out))
    print("  owasp_output_tokens", group_stats(owasp_out))
    print("  dispatcher_output_tokens", group_stats(disp_out))

print("\nOVERALL TIMING")
for col in ["total_ms", "owasp_ms", "dispatcher_ms"]:
    for m in models:
        vals = [r[col] for r in rows if r["model"] == m and r.get(col) is not None]
        print(m, col, group_stats(vals))

print("\nDELTA 3.6 vs 3.0 (median %)")
for route in routes:
    print(f"ROUTE {route}")
    for col, label in route_cols.get(route, []):
        v30 = [
            r[col]
            for r in rows
            if r["model"] == "qwen3.0" and r["route"] == route and r.get(col) is not None
        ]
        v36 = [
            r[col]
            for r in rows
            if r["model"] == "qwen3.6" and r["route"] == route and r.get(col) is not None
        ]
        if not v30 or not v36:
            print(f"  {label}: skip n30={len(v30)} n36={len(v36)}")
            continue
        m30, m36 = median(v30), median(v36)
        pct = round((m36 - m30) / m30 * 100, 1) if m30 else 0
        print(f"  {label}: {m30} -> {m36} ({pct:+.1f}%)")

print("\nOUTPUT TOKENS BY ROUTE (leaf)")
for route in routes:
    col = leaf_output_col(route)
    if not col:
        continue
    print(f"ROUTE {route} {col}")
    for m in models:
        vals = [r[col] for r in rows if r["model"] == m and r["route"] == route and r.get(col) is not None]
        print(f"  {m}", group_stats(vals))

print("\nMS PER OUTPUT TOKEN (leaf, lower=better)")
for route in routes:
    ratios30, ratios36 = [], []
    ms_col = {"kb_answer": "k_a_ms", "doc_search": "d_s_ms", "product_selection": "p_s_ms"}.get(route)
    out_col = leaf_output_col(route)
    if not ms_col or not out_col:
        continue
    for r in rows:
        if r.get("route") != route:
            continue
        ms, out = r.get(ms_col), r.get(out_col)
        if not ms or not out or out <= 0:
            continue
        ratio = ms / out
        if r["model"] == "qwen3.0":
            ratios30.append(ratio)
        else:
            ratios36.append(ratio)
    if ratios30 and ratios36:
        print(
            f"{route}: 3.0 med {median(ratios30):.1f} ms/tok, "
            f"3.6 med {median(ratios36):.1f} ms/tok "
            f"({round((median(ratios36)-median(ratios30))/median(ratios30)*100,1):+.1f}%)"
        )
