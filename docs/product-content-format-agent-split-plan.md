# Product content/format split: change plan

## Current implementation

The repository currently has:

- `product_info_agent` and `product_filter_agent`, each combining DBHub tools
  with an ADK `output_schema`;
- `ProductInfoResponseSchema` and `validate_product_info_result()` in
  `agent/agents/product_info_agent.py`;
- `ProductFilterResponseSchema` and `validate_product_filter_result()` in
  `agent/agents/product_filter_agent.py`;
- one `RootAgent` call per product route through `run_json_leaf_agent()`;
- semantic validation in `run_json_leaf_agent()`, but no explicit
  `ResponseSchema.model_validate(extracted)` call before it;
- two combined prompts:
  `product_info/product_info_agent_prompt.md` and
  `product_filter/product_filter_agent_prompt.md`.

The following sections list only changes required from that state.

## 1. Split each existing factory into two agents

Modify `agent/agents/product_info_agent.py`:

- replace `create_product_info_agent()` with:
  - `create_product_info_content_agent()`;
  - `create_product_info_format_agent()`;
- move the existing DBHub tool setup and `PRODUCT_INFO_TOOL_FILTER` use into
  `product_info_content_agent`;
- set its output key to `product_info_content_result_json`;
- do not set `output_schema` on the content agent;
- configure `product_info_format_agent` without tools;
- keep its output key as the existing public key
  `product_info_result_json`;
- keep `ProductInfoResponseSchema` and `validate_product_info_result()` in this
  module and assign `ProductInfoResponseSchema` only to the format agent.

Modify `agent/agents/product_filter_agent.py` in the same way:

- replace `create_product_filter_agent()` with:
  - `create_product_filter_content_agent()`;
  - `create_product_filter_format_agent()`;
- keep DBHub tools only on `product_filter_content_agent`;
- set its output key to `product_filter_content_result_json`;
- set no `output_schema` on the content agent;
- keep `product_filter_format_agent` tool-free;
- keep its output key as `product_filter_result_json`;
- retain `ProductFilterResponseSchema` and
  `validate_product_filter_result()` and assign the schema only to the format
  agent.

Do not create additional Python modules for the four agents. Keeping the two
current route modules minimizes imports and preserves the existing locations of
the schemas and legacy validators.

## 2. Replace the two combined prompts with four stage prompts

Add:

- `kb_storage/prompts/product_info_content/product_info_content_agent_prompt.md`;
- `kb_storage/prompts/product_info_format/product_info_format_agent_prompt.md`;
- `kb_storage/prompts/product_filter_content/product_filter_content_agent_prompt.md`;
- `kb_storage/prompts/product_filter_format/product_filter_format_agent_prompt.md`.

Change the current factory prompt names and watchers to these four files.

Move from each existing combined prompt:

- tool selection, catalog lookup, SQL rules, resolver inputs, and evidence
  collection into its content prompt;
- response modes, final field rules, and user-facing answer formatting into its
  format prompt.

The content prompt must return one internal JSON object containing the data
needed by the formatter, including the successful SQL result used for the
answer. The format prompt must reference the corresponding
`product_*_content_result_json` state value, use only that supplied evidence,
call no tools, and return one final schema-compatible object.

Delete the two old combined prompt files only after repository-wide references
to their filenames are removed. Preserve all moved Russian text exactly and
save the four new files as UTF-8.

## 3. Extend `run_json_leaf_agent()` for the two-stage flow

Modify `agent/json_leaf_runner.py` with three optional inputs:

```python
response_schema: type[BaseModel] | None = None
tool_calls_state_key: str | None = None
tool_events_state_key: str | None = None
```

Store the collected tool calls and tool-event summaries under the requested
state keys before parsing or validation. Existing callers that omit these
arguments must behave as they do now.

When `response_schema` is supplied, change the validation path from:

```python
parsed = validator(extracted, validator_context)
```

to:

```python
schema_result = response_schema.model_validate(extracted)
schema_data = schema_result.model_dump()
parsed = validator(schema_data, validator_context)
```

This makes structural validation explicit before the existing semantic
validator. Do not remove `output_schema` from either format agent and do not
remove or weaken either legacy validator.

Allow the content-stage call to parse and store its internal JSON result without
running a final response schema or product semantic validator. Add only a
minimal content check: the result must be a non-empty JSON object.

## 4. Change `RootAgent` from one call to two calls per product route

