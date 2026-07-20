# Timing summary by agent

| agent | hits | sum_ms | avg_ms | p50_ms | max_ms | share_of_wall_pct |
| --- | --- | --- | --- | --- | --- | --- |
| wall (e2e) | 3 | 50363 | 16787.7 | 18276.0 | 20935 | 100.0 |
| owasp | 3 | 5482 | 1827.3 | 1785.0 | 2448 | 10.9 |
| dispatcher | 3 | 20177 | 6725.7 | 6253.0 | 8414 | 40.1 |
| doc_search | 0 |  |  |  |  |  |
| kb_answer | 3 | 24516 | 8172.0 | 9508.0 | 11213 | 48.7 |
| product_selection | 0 |  |  |  |  |  |

## Tokens / TTFT

| agent | hits | avg_raw_ttft_ms | avg_visible_ttft_ms | sum_input_tokens | sum_output_tokens | sum_cached_tokens | sum_reasoning_tokens | sum_tool_calls | sum_model_turns |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| wall (e2e) | 3 |  |  |  |  |  |  |  |  |
| owasp | 3 | 1812.7 | 1812.7 | 7538 | 745 | 0 | 0 | 0 | 3 |
| dispatcher | 3 | 6714.7 | 6714.7 | 45654 | 2967 | 0 | 0 | 0 | 3 |
| doc_search | 0 |  |  |  |  |  |  |  |  |
| kb_answer | 3 | 4867.3 | 4867.3 | 26046 | 3873 | 0 | 0 | 4 | 4 |
| product_selection | 0 |  |  |  |  |  |  |  |  |

## Per question (ms)

| n | wall_ms | route | intent | owasp_ms | dispatcher_ms | doc_search_ms | kb_answer_ms | product_selection_ms | error | question |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2 | 11152 | kb_answer | smalltalk | 1785 | 5510 |  | 3795 |  |  | Что ты умеешь? |
| 3 | 18276 | kb_answer | smalltalk | 2448 | 6253 |  | 9508 |  |  | О продуктах какой компании ты можешь проконсультировать ? |
| 5 | 20935 | kb_answer | kb_answer | 1249 | 8414 |  | 11213 |  |  | Что предложить клиенту, у которого деньги просто лежат? |
