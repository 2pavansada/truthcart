# API_SPEC.md — HTTP API

> Design specification. Nothing is implemented. **All product titles, IDs, prices and policy text in examples are ILLUSTRATIVE placeholders, not catalogue data.** Product ID format and currency handling are unconfirmed (UNK-02, UNK-05); IDs are assumed to match a configurable `product_id_regex` that must **not** allow the `:` character (evidence IDs use `:` as a separator).

## 1. Conventions

- Base path `/api`. JSON in and out, UTF-8. `Content-Type: application/json`.
- All request bodies reject unknown fields (`extra="forbid"`).
- Every response carries header `X-Request-Id` (also in error bodies).
- Money is returned both as integer minor units and a display string; clients must display `price_display` and never compute prices.
- Chat never returns 5xx for model or validation failures (it falls back, see `architecture.md` §6). 5xx only for `INDEX_NOT_READY` or unhandled bugs.

## 2. Endpoint summary

| Method | Path | Purpose | Auth |
|---|---|---|---|
| POST | `/api/chat` | One conversational turn | none |
| POST | `/api/session/reset` | Clear a session's constraints and context | none |
| GET | `/api/products/{product_id}` | Full product record (evidence drawer, direct lookup) | none |
| GET | `/api/products?ids=P1,P2` | Batch lookup (max 20) | none |
| GET | `/api/policy/sections` | List policy sections (ID, heading) | none |
| GET | `/api/policy/sections/{section_id}` | Section text (evidence drawer) | none |
| GET | `/api/catalogue/meta` | Catalogue and index metadata | none |
| GET | `/api/health` | Liveness | none |
| GET | `/api/ready` | Readiness and degradation flags | none |
| POST | `/api/eval/run` | Run an evaluation suite (flag-gated) | none (local only) |
| GET | `/api/eval/runs/{run_id}` | Evaluation run status/result | none |

No authentication: the app is local and offline. Eval routes are disabled unless `app.yaml: eval.enabled: true`, and then respond only to `127.0.0.1`.

## 3. Error envelope and codes

```json
{ "error": { "code": "VALIDATION_ERROR", "message": "message must be 1-500 characters", "details": [{"field": "message", "issue": "too_long"}], "request_id": "r_8f3a1c" } }
```

| HTTP | code | When |
|---|---|---|
| 422 | `VALIDATION_ERROR` | Schema violation, unknown field, bad `session_id`, empty message without ops/resolution, `ids` > 20 |
| 400 | `INVALID_CONSTRAINT_OP` | `constraint_ops` has an unsupported key/value combination |
| 400 | `INVALID_RESOLUTION` | `resolution.clarification_id` is unknown/expired, or `option_id` not in that clarification |
| 404 | `PRODUCT_NOT_FOUND` | Unknown `product_id` in product endpoints |
| 404 | `POLICY_SECTION_NOT_FOUND` | Unknown section |
| 404 | `EVAL_RUN_NOT_FOUND` | Unknown run |
| 403 | `EVAL_DISABLED` | Eval endpoints used while disabled |
| 409 | `EVAL_RUN_IN_PROGRESS` | Another run is active |
| 503 | `INDEX_NOT_READY` | DB/manifest/policy missing or manifest mismatch |
| 500 | `INTERNAL_ERROR` | Unhandled exception (details omitted, `request_id` logged) |

Note: "product not found" **inside a chat turn** is a normal `REFUSE` decision, not an HTTP error.

## 4. `POST /api/chat`

### 4.1 Request

```json
{
  "session_id": "sess_9fK2xQp7aLm3",
  "message": "Under ₹2,000",
  "client_context": { "focus_product_id": null },
  "constraint_ops": [],
  "resolution": null,
  "options": { "max_results": 5, "generation_mode": "AUTO" }
}
```

| Field | Type | Rules |
|---|---|---|
| `session_id` | string \| null | Optional. Regex `^[A-Za-z0-9_-]{8,64}$`. If null/absent the server creates one and returns it. |
| `message` | string | Trimmed, NFKC, control characters stripped, length 0–500. Empty only if `constraint_ops` or `resolution` is non-empty. |
| `client_context.focus_product_id` | string \| null | Set when the user clicked "ask about this product" on a card. Must match `product_id_regex`; unknown IDs are ignored with a warning. |
| `constraint_ops` | array (≤10) | Structured edits (from constraint chips / suggestion buttons). See 4.2. |
| `resolution` | object \| null | `{ "clarification_id": "...", "option_id": "..." }` answering a pending clarification. |
| `options.max_results` | int 1–10 | Default 5 (`decision_thresholds.yaml: top_k_display`). |
| `options.generation_mode` | `"AUTO"` \| `"TEMPLATE"` | `AUTO` uses the LLM rephraser if enabled and healthy; `TEMPLATE` forces the template path (used by tests/eval). |