Modify the `RootAgent` fields, constructor parameters, and `sub_agents` list:

```text
product_info_agent
  -> product_info_content_agent
  -> product_info_format_agent

product_filter_agent
  -> product_filter_content_agent
  -> product_filter_format_agent
```

Change `_handle_product_info()`:

1. Keep the existing query expansion, profile state, intent state, and product
   resolution preparation.
2. Run `product_info_content_agent`.
3. Store its parsed internal result and tool evidence under:
   - `_product_info_content_result_parsed`;
   - `_product_info_content_tool_calls`;
   - `_product_info_content_tool_events`.
4. Reject an empty content result or missing `execute_sql` when the existing
   semantic rules require SQL.
5. Run `product_info_format_agent`.
6. Pass `ProductInfoResponseSchema` to `run_json_leaf_agent()`.
7. Pass the content agent's recorded tool calls into the semantic validation
   context instead of the format agent's empty tool-call list.
8. Continue using the existing `_product_info_result_parsed`,
   final-message formatting, dialog-context updates, and bot-action logic.

Apply the equivalent changes to `_handle_product_filter()` using:

- `_product_filter_content_result_parsed`;
- `_product_filter_content_tool_calls`;
- `_product_filter_content_tool_events`;
- `ProductFilterResponseSchema`;
- the existing `_product_filter_result_parsed` final key.

Add all new internal content, evidence, and retry keys to the existing
turn-state cleanup lists.

Do not change dispatcher routes, supported intents, resolver behavior, public
final keys, response schemas, final text formatting, or bot actions.

## 5. Add one bounded format-only correction

Add a small `RootAgent` helper used by both product handlers:

1. Run the format agent and perform schema-first plus semantic validation.
2. If it raises `AgentValidationFailure`, store a correction instruction with:
   - the validation error;
   - the previous invalid payload when available;
   - an instruction to return one corrected object using the unchanged content
     evidence.
3. Clear only the failed format raw and parsed state keys.
4. Run the same format agent one more time.
5. If the second format result fails, re-raise the failure to the existing
   application fallback.

The limit is two format attempts and one correction. Do not rerun the content
agent, DBHub tools, resolver, or SQL. Clear the correction instruction and
attempt counter with the other turn-scoped state.

## 6. Update agent construction

Modify `agent/start_agent.py`:

- import the four new factory functions from the two existing route modules;
- construct all four agents with the current common model;
- inject all four into `RootAgent`;
- remove construction and injection of the two combined agents.

Keep the current route-specific temperature for both parts of each route. Do
not add new configuration variables as part of this change.

## 7. Update only affected tests

Modify:

- `tests/unit/agent/test_product_info_agent.py`:
  verify the content agent has DBHub tools and no schema, the format agent has
  no tools and retains `ProductInfoResponseSchema`, and retain existing schema
  and validator tests;
- `tests/unit/agent/test_product_filter_agent.py`:
  add the equivalent assertions and retain current contract tests;
- `tests/unit/agent/test_json_leaf_runner.py`:
  verify explicit Pydantic validation runs before semantic validation, invalid
  structure does not reach the semantic validator, and content tool evidence
  is stored;
- `tests/unit/agent/test_rootagent.py`:
  verify content then format ordering, propagation of content tool calls,
  exactly one format retry, no content or SQL retry, and existing final keys;
- `tests/unit/agent/test_start_agent.py`:
  replace the two combined factory stubs/assertions with four stage-agent
  stubs/assertions.

Run:

```powershell
.\venv\Scripts\python.exe -m pytest -p no:cacheprovider -s `
  tests\unit\agent\test_product_info_agent.py `
  tests\unit\agent\test_product_filter_agent.py `
  tests\unit\agent\test_json_leaf_runner.py `
  tests\unit\agent\test_rootagent.py `
  tests\unit\agent\test_start_agent.py
```

Then run:

```powershell
.\tests\run-unit-tests.ps1
```

## Completion criteria

- Both current combined factories are replaced by content/format factory pairs.
- Only content agents have DBHub tools.
- Only format agents have the retained `output_schema`.
- Both final payloads pass explicit `model_validate()` before their retained
  legacy validators.
- Semantic validation receives the corresponding content agent's tool evidence.
- A validation correction makes at most one additional format call and never
  repeats content retrieval or SQL.
- Existing routes, public final state keys, final formatting, and bot actions
  pass their current regression tests.
