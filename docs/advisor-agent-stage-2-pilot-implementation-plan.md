# `advisor_agent` Stage 2 Pilot Implementation Plan

## 1. Purpose

This document turns Stage 2 from `advisor-agent-implementation-approaches-v2.md` into an actionable engineering plan.

The pilot must add a standalone `advisor` route that:

- collects and retains the minimum client profile;
- matches the collected facts to the approved client types from `kb_storage/manager/tables/typical_client_profiles_active.xlsx`;
- asks one high-impact clarification question when essential data is missing;
- retrieves current product facts from the approved product catalog;
- excludes products that violate hard eligibility constraints;
- ranks the remaining products with a simple, deterministic, explainable model;
- returns a client-specific TOP-3 with reasons and key tradeoffs;
- recalculates the TOP-3 when the client profile changes;
- answers follow-up questions about the recommendation;
- hands a selected product to the existing `product_info` route for a product card or document kit.

The pilot is successful when the agreed reference cases produce valid, explainable recommendations, all hard restrictions are enforced, context-dependent follow-ups work, and no existing route regresses.

## 2. Scope

### 2.1. Included

- One new dispatcher route: `advisor`.
- One new dispatcher intent: `advisor_recommendation`.
- A logical standalone advisor flow with separate content and formatting components, consistent with the current product-agent architecture.
- Structured client-profile extraction and incremental profile updates.
- Runtime use of the Client Types table as the source of client-type definitions, required properties, preferences, compromises, and contraindications.
- Inclusion of the Client Types workbook in the shared `load_tables` action used by both `.\load_tables.ps1` and the KB Manager UI.
- One clarification question per turn when a recommendation cannot safely be produced.
- Current-catalog retrieval through DBHub using read-only SQL.
- Deterministic hard filtering and client-fit scoring.
- TOP-3 generation with traceable score components and explanations.
- Persistent advisor context in ADK session state.
- Ordinal and pronoun resolution for phrases such as "the first one," "the second option," and "this product."
- Handoff to `product_info` for product-card and product-kit requests.
- Unit, contract, integration, and manual end-to-end checks.
- Pilot feature flag, structured logs, and operational metrics.

### 2.2. Explicitly excluded

- Any scoring bonus based on focus-product status.
- Any scoring bonus based on manager commission (KV).
- Machine-learning ranking, statistical clustering, semantic candidate ranking, or optimization from sales outcomes.
- Automatic changes to business rules or weights.
- A new product-card or document-kit implementation; the pilot must reuse `product_info`.
- Changes to the existing responsibilities of `product_filter` and `product_info`.
- Client-facing financial advice or a claim that the assistant makes the final sales decision. The output is a recommendation for manager review.

Focus-product and KV influence belong to Stage 3. Their catalog fields may be retrieved for future compatibility, but the Stage 2 scorer must ignore them.

## 3. Required Stage 1 Inputs

The primary Stage 1 input is the Client Types table:

`kb_storage/manager/tables/typical_client_profiles_active.xlsx`

The current workbook contract is:

- worksheet: `Типовые профили`;
- table range: `A1:U5`;
- 21 source technical columns in row 1 and Russian business labels in row 2;
- one loader-generated database column, `client_type_code`, using stable source-row codes `CT-001`, `CT-002`, and so on;
- three client types: `Консервативный`, `Умеренный`, and `Агрессивный`;
- client-type inputs in `client_goal` through `additional_context`;
- product-matching rules in `required_properties`, `preferred_properties`, `acceptable_compromises`, and `contraindications`;
- no product codes: matching must use product property names and values.

Treat this workbook as the single source of truth for pilot client types and their product-property rules. Do not copy its rows into prompts, hardcode the three types in Python, or maintain a parallel suitability matrix.

Implementation must not begin until the following remaining inputs have named business owners:

1. Business approval of the current Client Types table and its exact property vocabulary.
2. A documented rule for matching a partially collected client profile to one or more table rows.
3. Pilot scoring weights for required, preferred, compromise, and contraindicated property matches.
4. At least 20 reference cases, including expected client-type matches, exclusions, and TOP-3 results.
5. A current product catalog with stable identity fields and an `as_of` or equivalent freshness field.
6. Confirmation of whether the assistant may recommend products or must phrase the result as options for manager review.

The workbook currently contains illustrative contribution amounts. Because the product catalog does not contain corresponding minimum-contribution fields, these amounts may guide client-type matching but must not be used as hard product eligibility filters until a grounded product-side field is available and approved.

If any required product attribute is absent from the catalog, record it as a data blocker. Do not infer the value from descriptions or silently treat it as favorable.

## 4. Architectural Decision

The `advisor` route will be standalone from the dispatcher and user perspective. Internally, it will follow the product flow already used by the repository:

```text
dispatcher_agent
    -> advisor_content_agent (profile extraction + verified Client Types and product facts)
    -> advisor contract validation
    -> deterministic AdvisorProfileMatcher and AdvisorRankingService
    -> advisor_format_agent
    -> RootAgent state update and final response
```

This split has three important properties:

- the LLM interprets natural language and retrieves catalog facts;
- Python code matches the client to table-defined types, enforces hard constraints, and calculates scores deterministically;
- the formatter explains an already-computed result and cannot change eligibility or ranking.

The existing table loader must expose the workbook as PostgreSQL table `typical_client_profiles`, derived from the single-sheet filename. The advisor content agent may call only approved DBHub discovery tools and `execute_sql`, and must read both `typical_client_profiles` and the current products table in every recommendation run. The format agent must have no tools.

Both operator entry points must use the same loader implementation:

```text
.\load_tables.ps1
    -> docker compose exec -T kb-manager
    -> python -m app.scripts.load_tables --strict-validation

KB Manager UI: "Загрузить таблицы"
    -> app.js: loadTables()
    -> POST /api/tables/load
    -> python -m app.scripts.load_tables --strict-validation
```

