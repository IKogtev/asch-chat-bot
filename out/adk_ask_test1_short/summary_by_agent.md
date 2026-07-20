# Timing summary by agent

| agent | hits | sum_ms | avg_ms | p50_ms | max_ms | share_of_wall_pct |
| --- | --- | --- | --- | --- | --- | --- |
| wall (e2e) | 3 | 25203 | 8401.0 | 6016.0 | 13350 | 100.0 |
| owasp | 0 |  |  |  |  |  |
| dispatcher | 0 |  |  |  |  |  |
| doc_search | 0 |  |  |  |  |  |
| kb_answer | 0 |  |  |  |  |  |
| product_selection | 0 |  |  |  |  |  |

## Per question (ms)

| n | wall_ms | route | intent | owasp_ms | dispatcher_ms | doc_search_ms | kb_answer_ms | product_selection_ms | error | question |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2 | 6016 |  |  |  |  |  |  |  |  | Что ты умеешь? |
| 3 | 5837 |  |  |  |  |  |  |  |  | О продуктах какой компании ты можешь проконсультировать ? |
| 5 | 13350 |  |  |  |  |  |  |  |  | Что предложить клиенту, у которого деньги просто лежат? |