### 4.2 `ConstraintOpIn`

```json
{ "op": "REMOVE", "key": "price_max" }
{ "op": "SET", "key": "price_max", "value": { "amount_minor": 200000, "inclusive": false } }
{ "op": "SET", "key": "stock", "value": "IN_STOCK" }
{ "op": "ADD", "key": "category", "value": "Shirts" }
{ "op": "ADD", "key": "attribute", "value": { "key": "material", "value": "cotton" } }
{ "op": "SET", "key": "sort", "value": { "field": "price", "direction": "asc" } }
{ "op": "RESET", "key": "all" }
```

| `op` | Meaning | Allowed keys |
|---|---|---|
| `SET` | Replace the value for that key (single-valued keys) | `price_min`, `price_max`, `stock`, `rating_min`, `sort` |
| `ADD` | Add to a multi-valued key | `category` (OR set), `attribute` (AND list), `text_term` |
| `REMOVE` | Remove key (or one value for multi-valued keys: pass `value`) | all except `all` |
| `RESET` | Clear everything | `all` only |

`category` values must be canonical category strings from `GET /api/catalogue/meta`; otherwise `INVALID_CONSTRAINT_OP`.

### 4.3 Response (`ChatResponse`)

```json
{
  "session_id": "sess_9fK2xQp7aLm3",
  "turn_id": 3,
  "decision": "ANSWER",
  "answer_text": "…",
  "claims": [ { "claim_id": "c1", "type": "PRICE", "sentence": "…", "product_id": "P102", "value": {"price_minor": 129900, "currency": "INR"}, "scope": "PRODUCT", "evidence_ids": ["prod:P102:price", "prod:P102:title"] } ],
  "evidence": [ { "evidence_id": "prod:P102:price", "kind": "PRODUCT_FIELD", "product_id": "P102", "field": "price", "value": 129900, "display_value": "₹1,299.00" } ],
  "products": [ { "product_id": "P102", "title": "…", "price_minor": 129900, "currency": "INR", "price_display": "₹1,299.00", "stock_status": "IN_STOCK", "stock_label": "In stock", "rating": 4.2, "rating_count": null, "category": "…", "match_basis": "EXACT_CONSTRAINTS", "evidence_ids": ["prod:P102:price"] } ],
  "clarification": null,
  "refusal": null,
  "active_constraints": [ { "key": "price_max", "label": "Price under ₹2,000", "op_to_remove": { "op": "REMOVE", "key": "price_max" } } ],
  "suggestions": [],
  "generation": { "mode": "TEMPLATE", "fallback_reason": null },
  "warnings": [],
  "metrics": { "request_id": "r_8f3a1c", "total_ms": 0.0, "stages": {}, "tokens": { "llm_input": null, "llm_output": null, "source": "unavailable" }, "validator_trips": 0, "fallback": null }
}
```

| Field | Notes |
|---|---|
| `decision` | `ANSWER` \| `CLARIFY` \| `REFUSE` |
| `answer_text` | Deterministic assembly of claim sentences + non-factual templates + inline citation markers (§4.5) |
| `claims` | Typed claims (`claims` table below). Empty only for non-factual answers (greeting/help/reset), CLARIFY, and REFUSE with no catalogue claims |
| `evidence` | Every `Evidence` referenced by claims, cards and clarification options. Each is retrievable again from the product/policy endpoints (except search manifests) |
| `products` | Display cards built **directly from DB rows**, not from model text. Every card must satisfy the active constraints (validator V-10) |
| `clarification` | Present iff `decision == CLARIFY` |
| `refusal` | Present iff `decision == REFUSE` |
| `active_constraints` | State after this turn; each chip includes the op that removes it |
| `suggestions` | Relaxation hints, each `{ "label", "constraint_ops": [...], "evidence_ids": ["search:s_3"] }` |
| `generation.mode` | `TEMPLATE` (forced or no LLM), `LLM` (validated rephrase), `TEMPLATE_FALLBACK` (LLM output rejected) |
| `warnings` | Subset of: `SEMANTIC_UNAVAILABLE`, `LLM_UNAVAILABLE`, `LLM_FALLBACK`, `UNKNOWN_STOCK_EXCLUDED`, `SPELLING_CORRECTED`, `CODEMIX_NORMALIZED`, `CONSTRAINTS_CARRIED_OVER`, `RESULTS_TRUNCATED`, `FOCUS_PRODUCT_UNKNOWN` |
| `metrics.stages` | ms per stage: `normalize, extract, merge, retrieve_lexical, retrieve_semantic, filter, decide, evidence, generate_llm, generate_template, validate` (absent if not run) |
| `metrics.tokens.source` | `measured` \| `estimated` \| `unavailable` — never blended |