Do not add a separate advisor-only loader. The shared action must discover the mounted file at `/app/data/kb_documents/manager/tables/typical_client_profiles_active.xlsx`, load it as `typical_client_profiles`, validate it, and include its result in the common load log and UI table list.

## 5. Target Runtime Flow

### 5.1. New recommendation

1. The user asks what to offer a specific client.
2. `dispatcher_agent` returns `route="advisor"`, `intent="advisor_recommendation"`, and the user request as `search_query`.
3. `RootAgent` loads the persistent advisor context and passes the current message to the advisor content agent.
4. The content agent extracts a profile patch and merges it with the existing profile through validated code.
5. The content agent retrieves the valid client-type rows from `typical_client_profiles`; the contract rejects the Russian business-label row if it was loaded as data.
6. `AdvisorProfileMatcher` compares the collected facts with the table columns and returns one or more client-type matches with criterion-level evidence.
7. If a decision-changing field required to distinguish the best client-type match is missing, the flow returns `needs_clarification` with exactly one question and stops before product ranking.
8. Otherwise, the content agent retrieves current active products and the fields referenced by the matched client-type rows.
9. The advisor contract rejects malformed, ungrounded, or incomplete client-type or candidate data.
10. `AdvisorRankingService` applies the matched row's required properties and contraindications, scores preferred properties and approved compromises, and selects a diverse TOP-3.
11. The format agent produces the final user-facing explanation without changing client-type matches, product order, or facts.
12. `RootAgent` stores the profile, matched client types, candidates, TOP-3, explanations, source-table version, and catalog freshness in advisor context.

### 5.2. Profile refinement

1. The user adds or corrects a client fact, for example, "The client cannot accept capital loss."
2. The message is routed back to `advisor` using the existing advisor context.
3. The profile patch overwrites only explicitly changed fields.
4. The candidate set is refreshed from the catalog.
5. The Client Types table is refreshed, client-type matching is rerun, and product filtering/scoring is rerun from scratch.
6. The response states that the recommendation changed and shows the new TOP-3.

### 5.3. Recommendation explanation or comparison

- "Why is the second option below the first?" remains in `advisor`.
- The advisor resolves ordinal references from the stored TOP-3.
- The answer uses stored score components and verified product facts.
- No new ranking is calculated unless the client profile or catalog data changed.

### 5.4. Product-card or document-kit handoff

- "Show the card for the second option" routes to `product_info` with `intent="product_card"`.
- "Send the kit for the first one" routes to `product_info` with `intent="product_kit"`.
- `RootAgent` resolves the ordinal to an exact product identity tuple: `code`, `name`, and `is_active`.
- The existing product resolver, product-info agents, and `_bot_action` flow continue unchanged.
- Advisor context remains available after the handoff so the user can return to the recommendation.

## 6. Data Contracts

### 6.1. Client profile

Create a typed `AdvisorClientProfile` model. Fields should be optional while the profile is being collected, but validated when present.

The model fields must map explicitly to the technical columns in `typical_client_profiles`. Where the runtime representation is more structured than the workbook text—for example, a numeric age versus the table's `age_range`—implement a documented parser and comparison rule rather than changing or duplicating the workbook value.

Recommended pilot fields:

| Field | Type | Purpose |
|---|---|---|
| `goal` | enum/string | Protection, accumulation, regular income, education, retirement, or another approved goal |
| `target_date` or `term_months` | date/integer | Checks product duration against the client's horizon |
| `contribution_amount` | decimal | Checks minimum and permitted contribution amount |
| `contribution_frequency` | enum | Single, monthly, quarterly, annual, or approved values |
| `currency` | enum | Checks product/client currency compatibility |
| `capital_loss_tolerance` | enum | Enforces risk restrictions |
| `guarantee_required` | boolean | Enforces capital-guarantee requirements |
| `liquidity_need` | enum | Evaluates early access requirements |
| `client_age` | integer | Enforces product age limits |
| `insurance_need` | enum/boolean | Evaluates required protection |
| `investment_experience` | enum | Supports suitability rules where approved |

Additional collected fields may map to `family_context`, `income_stability`, and `additional_context`. They must be added only when the Client Types table uses them to distinguish profiles or apply an approved rule.

The model must also record provenance per field:

- `value`;
- `source_turn`;
- `updated_at`;
- `explicit` versus `inferred`.

Only explicit values may satisfy a hard eligibility requirement. An inferred value may guide the next clarification question but must not be used to qualify a product.

### 6.2. Advisor content result

The content agent returns one internal JSON object with a mode of either `needs_clarification`, `candidates`, or `no_data`.

Minimum structure:

```json
{
  "mode": "candidates",
  "profile_patch": {},
  "client_types_source": {
    "table": "typical_client_profiles",
    "source_file": "typical_client_profiles_active.xlsx",
    "loaded_at": "2026-08-27T00:00:00Z"
  },
  "client_types": [
    {
      "profile_name": "Консервативный",
      "attributes": {},
      "required_properties": [],
      "preferred_properties": [],
      "acceptable_compromises": [],
      "contraindications": []
    }
  ],
  "missing_fields": [],
  "clarification_question": "",
  "catalog_as_of": "2026-08-27",
  "products": [
    {
      "code": "2832",
      "name": "Example product",
      "is_active": "Active",
      "attributes": {},
      "source_table": "products",
      "source_row_identity": {}
    }
  ]
}
```

Contract rules:

- `needs_clarification` requires one non-empty question and at least one missing field;
- `candidates` requires at least one validated client-type row, at least one product, and current-run `execute_sql` calls for both source tables;
- every client-type row requires a non-empty `profile_name` and the four product-rule fields;
- a row with `profile_name="Тип профиля"` is descriptive metadata and must never be treated as a client type;
- only the three rows currently present in the workbook are expected initially, but code must be data-driven and accept newly approved rows without a release;
- every product requires a complete stable identity;
- only fields returned by SQL in the current run may appear in client-type or product `attributes`;
- property expressions must be parsed from the four workbook rule columns through one deterministic parser; the LLM must not reinterpret or silently rewrite them;
- the result must include the catalog freshness value;
- `no_data` is permitted only when the source was queried successfully and returned no usable rows, or when required catalog fields are unavailable;
- focus and KV values must never be accepted as score inputs in Stage 2.

