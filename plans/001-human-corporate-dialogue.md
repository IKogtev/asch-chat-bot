# План 001: Деловой диалог, ack-паттерн, steering

**Статус:** draft  
**Дата:** 2026-06-21  
**Контекст:** внутренний помощник АСЖ; деловой тон; краткий smalltalk по делу; не слепо следовать пользователю

---

## 1. Цели

| # | Цель | Критерий успеха |
|---|------|-----------------|
| G1 | Меньше «железного» тона | Ответы читаются как коллега, не FAQ-бот |
| G2 | UX «живой» при долгой цепочке | Ack ≤ 1.5 с; typing + короткий текст до финала |
| G3 | Ведение разговора | После релевантных ответов — уместный next step; vague → уточнение |
| G4 | Без потери точности | Факты только из tools; voice/DM не добавляют данные |
| G5 | Контроль off-topic | Smalltalk ≤ 2 turn подряд, потом мягкий redirect |

**Не делаем в этом плане:**

- смена LLM-модели
- streaming partial tokens в один bubble
- глубокий CRM / персонализация вне профиля бота

---

## 2. Архитектура (целевая)

```
User → bot/handlers
         ├─ [parallel] quick_ack (rule / micro-LLM)
         └─ adk.run → root_agent
              owasp → glossary → dispatcher → leaf (content)
              → dialogue_manager (steering, CTA, limits)
              → voice_agent (corporate tone, facts-safe)
              → final Event
         → bot: второе сообщение (или одно для smalltalk)
```

**Принцип:** content strict, presentation flexible.

---

## 3. Риски и меры

### R1. Пользователь отвечает на ack до прихода финала

**Симптом:** два turn пересекаются; финал «не в тему».

**Меры:**

- `turn_id` (uuid) на каждый user message в `handlers.py`
- В `session.state`: `active_turn_id`, `ack_sent_for_turn`
- Перед отправкой финала: проверка `turn_id == active_turn_id`; иначе — drop + log
- Ack **не задаёт вопросов** (только «Проверю…», «Подберу…»)
- `/reset` и cancel — как сейчас, плюс invalidate turn

**Acceptance:** при быстром втором сообщении старый финал не уходит пользователю.

---

### R2. Ack обещает результат, приходит `no_data`

**Симптом:** «Сейчас найду ответ» → «не найдено» — недоверие.

**Меры:**

- Шаблоны ack только про **процесс**, не результат:
  - ✅ «Проверю информацию в базе знаний.»
  - ❌ «Сейчас найду ответ на ваш вопрос.»
- Для `smalltalk`, `file_download`, `show_more`, `show_all` — **без ack**
- Voice смягчает `no_data` без противоречия ack

---

### R3. Voice Agent галлюцинирует поверх draft

**Симптом:** цифры/коды/условия не из KB/SQL.

**Меры:**

- Voice получает `draft_message` + `fact_anchors[]` (regex: коды `\d{3,}`, %, суммы, «до N лет»)
- Post-validator в коде: новые anchors в voice → fallback на draft
- Промпт: «запрещено добавлять факты, коды, числа, условия»
- Этап 0 без voice — только промпт kb_answer; voice — этап 4

**Acceptance:** unit-тесты validator на 10 кейсах с injected numbers.

---

### R4. CTA spam после каждого ответа

**Симптом:** раздражение «Могу показать карточку…» везде.

**Меры:**

- Whitelist CTA в `conversation_policy.md`:
  - kb_answer + упоминание продукта → карточка/комплект
  - product_filter → уже есть follow-up
  - doc_search success → номер/«ещё»
  - иначе — **без CTA**
- Не более **одного** CTA в сообщении
- Не повторять тот же CTA два turn подряд (`last_cta` в `dialog_state`)

---

### R5. +latency от voice / dialogue LLM

**Симптом:** ack помог, но финал ещё +2–3 с.

**Меры:**

- Ack закрывает «тишину» первых секунд
- Voice/DM — **fast model** env (`LLM_VOICE_MODEL`, optional)
- Dialogue Manager на этапе 3 — **гибрид**: CTA/smalltalk limit в коде; LLM только для clarification
- Метрики: `ack_ms`, `total_ms`, `voice_ms` в event log