### 4.4 Claim types, values and required evidence

| `type` | `value` shape | Required evidence kinds |
|---|---|---|
| `PRODUCT_EXISTS` | `{}` | `PRODUCT_FIELD:title` of that product |
| `TITLE` | `{ "title": str }` | `PRODUCT_FIELD:title` |
| `PRICE` | `{ "price_minor": int, "currency": str }` | `PRODUCT_FIELD:price` of the same product |
| `STOCK` | `{ "stock_status": "IN_STOCK"\|"OUT_OF_STOCK" }` | `PRODUCT_FIELD:stock` |
| `RATING` | `{ "rating": float, "rating_count": int\|null }` | `PRODUCT_FIELD:rating` |
| `CATEGORY` | `{ "category": str }` | `PRODUCT_FIELD:category` |
| `ATTRIBUTE` | `{ "key": str\|null, "value": str, "basis": "STRUCTURED"\|"DESCRIPTION_TEXT" }` | `PRODUCT_FIELD:attr.<key>` (STRUCTURED) or `PRODUCT_FIELD:description` (DESCRIPTION_TEXT) |
| `DESCRIPTION_QUOTE` | `{ "quote": str }` (verbatim substring) | `PRODUCT_FIELD:description` |
| `FIELD_UNAVAILABLE` | `{ "field": str }` | `PRODUCT_FIELD:title` (product exists) |
| `RESULT_COUNT` | `{ "count": int }` | `SEARCH_MANIFEST` |
| `ABSENCE` | `{ "scope": "supplied_catalogue", "searched": str }` | `SEARCH_MANIFEST` with `verified_count == 0` |
| `POLICY_QUOTE` | `{ "quote": str }` (verbatim substring) | `POLICY_SECTION` |
| `POLICY_PARAPHRASE` *(Tier 2 only)* | `{ "numbers": [..] }` | `POLICY_SECTION` (numbers/durations must appear in section) |

`scope`: `PRODUCT` (about one product), `CATALOGUE` (about the search/catalogue), `GENERAL_POLICY` (store policy, never about a specific product).

### 4.5 Evidence IDs and citation markers

| Evidence | ID grammar | Inline marker in `answer_text` | UI chip |
|---|---|---|---|
| Product field | `prod:<product_id>:<field>` where field ∈ `title, price, stock, rating, category, description, attr.<key_norm>` | `[P102]` (deduplicated per sentence) | product ID |
| Policy section | `pol:<section_id>` | `[policy:2.1]` | "Policy 2.1 · heading" |
| Search manifest | `search:<search_id>` | `[search:s_3]` | "Catalogue search" |

Marker formats are configurable (`config/app.yaml: citation_format`) because the official evaluator may expect a specific form (UNK-10). Negative claims (ABSENCE) have no product ID; the search manifest is their traceable evidence. **Confirm with the official format on Day 1.**

### 4.6 `Evidence` object

```json
{
  "evidence_id": "pol:2.1", "kind": "POLICY_SECTION",
  "section_id": "2.1", "heading": "Return window",
  "excerpt": "<verbatim text of the section or the cited span>"
}
```
```json
{
  "evidence_id": "search:s_3", "kind": "SEARCH_MANIFEST",
  "search": {
    "search_id": "s_3", "scope": "supplied_catalogue",
    "intent": "SEARCH", "query_text": "cotton clothes",
    "constraints": [ {"key":"attribute","label":"cotton"}, {"key":"price_max","label":"Price under ₹2,000"}, {"key":"stock","label":"In stock only"} ],
    "eligible_count": 412, "verified_count": 37, "returned_count": 5,
    "excluded_unknown_stock": 6, "lexical_available": true, "semantic_available": true
  }
}
```

### 4.7 `ClarificationBlock` and `RefusalBlock`

```json
{ "clarification_id": "q_41", "reason": "AMBIGUOUS_PRODUCT",
  "question": "Which product do you mean?",
  "options": [ { "option_id": "o1", "label": "<title> — ₹<price> (P201)", "product_id": "P201", "evidence_ids": ["prod:P201:title","prod:P201:price"] },
               { "option_id": "o2", "label": "…", "product_id": "P207", "evidence_ids": ["…"] } ] }
```
`reason` ∈ `AMBIGUOUS_PRODUCT, UNRESOLVED_REFERENCE, UNDER_SPECIFIED_QUERY, CONTRADICTORY_CONSTRAINTS, AMBIGUOUS_POLICY_TOPIC`. Options may carry `product_id` **or** `constraint_ops` (for contradictions) **or** `policy_topic`. `UNDER_SPECIFIED_QUERY` may have zero options (open question). Options count ≤ `clarify_max_options` (default 5).