### 6.3. Ranking result

`AdvisorRankingService` returns a typed result containing:

- matched client types and criterion-level match evidence;
- the primary client type used for ranking, plus any secondary match;
- accepted candidates;
- excluded candidates and machine-readable exclusion codes;
- total client-fit score for every accepted candidate;
- score components by approved criterion;
- detected compromises;
- the selected TOP-3 in final order;
- the Client Types source file/load version, scoring-policy version, and product-catalog freshness timestamp.

The format agent receives this result but cannot alter product order, scores, exclusions, or facts.

### 6.4. Persistent advisor context

Store one versioned object under `advisor_dialog_context`:

```json
{
  "schema_version": 1,
  "profile": {},
  "matched_client_types": [],
  "primary_client_type": null,
  "missing_fields": [],
  "candidate_products": [],
  "top_products": [],
  "exclusions": [],
  "selected_product": null,
  "client_types_source": null,
  "scoring_policy_version": "pilot-v1",
  "catalog_as_of": null,
  "updated_at": null
}
```

Keep this persistent object separate from per-turn intermediate keys. Add intermediate advisor keys to `STATE_KEYS_TO_CLEAR`, but do not clear `advisor_dialog_context` at the beginning of every turn.

## 7. Profile Collection and Clarification Logic

### 7.1. Profile merge rules

- Start a new profile when there is no advisor context or the user explicitly asks to advise for another client.
- Merge only fields supported by the current message.
- Explicit corrections replace older values and trigger a full recalculation.
- Conflicting values must produce a clarification rather than an arbitrary choice.
- Do not copy the manager's application profile fields into the client financial profile.
- Do not persist free-form sensitive details that are not required by the approved schema.
- Do not assign a client type from a single keyword. Compare all available facts with the corresponding Client Types table columns and retain the match evidence.

### 7.2. Choosing one question

Select the question deterministically from missing fields using this priority:

1. fields required to enforce a prohibition or eligibility rule;
2. fields that distinguish the two highest client-type matches in `typical_client_profiles`;
3. fields that can remove the largest portion of the current product candidate set;
4. fields with the largest approved scoring weight;
5. the business-defined tie-break order.

The question must be short, ask for only one decision, and explain why it matters when that is not obvious. The agent must not present a long questionnaire.

### 7.3. Recommendation threshold

The scoring policy must define a minimum client-type match confidence and a minimum profile completeness level. If either is not met, the flow must return `needs_clarification`; it must not generate a provisional TOP-3 that appears authoritative. A client may partially match multiple table rows, so the matcher must retain a primary and optional secondary type rather than forcing a match without evidence.

## 8. Hard Filtering and Ranking

### 8.1. Hard filtering

Apply hard rules before scoring. The matched row's `required_properties` and `contraindications` columns are authoritative for client-type-specific product filtering. Typical exclusions include:

- inactive or unavailable product;
- age outside the permitted range;
- term incompatible with the target date;
- contribution below the product minimum or incompatible frequency, but only after corresponding grounded product fields are available;
- unsupported currency when currency is mandatory;
- capital-loss risk above the client's explicit tolerance;
- missing guarantee when a guarantee is required;
- liquidity incompatible with an explicit early-access need;
- channel, region, or client-segment restriction;
- a mismatch with any expression in `required_properties`;
- a match with any expression in `contraindications`.

Every exclusion must have a stable code and a human-readable reason. A product that fails one hard rule cannot be restored by a high soft score.

### 8.2. Explainable score

Use a 0-100 client-fit score calculated from the matched Client Types row and the approved scoring policy. Do not put weights or copies of workbook property rules in prompts. The workbook supplies the rule values; a small versioned Python policy supplies only how required, preferred, compromise, and client-field matches are weighted.

Illustrative structure, with exact weights supplied by the business owner:

```text
goal fit                 0..W1
client-type fit          0..W2
preferred properties     0..W3
acceptable compromises   0..W4
individual client fit    0..W5
--------------------------------
client-fit score         0..100
```

Rules:

- the same loaded Client Types rows, product inputs, and scoring-policy version must always produce the same score;
- the same workbook row and scoring-policy version must always produce the same score;
- `required_properties` and `contraindications` act as gates, not soft bonuses;
- `preferred_properties` add positive fit only when their parsed property/value pairs match;
- `acceptable_compromises` may reduce the score by an approved bounded amount and must be disclosed;
- missing optional data contributes zero or a documented neutral value, never a favorable assumption;
- score components must sum exactly to the total;
- decimal rounding must be defined in code and covered by tests;
- focus and KV must have zero influence;
- ties use stable business tie-breakers, then product code as the final deterministic tie-breaker.

### 8.3. TOP-3 diversity

After sorting by client-fit score, apply the approved diversity rule so the TOP-3 is not three near-identical variants when materially different suitable alternatives exist.

The diversity pass may replace a lower-ranked duplicate-family product only when:

- the alternative passed all hard rules;
- the alternative is above the minimum suitability threshold;
- the maximum permitted score gap is not exceeded;
- the replacement reason is stored in the ranking result.

If fewer than three suitable products remain, return the available products and state that fewer than three passed the constraints. Never pad the result with unsuitable products.

## 9. User-Facing Response Contract

For each recommended product, return:

- rank, name, and product code;
- the two or three strongest client-specific reasons;
- two or three relevant verified properties;
- the main compromise or limitation;
- a next action: compare recommendations, explain one option, show a card, or send a kit.

The response must:

- be concise enough for Telegram and Max;
- use lists instead of Markdown tables;
- distinguish verified product facts from recommendation reasoning;
- avoid internal scores unless the business explicitly approves showing them;
- never mention SQL, table names, prompt rules, focus, or KV;
- state catalog freshness when the data is not current enough for the approved SLA;
- state that the manager must verify suitability when required by policy.

