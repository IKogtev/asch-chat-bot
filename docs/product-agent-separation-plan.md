# Product agent separation plan

## Goal and target contract

Replace `product_selection_agent` with two agents while keeping the public ADK app, final text responses, bot actions, OWASP, document search, KB answers, and glossary expansion unchanged.

| Agent | Dispatcher route | Intents | Allowed output modes |
| --- | --- | --- | --- |
| `product_info_agent` | `product_info` | `product_card`, `product_kit` | `product_card`, `product_kit`, `needs_clarification`, `no_data` |
| `product_filter_agent` | `product_filter` | `product_filter`, `product_compare`, `product_attribute_values` | `product_filter`, `product_compare`, `product_attribute_values`, `needs_clarification`, `no_data` |

`needs_clarification` and `no_data` remain output modes, not dispatcher intents. Both agents need them because either branch can be ambiguous or lack data.

Use explicit routes rather than keeping a broad `product_selection` route and mapping by intent in RootAgent. This makes the chosen agent visible in validation and logs, and prevents an implicit routing rule from drifting.

## Current coupling to remove

- `agent/agents/product_selection_agent.py` combines the MCP factory, validator, modes, prompt fallback, output key, and temperature.
- `agent/rootagent.py` has one `product_selection_agent` field, one `_handle_product_selection` handler, one parsed key (`_product_selection_result_parsed`), and a single product route branch.
- `agent/start_agent.py` builds and injects one product agent.
- `agent/config.py` exposes only `PRODUCT_SELECTION_TEMPERATURE`.
- `kb_storage/prompts/product_selection/product_selection_agent_prompt.md` contains every product workflow.
- `agent/agents/dispatcher_agent.py` and `kb_storage/prompts/dispatcher/dispatcher_agent_prompt.md` allow only `route="product_selection"`.
- `tests/unit/agent/test_product_selection_agent.py`, `test_rootagent.py`, and `test_start_agent.py` hard-code the old module, property, and output key.

## Code and validator split

Create:

- `agent/agents/product_info_agent.py`
- `agent/agents/product_filter_agent.py`
- `agent/agents/product_result_validation.py` for private shared normalization helpers

Move only pure common helpers to `product_result_validation.py`: normalization of `used_tables`, product objects, clarification options, lists, text lists, tool calls, and construction of the normalized common result. Keep agent-specific mode sets, loggers, and semantic validation in the two agent modules.

Expose two validators:

- `validate_product_info_result(data, context)` accepts only info modes; it requires `resolved_product` for card and kit, and `resolved_product.code` for kit.
- `validate_product_filter_result(data, context)` accepts only filter modes; it requires values for `product_attribute_values` and non-empty options for `needs_clarification`.

### Structured response contracts

Follow the `kb_answer_agent` pattern for both product agents: define a Pydantic
response model, pass it as `LlmAgent.output_schema`, and retain the explicit
runtime validator. `output_schema` gives the model a constrained JSON target;
the validator remains necessary for state-dependent checks such as the
`execute_sql` requirement and resolver-derived exceptions.

Create these models in the owning agent modules:

- `ProductInfoResponseSchema`: `status="ok"`; info modes only; `message`;
  `used_tables`; `resolved_product`; `clarification_options`; and the existing
  empty-compatible common fields (`products`, `attribute_name`,
  `attribute_column`, `attribute_values`).
- `ProductFilterResponseSchema`: `status="ok"`; filter modes only; `message`;
  `used_tables`; `resolved_product`; `clarification_options`; `products`;
  `attribute_name`; `attribute_column`; and `attribute_values`.

Use `Literal` for the allowed modes and `Field(default_factory=...)` for mutable
list fields. Preserve nullable `resolved_product` and the complete current
normalized result shape. The output schemas must not attempt to encode
tool-call history or resolver state; those are validated after the response is
parsed.

Both must return the current normalized result shape so RootAgent and bot code do not need a new external contract:

```text
status, mode, message, used_tables, resolved_product,
clarification_options, products, attribute_name, attribute_column,
attribute_values
```