```json
{ "reason": "NO_CATALOGUE_MATCH", "detail": "No matching product is listed in the supplied catalogue." }
```
`reason` ∈ `NO_CATALOGUE_MATCH, FIELD_MISSING, POLICY_NOT_ESTABLISHED, OUT_OF_SCOPE, UNSUPPORTED_REQUEST, INSUFFICIENT_EVIDENCE, INTERNAL_VALIDATION_FAILURE`.

### 4.8 Validation order (server side)

1. Pydantic schema (types, extra fields, lengths).
2. `session_id` pattern; create if absent.
3. Message sanitation (NFKC, control chars). If empty after sanitation and no ops/resolution → `VALIDATION_ERROR`.
4. `constraint_ops` semantic validation (key/value pair, canonical category) → `INVALID_CONSTRAINT_OP`.
5. `resolution` must match the session's pending clarification → `INVALID_RESOLUTION`.
6. Readiness check → `INDEX_NOT_READY`.

If both `resolution` and `message` are sent, the resolution is applied first, then the message is processed as an additional turn input.

## 5. Other endpoints

### 5.1 `POST /api/session/reset`
Request `{ "session_id": "…" }` → `200 { "session_id": "…", "reset": true }`. Unknown session → still `200` (idempotent).

### 5.2 `GET /api/products/{product_id}` → `ProductDetail`
```json
{ "product_id": "P102", "title": "…", "description": "…", "category": "…",
  "price_minor": 129900, "currency": "INR", "price_display": "₹1,299.00",
  "rating": 4.2, "rating_count": null,
  "stock_status": "IN_STOCK", "stock_label": "In stock", "stock_quantity": null,
  "attributes": [ { "key": "material", "value": "cotton", "raw_key": "Material", "raw_value": "Cotton" } ],
  "unavailable_fields": ["rating_count"],
  "flags": { "suspicious_text": false } }
```
Fields that are missing or failed validation appear as `null` and in `unavailable_fields`. Never defaulted.

`GET /api/products?ids=P1,P2` → `{ "products": [ProductDetail…], "not_found": ["P9"] }` (200 even if some are not found).

### 5.3 `GET /api/policy/sections` → `{ "sections": [ { "section_id": "2.1", "ordinal": 7, "heading": "…", "level": 2, "parent_id": "2" } ] }`
### `GET /api/policy/sections/{section_id}` → `{ "section_id", "heading", "body", "parent_id" }`

### 5.4 `GET /api/catalogue/meta`
```json
{ "product_count": 0, "quarantined_count": 0,
  "field_issue_counts": { "price_missing": 0, "rating_out_of_range": 0 },
  "categories": [ { "name": "…", "count": 0 } ],
  "price_range": { "min_minor": null, "max_minor": null, "currency": null },
  "stock_distribution": { "IN_STOCK": 0, "OUT_OF_STOCK": 0, "UNKNOWN": 0 },
  "policy_section_count": 0,
  "built_at": "…", "manifest_hash": "…",
  "models": { "embedder": null, "embedding_dim": null, "llm": null },
  "semantic_available": false, "lexical_backend": "FTS5|PY_BM25" }
```
Values above are placeholders; real values come from the build.

### 5.5 Health
`GET /api/health` → `200 {"status":"ok"}`.
`GET /api/ready` → `200|503`
```json
{ "ready": true,
  "checks": { "database": true, "manifest": true, "policy": true, "fts5": true, "vectors": true, "embedder": true, "llm": false },
  "degraded": ["LLM_UNAVAILABLE"] }
```
`ready=true` requires `database`, `manifest`, `policy`. Other failures appear in `degraded`.

### 5.6 Evaluation
`POST /api/eval/run`
```json
{ "suite": "dev", "mode": "template", "limit": null }
```
`suite` ∈ `dev | official`; `mode` ∈ `template | llm`. → `202 { "run_id": "e_20260101_1" }`. `GET /api/eval/runs/{run_id}` → `{ "run_id", "status": "running|done|failed", "progress": {"done": 10, "total": 60}, "summary": { …metrics from EVALUATION_PLAN §5… } | null, "report_path": "data/reports/…" | null }`. Running through the CLI (`scripts/run_eval.py`) is the primary path; the HTTP route exists for demos.

## 6. Examples (illustrative data)

