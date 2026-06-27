# План 002: Настя с душой — человечный диалог

**Статус:** in progress  
**Дата:** 2026-06-27  
**Бaseline:** run `20260627_115139` (51 turn, human score ~5/10)  
**Цель:** **8/10** в Telegram с MCP; **7/10** local без KB

---

## 1. Проблемы baseline

| # | Симптом | Причина |
|---|---------|---------|
| P1 | Повтор «Я Настя…» на каждом приветствии | kb_answer без памяти сессии |
| P2 | «Что нового?» / «На связи завтра» → clarify | dispatcher + should_clarify |
| P3 | «Fort Knox» одним словом → OWASP / ошибка | нет follow-up после clarify |
| P4 | «Не удалось корректно обработать» | MCP off + жёсткие validation errors |
| P5 | 14 s тишины | ack только в bot |
| P6 | Галлюцинации без tools | kb_answer без MCP |

---

## 2. Целевые метрики (dialogue runs)

| Метрика | Baseline | Target |
|---------|----------|--------|
| Повтор full intro / session | ~8/15 smalltalk | **0** |
| Social → needs_clarification | 6 turns | **0** |
| Generic validation error (smalltalk path) | N/A | **0** |
| Avg wall_ms (smalltalk) | ~12 s | **<4 s** (rule smalltalk) |
| Dialogue scenarios PASS (soft checks) | 11/11 | 11/11 + human rubric |

Rubrics (ручная / будущий scorer):
- **G1** тон коллеги с теплом, не FAQ
- **G3** social/defer/farewell без лишних вопросов
- **G5** off-topic redirect на 3-й turn

---

## 3. Архитектура

```
User → root_agent
         owasp → glossary → dispatcher
         → dialogue.adjust_dispatch()     # social override, clarify follow-up
         → dialogue.should_clarify()      # только не-social
         → [smalltalk] render_smalltalk_reply()  # rule-based, с душой
         → [kb/doc/product] leaf agents
         → dialogue.apply_steering()      # CTA, redirect
         → [optional] voice_agent
```

**Принцип:** social/repeat — **детерминированные** ответы (быстро, стабильно); факты — **только tools**.

---

## 4. Этапы

### Этап A — Social routing (код) ✅

- [x] `is_social_smalltalk()` — whitelist defer/farewell/chitchat
- [x] `adjust_dispatch()` — social → smalltalk, не clarify
- [x] `resolve_clarification_followup()` — «Fort Knox» после vague
- [x] `render_smalltalk_reply()` — intro once, warm thanks/farewell/defer
- [x] `session_intro_done` в dialog state
- [x] rootagent: social fast-path (skip OWASP) + rule smalltalk
- [x] Baseline re-run `20260627_120932`: 2× быстрее, social→clarify ≈ 0

### Этап B — Промпты (душа)

- kb_answer: блок «характер Насти», no repeat intro
- dispatcher: расширить smalltalk examples + «не clarify для social»
- conversation_policy: social intents table

### Этап C — Ошибки и MCP

- Мягкие fallback в root (product/doc без MCP)
- Live run с faq + kbsearch + dbhub
- compare_runs baseline vs new

### Этап D — UX live

- ACK + route-aware interim (bot)
- VOICE_AGENT для polish kb_answer (не smalltalk)
- Human rubric scorer в `run_dialogues.py`

---

## 5. Голос Насти (character)

- Коллега внутри АСЖ, на «Вы»
- Тепло **без** фамiliarity: «Рада помочь», «Буду на связи» — да; «😊», сленг — нет
- Кратко: 1–2 предложения social; 2–4 content
- Имя **один раз** в сообщении, не в каждом turn
- Представление **один раз за сессию**

---

## 6. Acceptance checklist

- [x] real-01 turn 4 «позже вернусь» → defer, не clarify
- [x] real-05 «что нового?» → smalltalk
- [x] real-10 «на связи завтра» / «завтра продолжим» → defer smalltalk
- [x] real-04 «Fort Knox» после vague → kb_answer
- [x] real-11 «как дела?» mid-session → без full intro
- [ ] real-10 «хорошего вечера» → fast-path (после restart ADK)
- [ ] MCP live run + мягкие no_data
- [ ] Human score ≥ 7/10 local, ≥ 8/10 с MCP

---

## 7. Команды

```bash
source .env
python tester/dialogues/run_dialogues.py --adk-base http://127.0.0.1:8080
python tester/dialogues/compare_runs.py 20260627_115139 latest
```