Retain current tool-use policy: substantive modes require `execute_sql`; `needs_clarification` may omit it only with resolver options; `no_data` may omit it.

Preserve the current kit exception during this split. The existing validator accepts a kit without `execute_sql` when `resolved_product.folder_kit` exists, while RootAgent can resolve `folder_kit` in Python and force a kit action after the LLM returns `no_data`. Tightening this inconsistency is a separate behavioral change; move that code unchanged into the info branch.

Each agent receives its own `RefreshingMcpToolset` with the current read-only DBHub filter, logger, watcher, output key, and fallback prompt. A small factory helper is acceptable only for the duplicated MCP connection block.

| Agent | Prompt | Raw key | Parsed key |
| --- | --- | --- | --- |
| `product_info_agent` | `kb_storage/prompts/product_info/product_info_agent_prompt.md` | `product_info_result_json` | `_product_info_result_parsed` |
| `product_filter_agent` | `kb_storage/prompts/product_filter/product_filter_agent_prompt.md` | `product_filter_result_json` | `_product_filter_result_parsed` |

`load_prompt` and `start_prompt_watcher` already derive the folder from the prefix before `_agent`, so these names need no loader change. Do not reuse `product_selection_result_json`: a stale result from the other branch must never be parsed as the current answer.

## Temperatures

Replace `PRODUCT_SELECTION_TEMPERATURE` with:

- `PRODUCT_INFO_TEMPERATURE`, defaulting to `ROOT_TEMPERATURE`;
- `PRODUCT_FILTER_TEMPERATURE`, defaulting to `ROOT_TEMPERATURE`.

Keep the current `-1` behavior (omit `GenerateContentConfig`) in both factories. Initially set both values to the existing product-agent value in the runtime owned by the application team, then tune them independently from measured results. Infrastructure configuration changes are explicitly out of scope for this plan.

## Dispatcher changes

In `validate_dispatcher_result`:

1. Replace the `product_selection` route with `product_info` and `product_filter`.
2. Define `product_info_intents = {"product_card", "product_kit"}` and `product_filter_intents = {"product_filter", "product_compare", "product_attribute_values"}`.
3. Require each intent to use its exact route, while retaining all current document, KB, smalltalk, reason, and query-presence validation.

Update the fallback and live dispatcher prompts to name both downstream agents and to apply the same mapping:

- card, a bare product code, and a full kit -> `product_info`;
- a list/filter, a selected attribute value, and comparison -> `product_filter`.

Keep the existing high-priority rules: applicability and eligibility questions stay in `kb_answer`; a normal file request stays in `doc_search`; only a complete product kit goes to product information.

## RootAgent and follow-up changes

Replace the `product_selection_agent` constructor field and `sub_agents` item with the two agents. `start_agent.py` must build both from the shared model and inject both into `RootAgent`.

Split `_handle_product_selection` into shared setup plus two handlers:

- shared setup expands the glossary query, loads profile state, logs the request, and preserves the current error/final-event handling;
- `_handle_product_info` sets `product_info_intent` and `product_info_search_query`, prepares only `product_resolution`, runs the info validator and agent;
- `_handle_product_filter` sets `product_filter_intent` and `product_filter_search_query`, prepares `product_filter_resolution` for filters or `product_resolutions` for comparison, then runs the filter validator and agent.

Update `_reset_turn_state` and explicit cleanup in `_run_async_impl` to clear both parsed/raw result keys. Dispatch `route="product_info"` and `route="product_filter"` to their exact handler. Keep `last_route` equal to the new concrete route.

Continue using one `_product_dialog_context`; it describes the conversation rather than the old implementation. It must still store attribute data after `product_attribute_values`, product lists after `product_filter`, selected products after card/kit, and comparison context. Add an agent field only if diagnostics need it; never use it instead of the validated route and intent.

Retain the existing formatter and context store initially, optionally renaming `_format_product_selection_answer` to `_format_product_answer` in the same focused change. Preserve the filter/attribute follow-up questions, card-to-kit offer, clarification formatting, and kit `_bot_action`.