### 6.1 Exact price, product exists → ANSWER
Request: `{"session_id":"sess_demo0001","message":"What is the price of P102?"}`
```json
{ "decision": "ANSWER",
  "answer_text": "The price of “Example Cotton Shirt” (P102) is ₹1,299.00. [P102]",
  "claims": [ { "claim_id":"c1","type":"PRICE","sentence":"The price of “Example Cotton Shirt” (P102) is ₹1,299.00.","product_id":"P102","value":{"price_minor":129900,"currency":"INR"},"scope":"PRODUCT","evidence_ids":["prod:P102:price","prod:P102:title"] } ],
  "clarification": null, "refusal": null }
```
If `price` were missing: `decision: "REFUSE"`, `refusal.reason: "FIELD_MISSING"`, claims: `PRODUCT_EXISTS` + `FIELD_UNAVAILABLE{field:"price"}`; answer: “P102 (“Example Cotton Shirt”) is listed, but its price is not available in the catalogue. [P102]”.

### 6.2 Ambiguous product → CLARIFY
Request: `"What is the price of the blue running shoe?"` (three equally strong lexical matches)
```json
{ "decision": "CLARIFY", "claims": [],
  "answer_text": "More than one product matches “blue running shoe”. Which one do you mean?",
  "clarification": { "clarification_id":"q_41","reason":"AMBIGUOUS_PRODUCT","question":"Which product do you mean?",
    "options":[ {"option_id":"o1","label":"Example Blue Runner — ₹2,499.00 (P201)","product_id":"P201","evidence_ids":["prod:P201:title","prod:P201:price"]},
                {"option_id":"o2","label":"Example Blue Trail Shoe — ₹3,199.00 (P207)","product_id":"P207","evidence_ids":["prod:P207:title","prod:P207:price"]} ] } }
```
No price is asserted in `answer_text`; prices appear only in cited option labels.

### 6.3 Not in catalogue → REFUSE (scoped)
Request: `"Do you sell iPhones?"`
```json
{ "decision": "REFUSE",
  "answer_text": "No product matching “iPhones” is listed in the supplied catalogue. [search:s_1]",
  "claims": [ { "claim_id":"c1","type":"ABSENCE","sentence":"No product matching “iPhones” is listed in the supplied catalogue.","product_id":null,"value":{"scope":"supplied_catalogue","searched":"iPhones"},"scope":"CATALOGUE","evidence_ids":["search:s_1"] } ],
  "refusal": { "reason":"NO_CATALOGUE_MATCH","detail":"Nothing in the supplied catalogue matches this request." } }
```
The system makes no statement about the wider retailer's inventory.

### 6.4 Multi-turn constraint preservation
Turn 1 `"Show cotton clothes."` → constraints `{attribute:cotton, category:<resolved clothing categories>}`.
Turn 2 `"Under ₹2,000."` → adds `price_max < 200000`; response `active_constraints` lists cotton, clothing categories, price < ₹2,000; warning `CONSTRAINTS_CARRIED_OVER`.
Turn 3 `"Only in stock."` → adds `stock=IN_STOCK`; the `SEARCH_MANIFEST` of turn 3 lists all three constraints; `RESULT_COUNT` claim states the verified count; every card passes V-10.

### 6.5 Policy question → ANSWER with verbatim quote
Request: `"What is your return window?"` → claim `POLICY_QUOTE` with sentence `The policy states: “<verbatim sentence>”` and evidence `pol:<section_id>`; marker `[policy:<section_id>]`. If no section establishes it → `REFUSE`, `POLICY_NOT_ESTABLISHED`.

### 6.6 Malformed or abusive input
`{"message": ""}` → `422 VALIDATION_ERROR`. `{"message": "x"*501}` → `422`. `{"message":"hi","extra":1}` → `422`. Product description containing instructions never changes behaviour (see `GROUNDING_AND_REFUSAL.md` §9).

## 7. Acceptance criteria for the API

| ID | Criterion |
|---|---|
| API-AC-01 | Every response validates against its pydantic model; contract tests cover each example above |
| API-AC-02 | `answer_text` equals the deterministic render of `claims` + non-factual templates |
| API-AC-03 | No response contains a currency amount absent from its `evidence` (also checked by the independent auditor) |
| API-AC-04 | Chat returns 200 with a valid decision when the LLM is absent, slow, or returns garbage |
| API-AC-05 | `INDEX_NOT_READY` is returned when the manifest hash mismatches |
| API-AC-06 | Invalid input never reaches retrieval; each 4xx code in §3 has a test |
| API-AC-07 | OpenAPI schema generated by FastAPI matches this document (reviewed on Day 2) |
