# Тестовые диалоги (realistic UX + tuning framework)

Набор многоходовых сценариев и **run bundle** после каждого прогона — для тюнинга dispatcher / dialogue manager.

## Структура

```
tester/dialogues/
  README.md
  run_dialogues.py       # прогон + сохранение bundle
  compare_runs.py        # diff manifest между прогонами
  metrics.py             # tokens, route/intent, per-agent latency
  run_bundle.py          # manifest + summary + routing JSONL
  scenarios/*.json       # диалоги
  runs/<run_id>/         # артефакты прогона
    manifest.json
    summary.md
    results.json
    routing/<scenario>.jsonl
    events/              # raw ADK (gitignored)
```

## Запуск

```bash
source .env
python tester/dialogues/run_dialogues.py --adk-base http://127.0.0.1:8080
python tester/dialogues/run_dialogues.py --adk-base http://127.0.0.1:8080 --ids real-01,real-02
python tester/dialogues/run_dialogues.py --adk-base http://127.0.0.1:8080 --no-events
python tester/dialogues/compare_runs.py latest
```

## Что сохраняется в bundle

| Файл | Содержимое |
|------|------------|
| `manifest.json` | run_id, git, **LLM model/URL**, feature flags, MCP, totals, **tokens by agent** |
| `summary.md` | таблицы latency/tokens, route/intent per turn, tuning notes |
| `results.json` | полный JSON по сценариям |
| `routing/*.jsonl` | одна строка = turn: user, route, intent, tokens, agents |
| `events/*.json` | сырые ADK events (для отладки) |

Метрики per turn:
- `wall_ms` — HTTP /run
- `route` / `intent` / `search_query` — из `dispatcher_agent`
- tokens — сумма по owasp → dispatcher → kb_answer / …
- per-agent latency — delta timestamps в events

## Сценарии

| ID | Файл | Суть |
|----|------|------|
| real-01 | 01_morning_onboarding.json | Утро, знакомство |
| real-02 | 02_client_gss_explain.json | ГСС для клиента |
| real-03 | 03_meeting_presenter.json | Презентер к встрече |
| real-04 | 04_vague_then_fort_knox.json | vague → FN |
| real-05 | 05_offtopic_recovery.json | off-topic → работа |
| real-06 | 06_product_filter_session.json | product_filter |
| real-07 | 07_thanks_and_followup.json | спасибо + новый вопрос |
| real-08 | 08_busy_manager.json | короткие реплики |
| real-09 | 09_no_data_clarify.json | no_data → уточнение |
| real-10 | 10_end_of_day_wrap.json | конец дня |
| real-11 | 11_long_work_session.json | длинная сессия (~12 turns) |

## Tuning workflow

1. Прогон → `runs/<id>/summary.md`
2. Смотреть `routing/` — drift route/intent
3. Меняешь prompt dispatcher / dialogue manager
4. `compare_runs.py latest` — tokens/latency delta
5. Коммитишь `manifest.json` + `routing/` как baseline

## plan 001 smoke

Короткие кейсы M1–M9: `tester/plan001_dialogue_runner.py`