Move the Python `folder_kit` enrichment and `no_data` -> `product_kit` fallback into `_handle_product_info` only. It must not run for filters or comparisons.

Update every code-generated dispatcher result in `_get_explicit_intent_dispatch` and `_product_followup_dispatch`:

| Follow-up behavior | Route and intent |
| --- | --- |
| Explicit kit, confirmation after a card, card details, selected product code | `product_info` / `product_kit` or `product_card` |
| Explicit archive/list filter | `product_filter` / `product_filter` |
| Attribute value chosen from a prior value list | `product_filter` / `product_filter` |
| Blind comparison enriched from the prior product list | `product_filter` / `product_compare` |
| Product-specific ordinary document request | unchanged: `doc_search` / `doc_search` |

Update the card-confirmation gate that currently checks `last_route == "product_selection"` to check `product_info`. Do not alter the independent document-list gate (`last_route == "doc_search"`).

## Prompt division

Create self-contained UTF-8 prompts instead of composing fragments: the current watcher reloads one file per agent, so one complete file per agent is safer. Live prompt files must be written in Russian; in-code fallback prompts remain in English, matching the existing agent convention. Keep field names, JSON keys, SQL, tool names, and code identifiers unchanged.

Both prompts retain the applicable common safeguards: glossary handling, tool-first catalog workflow, read-only SQL, facts only from the current run, and `word_similarity` rules whenever a product name is searched.

`product_info_agent_prompt.md` contains only:

- card and kit modes;
- `product_resolution` and ambiguity rules;
- SQL-backed card rules;
- kit code/folder rules and the message that the bot sends the kit;
- info-agent JSON contract.

It must not mention `product_filter_resolution`, list formatting, attribute follow-ups, or comparison.

`product_filter_agent_prompt.md` contains only:

- filters, attribute-value discovery, and comparison;
- limits on `product_filter_resolution` and `product_resolutions` as code sources rather than fact sources;
- mandatory `search_analytic` before exact categorical values;
- `is_active` list formatting, `products` population, and attribute follow-up fields;
- complete comparison-column and two-product SQL rules;
- filter-agent JSON contract.

It must not mention kit delivery or use `product_resolution` as a fact source. Preserve existing Russian user-facing strings exactly when moving them. Replace or remove the stale `kb_storage/prompts/test_prompts` fixtures rather than copying their obsolete `ILIKE` rules.

## Qwen 3 to Qwen 3.6 transition analysis

The repository already has a broader Qwen 3.6 migration plan that calls for a
baseline, prompt adaptation, per-agent temperature decisions, and a decision on
whether follow-up code is still needed. The product split and the Qwen 3.6 move
are one combined change: do not create, deploy, or test a separately split
Qwen 3 branch. All model-level validation for the new agents is performed on
the Qwen 3.6 target environment as part of the migration.

The combined change should make three transition-oriented changes:

1. Use the Pydantic `output_schema` contracts above. `kb_answer_agent` already
   uses this pattern, and `json_leaf_runner` already accepts a dictionary or a
   Pydantic `model_dump()` result. No parser rewrite is required for typed
   output.
2. Reduce prompt scope by splitting the current large, cross-scenario prompt;
   retain only the applicable SQL and safety rules in each Russian live prompt
   and English fallback prompt. Keep the final instruction short and exact:
   return one JSON object that matches the response schema, with no Markdown
   fences or prose outside it.
3. Do not add Qwen-specific chat-template syntax, XML tool-call tags, or
   `<think>` instructions to application prompts. Tool parsing is a model-server
   integration concern. The existing `json_leaf_runner.strip_thought_parts`
   already removes ADK `thought` parts from user-visible events; verify that the
   chosen Qwen 3.6 server marks reasoning in that supported representation.

Before enabling the combined split-and-Qwen-3.6 change, run an integration
matrix against the actual LLM endpoint and its configured tool-call parser:

| Check | Required evidence |
| --- | --- |
| Structured output | Each agent produces a schema-valid object for every allowed mode; malformed output becomes a normal validation failure, not an uncaught exception. |
| MCP calls | The model invokes `search_semantic_template` first and completes the required catalog/`execute_sql` sequence. Tool call names and arguments reach `RefreshingMcpToolset` in the format accepted by the deployed server. |
| Reasoning isolation | Reasoning is absent from final text and does not displace function-call or JSON content. |
| Sampling | Establish separate temperature baselines for info and filter agents; do not copy Qwen 3 values without measurement. |
| Follow-ups | Compare the current RootAgent short-circuits with Qwen 3.6 dispatcher behavior. Retain deterministic code until tests show that removing or simplifying a rule preserves all routes. |
| Context and latency | Measure multi-turn card -> kit, attribute -> filter, and filter -> compare flows for accuracy, tool-call count, and latency. |

The official Qwen 3.6 repository currently states that its user guide is still
coming, while the Qwen API advertises OpenAI- and Anthropic-compatible APIs.
Therefore, do not assume a provider-specific generation parameter, reasoning
flag, or parser configuration in this application code. Record the exact model
identifier, serving stack, tool-call parser, reasoning setting, and ADK/LiteLLM
versions used for the integration matrix. If the server cannot produce valid
structured output or tool calls, the server/parser team must fix that boundary;
do not weaken product JSON contracts or add regex parsing for model-specific
XML output.

## Tests, documentation, and rollout

Split or replace `test_product_selection_agent.py` with two dedicated files:
`test_product_info_agent.py` and `test_product_filter_agent.py`. Both are
required unit-test suites, not deferred follow-up work.

For each agent, test its Pydantic response contract and factory configuration:

- only its declared modes are accepted by the schema and validator;
- the factory passes the correct `output_schema`, output key, prompt folder,
  watcher, DBHub tool filter, and independent temperature;
- defaults and optional fields serialize to the compatible common result;
- invalid schema payloads, empty messages, invalid nested values, and invalid
  modes fail predictably.

For `product_info_agent`, additionally test card/kit `resolved_product`
requirements, kit code requirements, resolver clarification, and the preserved
folder-kit exception. For `product_filter_agent`, additionally test
attribute-value fields, filter/compare SQL-call requirements, clarification,
product list normalization, and the complete comparison SQL safeguards.

Update RootAgent and start-agent stubs for two agents. Add dispatcher and RootAgent routing tests for:

- card/bare code -> info branch;
- kit and card-confirmation -> info branch and kit bot action;
- archive/focus/list filter -> filter branch;
- selected attribute value -> filter branch and a fresh SQL-backed filter;
- comparison/blind comparison -> filter branch and comparison resolver state;
- clarification from both branches, plus unchanged document-list follow-up.

Update `docs/agents-chain.md` and `docs/release_notes_glossary.md` when implementation changes the runtime. Do not add release notes until the split is actually released.

Implement in this order:

1. Add response schemas, modules, validators, Russian live prompts, English fallback prompts, settings, and isolated unit tests.
2. Change dispatcher contracts and RootAgent routing in the same change.
3. Update fixtures and architecture documentation.
4. Delete the old module, prompt, setting, and all references only after a repository-wide search is clean.
5. Run the Qwen 3.6 integration matrix only after the unit suite is green. Evaluate the new agents and any follow-up simplification together as the combined migration change; do not add a separate split-only model-validation stage.

Run the focused suite with the project interpreter:

```powershell
.\venv\Scripts\python.exe -m pytest -p no:cacheprovider -s tests\unit\agent\test_product_info_agent.py tests\unit\agent\test_product_filter_agent.py tests\unit\agent\test_dispatcher_agent.py tests\unit\agent\test_rootagent.py tests\unit\agent\test_start_agent.py
```

Then run `./tests/run-unit-tests.ps1` and validate representative sandbox requests. Completion requires no application runtime reference to `product_selection_agent`, `product_selection_result_json`, or `PRODUCT_SELECTION_TEMPERATURE`; each agent must have an independent temperature, prompt watcher, output key, Pydantic response contract, validator, unit-test suite, and regression coverage.