---

### R6. `dialog_state` конфликтует с `_clear_state_keys`

**Симптом:** phase/smalltalk сбрасываются каждый turn.

**Меры:**

- Ключи `dialog_*` **не** в списке clear в `rootagent._run_async_impl`
- Явный whitelist persistent keys:
  - `dialog_phase`, `dialog_topic`, `smalltalk_turns`, `last_route`, `last_cta`, `pending_clarification`
- `/reset` — полная очистка session (bot + ADK delete_session)

---

### R7. Два сообщения плодят историю

**Симптом:** в store два «model» на один user turn.

**Меры:**

- В БД бота: ack **не** пишем в `store.append` (или tag `meta:ack`)
- В ADK history: только финал через `_build_final_event_with_history`
- Event log: отдельный `event_type=ack` vs `response`

---

### R8. Pre-dispatcher ack неверного типа

**Симптом:** «Проверю в базе» → пришли документы.

**Меры:**

- **Этап 1b** (не 1a): ack после lightweight classify
- Вариант 1 (рекоменд): ack-шаблон из **parsed dispatcher** внутри ADK, первый Event с `interim=true`
- Вариант 2: bot-side heuristic до ADK + generic ack «Обрабатываю запрос.»

**Решение по умолчанию:** этап 1 — generic ack; этап 2 — route-aware ack из ADK.

---

## 4. Этапы реализации

### Этап 0 — Промпт kb_answer (tone baseline)

**Scope:** только `kb_storage/prompts/kb_answer/kb_answer_agent_prompt.md`

**Изменения:**

1. Секция «Голос»: деловой, «Вы», без markdown, 2–4 предложения
2. Smalltalk: формальное приветствие (из `old260409_v3`, упростить правила имени)
3. «Что умеешь»: 2 предложения + вопрос «Чем могу помочь?» (обновить validator в `kb_answer_agent.py` при необходимости)
4. `no_data`: шаблон с уточнением + опциональный CTA
5. Явно: **не** использовать историю как факты (оставить)

**Файлы:**

- `kb_storage/prompts/kb_answer/kb_answer_agent_prompt.md`
- `agent/agents/kb_answer_agent.py` — `ASSISTANT_CAPABILITIES_ANSWER`, validator
- `tests/unit/agent/test_kb_answer_agent.py`

**DoD:**

- [ ] Промпт без противоречий с FAQ-first
- [ ] Тесты validator green
- [ ] Ручной чеклист 10 фраз (привет, kb, no_data, capabilities)

**Rollback:** backup prompt (уже есть `*_backup*`)

---

### Этап 1 — Quick ack в bot

**Scope:** `bot/services/handlers.py`, config, events

**1.1 Конфиг**

```python
ACK_ENABLED = true
ACK_GENERIC_TEXT = "Обрабатываю запрос."
```

**1.2 Turn tracking**

- `turn_id = uuid4()` в начале `on_text`
- Передавать в `eventlogger` payload

**1.3 Поток**

```python
turn_id = ...
task = asyncio.create_task(adk.run(...))
if should_send_ack(user_text):
    await bot_res.send(ACK_GENERIC_TEXT)
    log event_type=ack
answer, events = await task
if turn_still_active(turn_id):
    await bot_res.send(final)
```

**1.4 Heuristic `should_send_ack`**

- False: пусто, `/start`, `/reset`, regex приветствий (`^(привет|здравств|добр)`)
- False: `parse_download_ranks`, show_more/all handlers (уходят раньше)
- True: всё остальное → generic

**Файлы:**

- `bot/services/handlers.py`
- `bot/services/config.py` (Settings)
- `tests/unit/bot/test_ack_flow.py` (новый)

**DoD:**

- [ ] Ack не в dialog store
- [ ] Cancel/reset не шлёт финал
- [ ] turn_id guard на финале

---

### Этап 2 — Route-aware ack + interim Event из ADK

**Scope:** `root_agent`, `AdkApiClient`, handlers

**2.1 ADK interim event**

После dispatcher (до leaf): yield Event с:

- `author=root_agent`
- `actions.interim=true` (новое поле, не `end_of_agent`)
- `content.parts[].text` = ack по route/intent