## 10. Repository Changes by Component

### 10.1. New files

| File | Responsibility |
|---|---|
| `agent/agents/advisor_content_agent.py` | Create the DBHub-enabled content agent |
| `agent/agents/advisor_format_agent.py` | Format validated ranking results without tools |
| `agent/agents/advisor_contract.py` | Validate content and final advisor contracts |
| `agent/advisor_profile.py` | Typed profile, merge rules, conflict handling, and completeness checks |
| `agent/advisor_profile_matcher.py` | Parse Client Types rows and deterministically match a client profile to one or more types |
| `agent/advisor_ranking_service.py` | Deterministic hard filtering, scoring, tie-breaking, and diversity |
| `kb_storage/prompts/advisor_content/advisor_content_agent_prompt.md` | Profile extraction and grounded catalog-retrieval instructions |
| `kb_storage/prompts/advisor_format/advisor_format_agent_prompt.md` | User-facing TOP-3 and clarification formatting instructions |
| `tests/unit/agent/test_advisor_contract.py` | Contract tests |
| `tests/unit/agent/test_advisor_profile.py` | Profile merge and clarification tests |
| `tests/unit/agent/test_advisor_profile_matcher.py` | Client Types parsing and matching tests |
| `tests/unit/agent/test_advisor_ranking_service.py` | Filtering and scoring tests |
| `tests/unit/agent/test_advisor_agent.py` | Agent factory and prompt tests |

Use `kb_storage/manager/tables/typical_client_profiles_active.xlsx` as the single source of client-type definitions and product-property rules. Python may define parsing, confidence thresholds, and weights, but must not duplicate the workbook's type names or property lists.

### 10.2. Existing files to update

| File | Planned change |
|---|---|
| `agent/agents/dispatcher_agent.py` | Add `advisor` route, `advisor_recommendation` intent/reason, validation, and self-healing mapping |
| `kb_storage/prompts/dispatcher/dispatcher_agent_prompt.md` | Add advisor examples and boundaries versus `product_filter` and `product_info` |
| `agent/rootagent.py` | Register advisor components, state keys, route handler, follow-up resolution, persistence, and error handling |
| `agent/start_agent.py` | Construct and inject advisor content and format agents |
| `agent/config.py` | Add advisor token/temperature settings and `ADVISOR_ENABLED` |
| `agent/agents/__init__.py` | Export advisor factories if this remains the package convention |
| `mcps/kb-manager/app/services/tables_loader_service.py` | Ensure the Client Types workbook loads as `typical_client_profiles` and its Russian business-label row is excluded from data |
| `mcps/kb-manager/app/scripts/load_tables.py` | Treat `typical_client_profiles` as a required advisor table, emit its source/row/column result, and return failure on validation errors in strict mode |
| `load_tables.ps1` | Run the shared loader with `--strict-validation` and preserve its nonzero exit code when the Client Types table is missing or invalid |
| `mcps/kb-manager/app/main.py` | Run the UI load endpoint in strict mode, verify that `typical_client_profiles` exists, and return the normal table list and human-readable log |
| `mcps/kb-manager/app/static/app.js` | Verify the API response includes `typical_client_profiles`, display the human-readable load log, and refresh the table cards |
| `mcps/kb-manager/app/static/index.html` | Clarify that the existing `Загрузить таблицы` action also loads the Client Types table; do not add a competing button |
| `tests/unit/agent/test_dispatcher_agent.py` | Add routing, boundary, and validation cases |
| `tests/unit/agent/test_rootagent.py` | Add advisor routing, state, recalculation, and product-info handoff cases |
| `tests/unit/agent/test_start_agent.py` | Verify advisor construction and dependency injection |
| `tests/unit/mcps/test_tables_loader_service_database_creation.py` | Verify Client Types table name, three data rows, 21 source columns plus `client_type_code`, code generation, and descriptive-row handling |
| KB Manager API/UI tests | Verify strict-mode invocation, Client Types table presence, success rendering, and failure rendering |

Update runtime prompt mounts or deployment configuration only if the current environment explicitly lists prompt files. Verify the effective prompt inside the running container during integration testing.

### 10.3. Shared `load_tables` action contract

The existing regular-table scan already includes files ending in `_active.xlsx`. Stage 2 must make the Client Types workbook an explicit required result of that action rather than relying only on generic discovery.

Required behavior:

1. Resolve the host source as `kb_storage/manager/tables/typical_client_profiles_active.xlsx` and the container source as `/app/data/kb_documents/manager/tables/typical_client_profiles_active.xlsx` through the existing `./kb_storage:/app/data/kb_documents` mount.
2. Confirm the file exists before destructive table replacement starts. If it is missing, fail the action with a clear source path.
3. Load it through `TablesLoaderService._load_regular_tables`; do not create a separate database-import implementation.
4. Normalize the single-sheet filename to PostgreSQL table `typical_client_profiles`.
5. Remove the Russian business-label row, validate the 21 source technical columns, and prepend generated `client_type_code` values in `CT-###` format to the database data.
6. Validate the current source contains the three client rows and no descriptive row. Runtime code must remain data-driven so later approved rows can be added without an application release.
7. Append a dedicated result to the loader output containing:
   - table name;
   - source file and worksheet;
   - loaded row count;
   - column count;
   - human-readable validation result;
8. In strict mode, any missing file, schema mismatch, invalid rule expression, or load failure must produce a nonzero process exit code.
9. Preserve the current behavior for products, the data catalog, glossary, and product-kit diagnostics.

PowerShell behavior:

- `.\load_tables.ps1` must continue to verify that the `kb-manager` Compose service is running;
- append `--strict-validation` to `python -m app.scripts.load_tables`;
- display the Client Types result in normal stdout;
- preserve the loader exit code and existing `Tables loader failed.` error path.

KB Manager API/UI behavior:

- `POST /api/tables/load` must invoke the same module with `--strict-validation`;
- on success, the response must include `typical_client_profiles` in `tables` and the loader's human-readable validation log;
- on failure, return a non-2xx response with the validation error; do not show a generic success message;
- `loadTables()` must show the Client Types row/column result in the load protocol and refresh the table list;
- `renderTables()` must render the `typical_client_profiles` card so an operator can inspect it through the existing table modal;
- the existing UI button remains the single command for loading all tables; its helper text should state that Client Types are included.

Success criterion: running either `.\load_tables.ps1` or the KB Manager `Загрузить таблицы` action produces the same validated `typical_client_profiles` table and a clear human-readable Client Types validation message.

## 11. Detailed Implementation Sequence

### Phase 0. Confirm data and rules

1. Register `typical_client_profiles_active.xlsx` as the authoritative Client Types source.
2. Verify the loader maps the single-sheet workbook to PostgreSQL table `typical_client_profiles`.
3. Correct the ingestion contract so row 2 is treated as Russian business labels, not as a fourth client type. Prefer an explicit table-specific validation in the loader unless the workbook owner approves changing the existing description-row marker.
4. Assert the source has exactly 21 expected technical columns and the loaded table has those columns plus `client_type_code` and the three current profile rows.
5. Parse and validate every property/value expression in `required_properties`, `preferred_properties`, `acceptable_compromises`, and `contraindications` against the product catalog's business names and exact categorical values.
6. Identify the actual product-catalog table and all columns referenced by the Client Types rows.
7. Confirm how active versus archived products are represented.
8. Confirm the product-catalog freshness field and acceptable staleness SLA.
9. Approve and version only the matching/scoring policy as `pilot-v1`; keep the rule values in the workbook.
10. Run all reference cases manually against both source tables and resolve rule/data ambiguity before coding.
11. Add the Client Types required-result validation to `app.scripts.load_tables` so the shared `load_table` action cannot succeed without it.
12. Update `.\load_tables.ps1` to invoke the loader with `--strict-validation`.
13. Update `POST /api/tables/load` to invoke the same strict command, verify that the Client Types table exists, and return the human-readable loader log.
14. Update KB Manager `loadTables()` and the upload-tab copy to display the Client Types result and failure state.
15. Execute both the PowerShell and UI commands and reconcile their row count, column count, and table name.

Exit criterion: both `load_table` entry points create the same validated `typical_client_profiles` table containing only the three current client types, every workbook rule maps to a structured product field/value, and any missing field is documented as a blocking issue.

Implementation status (August 27, 2026): the technical Phase 0 work is implemented. The shared loader now removes the business-label row, validates the exact 21-column source schema and the four product-rule columns against `products_active.xlsx`, adds deterministic `CT-001`-style codes without modifying the workbook, and prints a human-readable validation result through both the PowerShell and KB Manager UI paths. Unit and syntax checks pass. The live PowerShell/UI reconciliation in step 15 remains pending because Docker Desktop was not running during verification. Business approval of `pilot-v1`, the freshness SLA, and the manual reference cases in steps 7-10 also remain explicit Phase 0 gates.

### Phase 1. Implement domain models and deterministic product ranking

1. Add the typed client-profile model and per-field provenance.
2. Implement profile merge, explicit correction, conflict detection, and reset-for-new-client behavior.
3. Reuse the Phase 0 validation of the 21 source-column Client Types schema and implement deterministic runtime parsing only for the four semicolon-separated product-rule columns: `required_properties`, `preferred_properties`, `acceptable_compromises`, and `contraindications`. Do not deterministically parse the descriptive client-profile columns; the LLM uses those columns for semantic client-type selection.
4. Add typed models for the LLM's client-type selection: primary type, optional secondary type, confidence, criterion-level evidence, missing fields, and one clarification question.
5. Implement deterministic validation of the LLM result: selected type names must exist in the loaded table, confidence must be in range, evidence must reference supplied client facts and table fields, and clarification mode must contain exactly one question.
6. Add typed product facts and ranking-result models.
7. Implement hard-filter predicates from `required_properties` and `contraindications` with stable exclusion codes.
8. Implement weighted product scoring from the selected Client Types row's `preferred_properties` and `acceptable_compromises`, with stable tie-breaking, minimum threshold, and diversity selection.
9. Add table-driven unit tests using real workbook rows loaded through a fixture, not hardcoded copies.

Exit criterion: pure Python tests validate the Client Types schema, parse the four product-rule columns, reject invalid LLM selection payloads, and produce the approved exclusions and TOP-3 from a preselected valid client type without a live database.

### Phase 2. Implement advisor agents and contracts

1. Create `advisor_content_agent` with the same refreshing DBHub toolset pattern as the current product content agents.
2. Limit its tool filter to table-discovery tools and `execute_sql`.
3. Instruct it to retrieve `typical_client_profiles`, semantically compare the manager's client description with the loaded rows, and select a primary and optional secondary client type.
4. Require the LLM to return the selected type, confidence, supporting client facts, table-field evidence, missing fields, and exactly one clarification question when confidence is insufficient.
5. When selection confidence is sufficient, retrieve product facts required by the selected row's four product-rule columns; Python must validate the complete selection and evidence before any filtering or ranking occurs.
6. Instruct the agent to return only profile patches, client-type selection evidence, missing fields, clarification data, source metadata, validated client-type rows, and SQL-grounded product facts.
7. Add a strict content contract and verify current-run SQL use for `typical_client_profiles`; candidate mode must also verify current-run SQL use for the products table.
8. Create a no-tool `advisor_format_agent` with temperature `0.0`.
9. Add a final response contract that preserves the validated client-type selection, exact TOP-3 order, and product identities.
10. Add prompt fallback text and prompt-watcher registration, matching current conventions.

Exit criterion: client-type evaluation cases meet the approved accuracy threshold, and contract tests reject unknown type names, unsupported modes, unsupported evidence, invalid confidence, invalid clarification payloads, invented products, incomplete identities, and reordered recommendations.

