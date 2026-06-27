# Conversation policy (dialogue manager)

Цели steering-слоя:
- уместный next step после релевантных ответов;
- уточнение при vague-запросах;
- мягкий redirect после 3 off-topic smalltalk подряд;
- без CTA-spam;
- **social smalltalk без clarify** (plan 002).

## Social smalltalk (rule-based)

Детерминированные ответы через `render_smalltalk_reply()` — быстро, без повторного intro:

| kind | примеры | тон |
|------|---------|-----|
| greeting (1st) | привет, доброе утро | представление один раз |
| greeting (repeat) | привет mid-session | «Здравствуйте.» |
| thanks | спасибо, спасибо за помощь | «Пожалуйста.» / «Рада была помочь.» |
| farewell | хорошего вечера, на связи | «До связи!» |
| defer | позже вернусь, на сегодня всё | «Буду на связи…» |
| chitchat | как дела, что нового | тепло + «Чем помочь?» |
| capabilities | что умеешь | 2 предложения + CTA |

## Whitelist CTA

| Условие | CTA |
|---------|-----|
| `kb_answer` + упоминание продукта в ответе | «Могу показать карточку продукта или подготовить комплект документов.» |
| `doc_search` success | «Могу показать ещё документы или отправить файлы по номерам из списка.» |
| `product_filter` | follow-up уже в product_selection |
| иначе | **без CTA** |

## Smalltalk limit

- Счётчик `smalltalk_turns` ↑ только для chitchat/other (не thanks/farewell/defer).
- При `smalltalk_turns >= 3` — redirect.

## Clarification

Vague без сущности → `needs_clarification`. **Не** для social.

Follow-up: короткий ответ «Fort Knox» после clarify → `kb_answer`.

## Persistent state keys

- `dialog_phase`: greeting | working | wrap_up
- `dialog_topic`
- `smalltalk_turns`
- `last_route`
- `last_cta`
- `pending_clarification`
- `session_intro_done`

Ключи `dialog_*` не очищаются в `_clear_state_keys` root_agent.
