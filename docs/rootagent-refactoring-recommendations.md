# RootAgent Refactoring Recommendations

## Scope

This document reviews only `agent/rootagent.py`. Unit tests and other production modules are outside its scope.

The comparison uses:

- `446a85f`: the implementation immediately before the `5279ce7` refactor;
- `5279ce7`: the initial `refactoring rootagent` commit;
- the current branch implementation.

## Size and structure

| Version | Lines | RootAgent methods |
|---|---:|---:|
| Pre-refactor `446a85f` | 2,311 | 55 |
| Initial refactor `5279ce7` | 2,173 | 61 |
| Current implementation | 2,737 | 70 |

The original refactor reduced the file by 138 lines. Most of the current size growth accumulated afterward through additional product-dialog, product-validation, and smalltalk-context behavior.

## Improvements over the old implementation

### 1. The main pipeline is easier to follow

The old `_run_async_impl` was approximately 430 lines and performed preparation, safety checks, dispatching, route execution, state updates, and error recovery inline.

The current normal execution path delegates work to explicit stages:

- `_prepare_pipeline_context`;
- `_check_safety_guardrails`;
- `_try_short_circuit`;
- `_run_llm_dispatcher`;
- `_execute_target_agent`.

This makes the normal control flow significantly easier to read.

### 2. Turn data has an explicit container

`PipelineContext` provides named per-turn data instead of repeatedly extracting every value from the invocation context.

### 3. Repeated configuration is centralized

Regular expressions and `STATE_KEYS_TO_CLEAR` are defined once at module level instead of being reconstructed or repeated inside methods.

### 4. Product processing has clearer stages

Product routes now explicitly perform content generation followed by formatting and contract validation. This separates data acquisition from presentation concerns and preserves content-agent tool evidence for final validation.

### 5. Several responsibilities have already been extracted

JSON leaf execution, product contracts, product resolution, smart fallback generation, and stage metrics live in dedicated modules. This is the correct architectural direction.

## Remaining problems

### 1. RootAgent is still a god object

The class currently owns:

- database access and cross-session cache restoration;
- dialog history;
- session-state serialization;
- regex-based routing rules;
- product resolution;
- product-dialog state transitions;
- dispatcher orchestration;
- every route handler;
- validation fallback and error recovery;
- final ADK event construction.

These responsibilities change for different reasons and should not belong to one class.

### 2. PipelineContext is only a partial abstraction

Although `PipelineContext` exists, most methods continue to read and mutate `ctx.session.state` directly through string keys. The state schema therefore remains implicit and mutations are difficult to trace.

### 3. Several behavioral methods remain too large

The main hotspots are:

- `_product_followup_dispatch`: approximately 214 lines;
- `_store_product_dialog_context`: approximately 149 lines;
- `_contextual_smalltalk_followup_dispatch`: approximately 126 lines;
- `_get_explicit_intent_dispatch`: approximately 106 lines;
- `_build_final_event_with_history`: approximately 103 lines;
- `_run_async_impl`: approximately 162 lines, mostly because error recovery remains inline.

### 4. Product handlers duplicate the same workflow

`_handle_product_filter` and `_handle_product_info` both:

1. prepare the effective query;
2. resolve product context;
3. run a content agent;
4. run a format agent;
5. validate and read the result;
6. update dialog state;
7. construct the final response or bot action.

This workflow should be expressed once and parameterized by route-specific configuration.

### 5. Routing rules are mixed with orchestration

Explicit intent detection, product follow-ups, contextual smalltalk, pagination, and document follow-ups are domain rules. They should calculate a dispatch decision without knowing about ADK agents, event generation, database access, or session persistence.

### 6. State shapes are not enforced

The implementation relies heavily on `Dict[str, Any]`. This allows incompatible shapes to cross method boundaries. For example, `_store_product_dialog_context` can assign a dictionary to `products`, while `_normalize_dialog_products` accepts only a list.

### 7. Persistence is coupled to the orchestrator

`rootagent.py` imports `asyncpg`, performs the history query, and manages a class-level cross-session cache. Session restoration should be a separate service with a narrow interface.

### 8. Error recovery obscures the orchestration method

The successful path in `_run_async_impl` is compact, but the method is still long because it constructs validation context and fallback responses inline. This also makes recovery policy harder to understand independently from routing.

## Recommended module boundaries

### `rootagent.py`

Keep only ADK wiring and high-level pipeline coordination. A practical target is approximately 300–500 lines.

### `product_dialog_flow.py`

Move product normalization, product context storage, clarification handling, comparison enrichment, and product follow-up decisions here.

### `routing_rules.py`

Move explicit-intent, contextual-smalltalk, pagination, document-follow-up, and related regular-expression rules here. Prefer pure functions that return dispatch decisions.

### `session_state.py`

Provide typed access to session keys, turn reset behavior, history updates, and state transitions. Centralize invariants such as `products` always being a list.

### `session_recovery.py`

Move database history checks and cross-session cache restoration behind a dedicated service.

### `product_pipeline.py`

Implement the shared content-agent to format-agent to validation to state-update workflow for both product routes.

### `root_error_handler.py`

Move `AgentValidationFailure` interpretation, fallback-context construction, and recovery-message selection here.

### `event_builder.py`

Move final ADK event creation, state-delta assembly, and bounded-history updates here.

## Recommended refactoring order

1. Extract the duplicated product pipeline without changing behavior or state keys.
2. Extract pure routing rules and their constants.
3. Introduce typed session-state accessors and explicit state invariants.
4. Extract product-dialog state transitions.
5. Extract error recovery and event construction.
6. Move persistence and cross-session recovery out of the orchestrator.
7. Reduce `RootAgent` to dependency wiring and pipeline sequencing.

Each step should be a structural move only. Avoid changing routing behavior, prompts, contracts, and session-key names in the same change, because combining architectural and behavioral changes would make regressions difficult to isolate.