### Phase 3. Add dispatcher routing

1. Add `advisor` to allowed routes.
2. Add `advisor_recommendation` to allowed intents and reasons.
3. Add semantic validation that binds the intent to the advisor route.
4. Add positive examples:
   - "What should I offer this client?"
   - "Recommend products for a cautious client with a seven-year horizon."
   - "The client wants to save for a child's education."
5. Add negative boundary examples:
   - parameter-only listing remains `product_filter`;
   - comparison by product identity remains `product_filter`;
   - card and kit requests remain `product_info`;
   - general product-rule questions remain `kb_answer`.
6. Add follow-up examples that keep profile corrections and recommendation explanations in `advisor`.

Exit criterion: the dispatcher test matrix passes with no route ambiguity in the agreed examples.

### Phase 4. Integrate `RootAgent` and session state

1. Inject both advisor agents, the client-type selection validator, and `AdvisorRankingService` into `RootAgent`.
2. Register advisor agents in `sub_agents`.
3. Add per-turn advisor keys to `STATE_KEYS_TO_CLEAR`.
4. Add `_handle_advisor` with this order:
   - prepare query and context;
   - run content agent;
   - validate and merge profile;
   - validate the LLM-selected client type against the retrieved Client Types rows;
   - return one clarification question when required;
   - parse the selected row's four product-rule columns;
   - run deterministic product filtering and ranking;
   - run format agent;
   - store final advisor context;
   - set `_root_final_text`.
5. Add `advisor` handling to `_execute_target_agent`.
6. Preserve advisor context and matched types in final event state deltas and cross-session recovery where current product context is preserved.
7. Extend fallback handling so validation or tool-use failures produce a safe retry message and never a partially grounded recommendation.
8. Ensure OWASP checks still run before advisor processing.

Exit criterion: RootAgent tests prove that only a contract-valid, table-grounded LLM selection reaches deterministic product ranking, advisor state survives turns, per-turn intermediate state does not leak, and failures do not emit recommendations.

### Phase 5. Implement follow-ups and product-info handoff

1. Resolve "first," "second," "third," and explicit product names against `top_products`.
2. Keep explanation, comparison, and client-profile correction in the advisor route.
3. Route card/kit verbs to the existing product-info intents.
4. Populate the existing product context with the selected advisor product identity before the handoff.
5. Reject an ordinal outside the current TOP-3 with a concise clarification.
6. Reset ordinal context when a new client or a new recommendation session starts.
7. Test that the existing product-kit `_bot_action` is generated by `product_info`, not by advisor code.

Exit criterion: all agreed multi-turn conversations resolve the intended product and preserve both advisor and product-info behavior.

### Phase 6. Add observability and pilot controls

1. Add `ADVISOR_ENABLED`, defaulting to `false` until the pilot is approved.
2. When disabled, route advisor-like queries to a clear unavailable response or the approved existing fallback; do not silently reinterpret them as parameter filters.
3. Add structured logs for:
   - route and mode;
   - profile completeness, matched client-type names/confidence, and missing-field names, without raw sensitive values;
   - candidate, excluded, and eligible counts;
   - exclusion-code counts;
   - scoring-policy version and product-catalog freshness;
   - selected product codes and score components;
   - clarification, no-data, validation-failure, and tool-failure outcomes;
   - latency by content retrieval, ranking, formatting, and total route.
4. Add stage metrics consistent with the current pipeline metrics implementation.
5. Do not log the full client profile or unrestricted free-form client text in new advisor-specific log fields.

Exit criterion: an operator can reconstruct why a product was selected or excluded from structured, privacy-safe evidence.

### Phase 7. Validate and release the pilot

1. Run focused unit tests during development.
2. Run the complete unit suite with the project test runner.
3. Test DBHub connectivity and queries for both `typical_client_profiles` and the products table in the integration environment.
4. Reconcile the loaded Client Types table to the source workbook: 21 source columns, one generated code column, and exactly three client rows.
5. Verify effective code, environment, and mounted advisor prompts inside the running container.
6. Execute all reference cases and record actual versus expected client type, TOP-3, exclusions, and explanations.
7. Run multi-turn manual scenarios in every supported client channel.
8. Enable the feature only for the approved pilot audience.
9. Monitor failures and recommendation divergence before broader rollout.

Exit criterion: all quality gates in Section 13 pass and business owners sign off on the recorded reference-case results.

## 12. Test Plan

### 12.1. Unit tests

Profile tests:

- create a profile from one message;
- merge additional facts without losing prior values;
- apply an explicit correction;
- detect conflicting values;
- reset for a different client;
- reject invalid ages, terms, amounts, currencies, and enum values.

Client Types schema and parser tests:

- load the 21 expected source columns from the workbook fixture and prepend `client_type_code`;
- generate `CT-001`, `CT-002`, and `CT-003` in source-row order;
- exclude the Russian business-label row from client-type data;
- load `Консервативный`, `Умеренный`, and `Агрессивный` without hardcoding them in production code;
- validate descriptive client columns as source data without implementing semantic matching rules in Python;
- deterministically parse every required, preferred, compromise, and contraindication expression;
- reject an unknown product property or categorical value;
- accept an additional approved workbook row without a code release.

Client-type selection contract tests:

- accept a primary type that exactly matches a row loaded in the current run;
- accept an optional secondary type and distinct evidence for a mixed profile;
- reject a type name not present in the loaded table;
- reject evidence that does not reference supplied client facts and table fields;
- reject confidence outside the allowed range;
- require exactly one clarification question when confidence is below the approved threshold;
- prohibit product retrieval and ranking when the selection contract is invalid.

Shared load-action tests:

- the regular-table scan discovers `typical_client_profiles_active.xlsx` through the mounted tables directory;
- the file maps to table name `typical_client_profiles`;
- the loader log reports the source file, worksheet, row and column counts, and a human-readable confirmation that Client Types codes were generated;
- strict mode fails when the workbook is missing, has an invalid schema, contains an invalid rule expression, or cannot be loaded;
- `load_tables.ps1` passes `--strict-validation` and propagates a nonzero exit code;
- `POST /api/tables/load` passes `--strict-validation` to the subprocess;
- the API success payload includes `typical_client_profiles` in `tables` and the human-readable loader log;
- the API returns non-2xx when Client Types validation fails;
- `loadTables()` renders the explicit Client Types result on success and the validation message on failure;
- `renderTables()` includes a working `typical_client_profiles` inspection card.

Ranking tests:

- each hard rule excludes a product independently;
- required properties from the matched Client Types row are enforced;
- contraindications from the matched row always exclude a matching product;
- preferred properties and acceptable compromises affect only the bounded soft score;
- multiple exclusion reasons are retained;
- an excluded product never enters TOP-3;
- score components sum to the total;
- missing optional data is not treated favorably;
- tied scores use stable tie-breakers;
- diversity replacement obeys threshold and maximum-gap rules;
- fewer than three eligible products are returned without padding;
- focus and KV changes do not change Stage 2 ranking;
- repeated input produces byte-for-byte equivalent structured ranking output.

Contract tests:

- accepted payload for each mode;
- clarification requires one question and missing fields;
- candidate mode requires validated Client Types rows, complete product identities, and current-run SQL for both source tables;
- unknown keys and modes are rejected where the contract requires exact shape;
- final formatter output cannot reorder products;
- no-data and failure payloads cannot masquerade as recommendations.

LLM client-type evaluation cases:

- complete conservative, moderate, and aggressive descriptions select the expected workbook rows;
- paraphrases and reordered facts preserve the expected type;
- mixed profiles return primary and secondary types with evidence rather than an unsupported forced classification;
- insufficient descriptions return one useful clarification question;
- explicit client corrections update the selected type;
- irrelevant or contradictory facts do not produce unsupported evidence;
- repeated runs meet the approved selection-consistency threshold without requiring identical free-form wording.

### 12.2. Dispatcher tests

- personalized recommendation routes to `advisor`;
- parameter-only search routes to `product_filter`;
- product comparison routes to `product_filter`;
- product card and kit route to `product_info`;
- recommendation refinement remains `advisor`;
- "why the second one?" remains `advisor` when TOP-3 context exists;
- "show the second card" routes to `product_info` when TOP-3 context exists;
- ambiguous ordinal without advisor context asks for clarification;
- disabled pilot behavior follows the approved fallback.

### 12.3. Root and state tests

- advisor agents are invoked only for the advisor route;
- client profile persists across turns;
- intermediate content and format results are cleared each turn;
- a clarification turn does not run ranking or formatting unnecessarily;
- a profile change triggers catalog refresh and full recalculation;
- a profile change triggers a new LLM client-type selection before deterministic product recalculation;
- matched client types, TOP-3, and score evidence are stored with Client Types source, scoring-policy, and product-catalog versions;
- ordinal references select the correct identity;
- product-info handoff uses `code`, `name`, and `is_active`;
- a new-client request clears the prior advisor context;
- validation and tool failures produce no recommendation;
- final state delta contains the versioned advisor context.

### 12.4. Integration tests

- running `.\load_tables.ps1` loads and reports `typical_client_profiles`;
- clicking KB Manager `Загрузить таблицы` loads and reports the same table;
- both entry points report the same source file, worksheet, row count, and column count;
- DBHub tools expose `typical_client_profiles`, the products table, and their required columns;
- the loaded Client Types table contains 21 source columns plus `client_type_code` and exactly three valid client types;
- the workbook description row is not available as a client-type data row;
- the advisor content agent uses read-only SQL;
- every matched client-type fact and property rule matches the current SQL result sourced from the workbook;
- every selected client type exists in the current SQL result and its evidence is limited to supplied client facts and loaded table fields;
- every displayed product fact matches the current SQL result;
- archived products are excluded unless the approved pilot rules explicitly allow them;
- catalog freshness is captured correctly;
- prompt hot reload or deployment mounts include the new prompts;
- container configuration exposes the feature flag and advisor settings.

### 12.5. Manual end-to-end scenarios

At minimum, test:

1. Complete conservative client profile produces a TOP-3.
2. Complete moderate and aggressive profiles match their corresponding workbook rows and produce a TOP-3.
3. Incomplete profile produces one clarification question that distinguishes the leading client types.
4. Answering the question produces a TOP-3 without asking for known data again.
5. Changing risk tolerance recalculates the client type and result.
6. A hard age restriction excludes an otherwise high-scoring product.
7. Only two products pass, and the response returns two honestly.
8. No products pass, and the response explains which constraint blocked selection.
9. "Why is the second option lower?" uses stored evidence.
10. "Compare the first and second" compares the current recommendations.
11. "Show the card for the second" opens the correct product through `product_info`.
12. "Send the kit for the first" triggers the existing document-kit flow.
13. A new-client request does not reuse the previous profile.
14. A DBHub failure produces a safe error and no recommendation.
15. A stale product catalog produces the approved warning or blocks recommendation according to the SLA.
16. Removing or corrupting the Client Types workbook makes both PowerShell and UI load actions fail visibly without a false success message.

## 13. Quality Gates and Acceptance Criteria

### Functional

- All approved reference cases satisfy hard exclusions exactly.
- Every reference case uses the matching row from `typical_client_profiles`; no prompt or Python copy of a client type is used.
- LLM client-type selection meets the business-approved accuracy and consistency thresholds on the reference set; every mismatch is reviewed before pilot release.
- TOP-3 order matches the approved deterministic scoring policy for at least 95% of reference cases; every mismatch is reviewed and resolved before pilot release.
- The flow asks no more than one clarification question per turn.
- Profile corrections trigger recalculation.
- Product-card and kit handoffs resolve the intended recommended product.
- Existing `product_filter`, `product_info`, `doc_search`, `kb_answer`, and `smalltalk` tests remain green.
- Both shared `load_table` commands load `typical_client_profiles` and report the validated result.