**Шаблоны:**

| route / intent | ack |
|----------------|-----|
| doc_search | «Подберу документы по запросу.» |
| kb_answer | «Проверю информацию в базе знаний.» |
| product_selection | «Уточню параметры продукта.» |
| smalltalk | *(no interim)* |

**2.2 Bot parse events**

- `AdkApiClient.run` parse events list:
  - interim → send ack immediately
  - final → send answer

**2.3 Убрать generic ack из bot** когда ADK ack включён

**Файлы:**

- `agent/rootagent.py`
- `agent/helpers.py` — `format_ack_message(route, intent)`
- `bot/services/database.py` — `_extract_interim_and_final(events)`
- `bot/services/handlers.py`
- `tests/unit/agent/test_rootagent_ack.py`

**DoD:**

- [ ] Ack соответствует route
- [ ] smalltalk — одно сообщение
- [ ] Нет двойного ack (bot + ADK)

---

### Этап 3 — Dialogue Manager (steering)

**Scope:** новый модуль + root_agent integration

**3.1 Persistent state**

```python
DIALOG_STATE_KEYS = [
  "dialog_phase",      # greeting | working | wrap_up
  "dialog_topic",
  "smalltalk_turns",
  "last_route",
  "last_cta",
  "pending_clarification",
]
```

**3.2 `agent/dialogue/manager.py`**

- `update_dialog_state(ctx, dispatch, content_result) -> DialogState`
- `build_cta(state, content) -> str | None` — rule-based
- `should_clarify(dispatch, user_text) -> bool`
- `handle_smalltalk_limit(state) -> redirect | None`

**3.3 `kb_storage/prompts/dialogue/conversation_policy.md`**

- Цели, whitelist CTA, лимит smalltalk, примеры

**3.4 Clarification path**

- Новый intent `needs_clarification` **или** mode в kb_answer: `clarification`
- Dispatcher prompt: vague односложные без сущности → clarification
- Root: если clarification — kb_answer без search tools

**3.5 Integration в root**

После leaf, до `_root_final_text`:

```python
draft = format_text_answer(content["message"])
cta = dialogue_manager.build_cta(...)
if cta and cta != state.last_cta:
    draft = f"{draft}\n\n{cta}"
state = dialogue_manager.update(...)
ctx.session.state.update(persist dialog keys)
```

**Файлы:**

- `agent/dialogue/manager.py`
- `agent/dialogue/__init__.py`
- `kb_storage/prompts/dialogue/conversation_policy.md`
- `agent/rootagent.py` — не clear dialog keys
- `kb_storage/prompts/dispatcher/dispatcher_agent_prompt.md` — clarification rules
- `tests/unit/agent/test_dialogue_manager.py`

**DoD:**

- [ ] smalltalk_turns increment/reset
- [ ] 3-й off-topic → redirect
- [ ] CTA не дублируется 2 turn подряд
- [ ] vague «расскажи про страхование» → уточнение

---

### Этап 4 — Voice Agent

**Scope:** новый leaf + fact validator

**4.1 `agent/agents/voice_agent.py`**

- Input state: `voice_draft`, `voice_profile=corporate_internal`, `first_name`, `intent`
- Output JSON: `{ "status": "ok", "message": "..." }`
- Без tools

**4.2 `kb_storage/prompts/voice/voice_agent_prompt.md`**

- Деловой тон, «Вы», без новых фактов
- Smalltalk коротко

**4.3 `agent/dialogue/fact_guard.py`**

- `extract_anchors(text) -> set`
- `validate_voice(draft, voiced) -> voiced | draft`

**4.4 root_agent**

```python
ctx.session.state["voice_draft"] = draft_with_cta
async for event in voice_agent: ...
final = fact_guard.validate(draft, voiced)
```

**4.5 Config**

- `VOICE_AGENT_ENABLED=true`
- `LLM_VOICE_MODEL` optional (default = common model)

**Файлы:**

- `agent/agents/voice_agent.py`
- `kb_storage/prompts/voice/voice_agent_prompt.md`
- `agent/dialogue/fact_guard.py`
- `agent/start_agent.py`
- `agent/rootagent.py`
- `tests/unit/agent/test_voice_agent.py`
- `tests/unit/agent/test_fact_guard.py`