### Grounding and safety

- Every displayed product fact is traceable to current-run structured data.
- Every reported client-type match and product-property rule is traceable to the current loaded version of `typical_client_profiles_active.xlsx`.
- No product that violates a hard rule is recommended.
- Focus and KV have no measurable effect on Stage 2 results.
- Tool, contract, or freshness failures never return a normal-looking TOP-3.
- Logs do not add raw sensitive client-profile fields.

### Engineering

- New domain logic has table-driven unit coverage.
- Contracts reject malformed and unsupported output.
- Client-type selection is LLM-driven, table-grounded, evidence-backed, and contract-validated.
- Product-rule parsing, hard filtering, and scoring are deterministic and independent of prompts.
- Client-type definitions and property rules are data-driven from the workbook and are not duplicated in code.
- Configuration has safe defaults and is documented.
- PowerShell and UI use the same strict loader module; there is no duplicated advisor-specific import path.
- Modified UTF-8 files contain no Cyrillic corruption.
- `git diff --check` passes.
- The full unit suite passes through `.\tests\run-unit-tests.ps1`, or focused tests use `.\venv\Scripts\python.exe -m pytest -p no:cacheprovider -s ...`.

### Operational

- Feature flag is off by default before approval.
- Metrics distinguish clarification, recommendation, no-data, and failure outcomes.
- The deployed container has the intended code, environment variables, and prompt versions.
- The KB Manager load log and table cards expose the validated `typical_client_profiles` result.
- A rollback consists of disabling `ADVISOR_ENABLED`; existing product routes remain available.

## 14. Pilot Rollout

1. Deploy with `ADVISOR_ENABLED=false` and verify startup plus agent registration.
2. Enable in the integration environment and run automated and reference-case checks.
3. Conduct a business review of explanations, exclusions, and TOP-3 diversity.
4. Enable for a small named pilot group.
5. Review daily during the initial pilot:
   - routing precision;
   - client-type match distribution and low-confidence rate;
   - clarification rate;
   - no-result rate;
   - hard-exclusion distribution;
   - reference-case divergence;
   - tool and contract failures;
   - product-card/kit handoff success;
   - catalog freshness.
6. Stop or disable the pilot immediately if an ineligible product is recommended, facts are ungrounded, or the catalog exceeds the approved freshness limit.
7. Expand only after business and engineering owners approve the pilot evidence.

## 15. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Required suitability fields are absent from the catalog | Treat as a Phase 0 blocker; do not infer them |
| Client Types business-label row is loaded as a profile | Add loader validation and assert exactly the expected data rows before enabling the pilot |
| Workbook property text does not map to a product field/value | Fail Phase 0 validation and require a business-owned workbook correction; do not reinterpret it in a prompt |
| LLM selects a client type inconsistently | Require table-grounded evidence, confidence thresholds, evaluation cases, and one clarification question when confidence is insufficient |
| Client matches multiple workbook rows | Let the LLM retain primary/secondary matches with evidence and ask one differentiating question instead of forcing a single type |
| Client Types workbook changes without a code release | Validate schema and property vocabulary at load time and fail closed on incompatible changes |
| PowerShell and UI load commands diverge | Keep both entry points on `app.scripts.load_tables --strict-validation` and assert response parity in integration tests |
| UI reports success after Client Types failure | Make the API return non-2xx and require `loadTables()` to render the validation error |
| LLM invents or transforms product facts | Require current-run SQL, validate structured output, and score only verified attributes |
| Prompt-based product decisions are inconsistent | Keep product-rule parsing, hard exclusions, and ranking arithmetic in deterministic Python code |
| Dispatcher confuses recommendation with filtering | Add explicit boundaries and regression examples |
| Prior client data leaks into a new recommendation | Implement explicit new-client reset and state tests |
| Ordinal references resolve to the wrong product | Resolve only against versioned stored TOP-3 identities and clarify invalid ranks |
| Catalog changes between turns | Refresh before recalculation and store `catalog_as_of` |
| TOP-3 contains near-duplicates | Apply a deterministic, business-approved diversity pass |
| Commercial priorities influence the pilot unintentionally | Exclude focus and KV from score inputs and add invariance tests |
| Prompt and image versions differ in deployment | Verify effective prompt mounts and code inside the runtime container |
| Client information appears in logs | Log field names and aggregate outcomes, not raw values |

## 16. Deliverables

1. Approved `typical_client_profiles_active.xlsx` Client Types source and reference-case fixture.
2. Loader validation for `typical_client_profiles` and its descriptive row.
3. Shared PowerShell and KB Manager UI load-action integration with strict validation and human-readable status.
4. Advisor profile, LLM client-type selection validator, product-rule parser, and ranking domain modules.
5. Advisor content and format agents with strict contracts.
6. Dispatcher and RootAgent integration.
7. Persistent advisor context and product-info handoff.
8. Feature flag, configuration, logs, and metrics.
9. Unit and integration test coverage.
10. Recorded reference-case results and business sign-off.
11. Pilot runbook with enable, disable, verification, and incident steps.

## 17. Definition of Done

Stage 2 is complete only when:

- the Client Types workbook and all remaining Stage 1 inputs are approved and versioned;
- the runtime `typical_client_profiles` table is reconciled to the workbook and contains only valid client rows;
- `.\load_tables.ps1` and KB Manager `Загрузить таблицы` both load and report that table through the shared strict loader;
- LLM client-type selection is grounded in the loaded table, contract-validated, and passes the approved evaluation set;
- the advisor route is implemented behind a feature flag;
- product-rule parsing, hard filtering, and scoring are deterministic and fully tested;
- the advisor produces grounded, explainable TOP-3 results for approved cases;
- clarification and multi-turn profile updates work;
- product-card and kit handoffs work through `product_info`;
- all quality gates pass in the deployed pilot environment;
- business and engineering owners approve the reference-case report;
- rollback has been tested by disabling the feature flag.