**DoD:**

- [ ] Inject number test → fallback draft
- [ ] Факты сохраняются при перефразировании
- [ ] `VOICE_AGENT_ENABLED=false` → draft as-is

---

### Этап 5 — Observability & rollout

**5.1 Metrics (event log)**

- `ack_sent`, `ack_ms`, `route`, `intent`, `cta_used`, `voice_fallback`, `turn_cancelled`

**5.2 Feature flags (env)**

```
ACK_ENABLED=
ADK_ROUTE_ACK_ENABLED=
DIALOGUE_MANAGER_ENABLED=
VOICE_AGENT_ENABLED=
```

**5.3 Rollout**

1. dev → test1: этап 0–1
2. test1: этап 2–3
3. prod: этап 4 после 1 недели метрик

**5.4 Документация**

- Обновить `docs/agents-chain.md`
- Release notes

---

## 5. Матрица файлов (сводка)

| Файл | Э0 | Э1 | Э2 | Э3 | Э4 |
|------|:--:|:--:|:--:|:--:|:--:|
| `kb_answer_agent_prompt.md` | ✓ | | | | |
| `kb_answer_agent.py` | ✓ | | | | |
| `handlers.py` | | ✓ | ✓ | | |
| `database.py` (AdkApiClient) | | | ✓ | | |
| `rootagent.py` | | | ✓ | ✓ | ✓ |
| `dispatcher_agent_prompt.md` | | | | ✓ | |
| `dialogue/manager.py` | | | | ✓ | |
| `conversation_policy.md` | | | | ✓ | |
| `voice_agent.py` | | | | | ✓ |
| `voice_agent_prompt.md` | | | | | ✓ |
| `fact_guard.py` | | | | | ✓ |
| `start_agent.py` | | | | | ✓ |
| `docs/agents-chain.md` | | | | | ✓ |

---

## 6. Тест-план

### Unit

- kb_answer validator (capabilities, no_data)
- ack heuristics / route templates
- turn_id stale final drop
- dialogue: smalltalk limit, CTA dedup, clarify trigger
- fact_guard anchors

### Integration

- `test_rootagent`: full chain mock MCP → interim + final events
- handlers: mock adk.run delayed → ack then final order

### Manual (test1)

| # | Input | Expected |
|---|-------|----------|
| 1 | «Привет» | 1 msg, деловое приветствие |
| 2 | «Что такое ГСС» | ack → kb answer |
| 3 | «дай презентер FN» | ack doc → список |
| 4 | быстро 2 msg подряд | только актуальный финал |
| 5 | 3× smalltalk off-topic | redirect |
| 6 | «расскажи про продукт» | уточнение |
| 7 | product_filter | список + CTA |
| 8 | no_data topic | мягкий отказ + уточнение |

---

## 7. Оценка effort

| Этап | Dev | QA |
|------|-----|-----|
| 0 | 0.5–1 d | 0.5 d |
| 1 | 1–2 d | 1 d |
| 2 | 2–3 d | 1 d |
| 3 | 3–4 d | 2 d |
| 4 | 2–3 d | 1 d |
| 5 | 1 d | — |
| **Итого** | **~10–14 d** | **~5.5 d** |

---

## 8. Открытые решения (зафиксировать до Э2)

| # | Вопрос | Рекомендация |
|---|--------|--------------|
| D1 | Ack в bot или ADK | Э1 bot generic → Э2 ADK route-aware |
| D2 | Clarification: новый intent vs mode | `intent=needs_clarification` → kb_answer без tools |
| D3 | Voice на все routes или только kb_answer | kb + product_selection; doc_search skip (UI list) |
| D4 | «Вы» vs «ты» | «Вы» везде |

---

## 9. Checklist перед merge в prod

- [ ] Feature flags default safe (voice off)
- [ ] Нет ack в dialog history store
- [ ] turn_id guard tested
- [ ] fact_guard tested
- [ ] `docs/agents-chain.md` updated
- [ ] Prompt backups на месте
- [ ] Event metrics в логах видны
