# folder_structure.md — Repository layout, module contracts, frontend specification

> Design only. Interfaces are *specifications* to be implemented; none exists yet. Signatures use Python typing notation. Domain types (`ParsedQuery`, `ConstraintSet`, `SearchSpec`, `SearchResult`, `DecisionPlan`, `EvidenceBundle`, `Claim`, …) are defined in `RAG_PIPELINE.md` §3 and `API_SPEC.md` §5; wire schemas in `API_SPEC.md`.

## 1. Tree

```
truthcart/
├── README.md
├── docs/
│   ├── architecture.md  folder_structure.md  API_SPEC.md  DATABASE_SCHEMA.md
│   ├── RAG_PIPELINE.md  GROUNDING_AND_REFUSAL.md  EVALUATION_PLAN.md
│   ├── TESTING_PLAN.md  IMPLEMENTATION_PLAN.md
│   ├── DECISIONS.md                  # created Day 1; any deviation from these docs is logged here
│   ├── DAY1_FINDINGS.md              # created Day 1; answers to UNK-01..UNK-16
│   └── OFFICIAL_EVALUATOR_NOTES.md   # created Day 1; real evaluate.py contract
├── config/
│   ├── app.yaml                      # paths, limits, flags, logging, CORS
│   ├── catalogue_mapping.yaml        # source field -> canonical field, value maps (Day 1)
│   ├── attribute_synonyms.yaml       # team-curated, only listed synonyms are applied
│   ├── category_aliases.yaml         # "clothes" -> [canonical categories] (team-curated from data)
│   ├── decision_thresholds.yaml      # tau_cov, tau_sem, rrf_k, top_k, pool sizes
│   ├── models.yaml                   # model paths/ids, runtime, prefixes, generation limits
│   └── lexicons/
│       ├── codemix_terms.yaml        # token-level code-mix -> English map (illustrative seeds only)
│       ├── intent_cues.yaml          # cue phrases for intents, price/stock/rating phrasing
│       ├── out_of_scope.yaml         # out-of-scope and unsupported-action patterns
│       └── stopwords.txt
├── data/
│   ├── raw/                          # hackathon inputs. READ-ONLY. Never edited by code.
│   └── build/                        # generated: truthcart.sqlite, vectors.npy, vector_ids.json,
│                                     #            build_manifest.json, quarantine.jsonl, build_report.json
├── backend/
│   ├── pyproject.toml  requirements.txt  requirements-dev.txt
│   ├── app/
│   │   ├── main.py  settings.py  container.py  orchestrator.py
│   │   ├── api/        deps.py errors.py routes_chat.py routes_products.py routes_policy.py
│   │   │               routes_meta.py routes_health.py routes_eval.py
│   │   ├── schemas/    common.py chat.py product.py policy.py evidence.py claims.py eval.py
│   │   ├── domain/     enums.py constraints.py parsed_query.py plans.py
│   │   ├── ingest/     source_reader.py catalogue_loader.py field_parsers.py catalogue_validator.py
│   │   │               dedupe.py policy_parser.py sanitizer.py build_pipeline.py manifest.py
│   │   ├── store/      db.py product_repo.py policy_repo.py vector_index.py
│   │   ├── nlp/        normalizer.py codemix.py spellfix.py price_parser.py vocab.py intent.py
│   │   │               constraint_extractor.py reference_resolver.py llm_extractor.py(Tier 3)
│   │   ├── conversation/ state.py merger.py store.py clarification.py
│   │   ├── retrieval/  lexical.py semantic.py fusion.py filters.py attribute_verifier.py
│   │   │               hybrid.py ambiguity.py relaxation.py policy_retriever.py
│   │   ├── decision/   engine.py rules.py claim_planner.py
│   │   ├── generation/ evidence_builder.py templates.py template_generator.py llm_generator.py
│   │   │               prompt.py output_parser.py citation.py card_builder.py
│   │   ├── validation/ validator.py rules.py scanners.py report.py
│   │   ├── models/     base.py mock.py registry.py  embedder_<runtime>.py  llm_<runtime>.py
│   │   └── metrics/    timing.py tokens.py recorder.py memory.py
│   ├── scripts/        inspect_inputs.py smoke_models.py build_index.py gen_dev_set.py
│   │                   run_eval.py benchmark.py check_offline.py
│   ├── eval/           test_set_adapter.py official_bridge.py auditor.py metrics.py
│   │                   provisional_score.py runner.py report.py dev_set/ report_templates/
│   └── tests/          unit/ integration/ e2e/ fixtures/ conftest.py
└── frontend/
    ├── package.json  vite.config.ts  tsconfig.json  index.html
    └── src/            main.tsx App.tsx api/ state/ hooks/ components/ styles/ test/
```

Files tagged *(Tier 3)* are optional. `embedder_<runtime>.py` and `llm_<runtime>.py` are created **only for the runtime confirmed in UNK-08/UNK-09**.

## 2. Backend file contracts

### 2.1 Entry, wiring, orchestration

| File | Responsibility | Public interface |
|---|---|---|
| `app/main.py` | Build FastAPI app, mount routers, mount `frontend/dist` if present, CORS, startup readiness check | `create_app(settings: Settings | None = None) -> FastAPI` |
| `app/settings.py` | Load/validate YAML config into frozen models | `load_settings(config_dir: Path) -> Settings`; `Settings` (frozen pydantic) |
| `app/container.py` | Construct and hold singletons (db, repos, indexes, adapters, session store) | `build_container(settings) -> Container`; `Container.ready() -> ReadinessReport` |
| `app/orchestrator.py` | Run the 11-step pipeline, own fallbacks and timing | `Orchestrator(container).handle(req: ChatRequest) -> ChatResponse` (**never raises for model/validation failures**) |

### 2.2 API (`app/api/`)

| File | Responsibility | Interface |
|---|---|---|
| `deps.py` | FastAPI dependencies (container, request_id) | `get_container()`, `get_request_id()` |
| `errors.py` | Exception → error envelope mapping | `register_error_handlers(app)`; `class ApiError(code, status, message, details=None)` |
| `routes_chat.py` | `POST /api/chat`, `POST /api/session/reset` | `router` |
| `routes_products.py` | `GET /api/products/{id}`, `GET /api/products?ids=` | `router` |
| `routes_policy.py` | `GET /api/policy/sections`, `GET /api/policy/sections/{id}` | `router` |
| `routes_meta.py` | `GET /api/catalogue/meta` | `router` |
| `routes_health.py` | `GET /api/health`, `GET /api/ready` | `router` |
| `routes_eval.py` | `POST /api/eval/run`, `GET /api/eval/runs/{run_id}` (flag-gated) | `router` |

### 2.3 Schemas and domain

| File | Responsibility |
|---|---|
| `schemas/common.py` | `ErrorEnvelope`, `Metrics`, `Warning` codes |
| `schemas/chat.py` | `ChatRequest`, `ChatResponse`, `ClarificationBlock`, `RefusalBlock`, `ConstraintView`, `ConstraintOpIn`, `Resolution` |
| `schemas/product.py` | `ProductDetail`, `ProductCard`, `ProductAttribute` |
| `schemas/policy.py` | `PolicySection`, `PolicySectionSummary` |
| `schemas/evidence.py` | `Evidence`, `EvidenceKind`, `SearchManifest` |
| `schemas/claims.py` | `Claim`, `ClaimType`, `ClaimSpec`, `ClaimValue` variants |
| `schemas/eval.py` | `EvalRunRequest`, `EvalRunStatus`, `EvalCaseResult` |
| `domain/enums.py` | `Decision`, `Intent`, `RefusalReason`, `ClarifyReason`, `StockStatus`, `MatchBasis`, `ClaimType`, `GenerationMode`, `OpKind` |
| `domain/constraints.py` | `ConstraintSet`, `PriceBound`, `AttrConstraint`, `SortSpec`, `ConstraintOp`, `Conflict` |
| `domain/parsed_query.py` | `ParsedQuery`, `NormalizationTrace` |
| `domain/plans.py` | `SearchSpec`, `Candidate`, `SearchResult`, `DecisionPlan`, `ClarificationSpec`, `RefusalSpec`, `EvidenceBundle` |

All pydantic models: `model_config = ConfigDict(extra="forbid", frozen=True)` for internal contracts.

### 2.4 Ingestion (build-time; `app/ingest/`)

| File | Responsibility | Interface |
|---|---|---|
| `source_reader.py` | Safe read with size cap, UTF-8, format sniff (CSV/JSON/JSONL per UNK-01) | `read_records(path: Path, fmt: str, max_bytes: int) -> Iterator[tuple[int, dict]]` (source row number, raw dict); `read_text(path, max_bytes) -> str` |
| `catalogue_loader.py` | Apply `catalogue_mapping.yaml` to raw dicts | `to_raw_product(row_no: int, raw: dict, mapping: Mapping) -> RawProduct` |
| `field_parsers.py` | Pure parsers: price, rating, stock, attributes, category | `parse_price(raw, cfg) -> ParsedField[int]`; `parse_rating(raw, cfg) -> ParsedField[float]`; `parse_stock(raw, value_map) -> ParsedField[StockStatus]`; `parse_attributes(raw, cfg) -> list[Attribute]`; `ParsedField(value|None, issue|None)` |
| `catalogue_validator.py` | Identity validation, field validation, issue recording | `validate(raw_products) -> ValidationOutcome(valid: list[Product], quarantined: list[QuarantineRow], field_issues: list[FieldIssue])` |
| `dedupe.py` | Exact-duplicate removal, conflicting-ID quarantine, near-duplicate reporting | `dedupe(products) -> DedupeOutcome(kept, quarantined, near_duplicate_groups)` |
| `policy_parser.py` | Split policy text into sections with stable IDs | `parse_policy(text: str, cfg) -> list[PolicySectionRow]`; `coverage_ratio(text, sections) -> float` |
| `sanitizer.py` | Flag instruction-like text; strip control chars | `is_suspicious(text: str) -> bool`; `clean_text(text: str) -> str` |
| `build_pipeline.py` | Orchestrate build: read → map → validate → dedupe → DB → FTS → embed → manifest | `build_all(settings, *, embedder: EmbedderAdapter | None) -> BuildReport` |
| `manifest.py` | Hash inputs/config/outputs; verify | `write_manifest(...)`; `verify_manifest(settings) -> ManifestStatus(ok, reasons)` |

### 2.5 Store (`app/store/`)

| File | Responsibility | Interface |
|---|---|---|
| `db.py` | Open SQLite read-only (runtime) / read-write (build); FTS5 capability probe | `open_ro(path) -> sqlite3.Connection`; `open_rw(path)`; `fts5_available(conn) -> bool` |
| `product_repo.py` | All product reads | `get(product_id) -> Product | None`; `get_many(ids) -> dict[str, Product]`; `filter_ids(f: StructuredFilter) -> list[str]`; `vocab() -> CatalogueVocab`; `meta() -> CatalogueMeta`; `attributes(product_id) -> list[Attribute]` |
| `policy_repo.py` | Policy section reads | `list_sections() -> list[PolicySection]`; `get_section(section_id) -> PolicySection | None` |
| `vector_index.py` | Cosine search over L2-normalized vectors | `class VectorIndex(Protocol)`: `search(query_vec, k, allowed_ids: set[str] | None) -> list[tuple[str, float]]`; `NumpyVectorIndex.load(dir)`; `.dim`, `.model_id` |

### 2.6 NLP (`app/nlp/`) — deterministic first

| File | Responsibility | Interface |
|---|---|---|
| `normalizer.py` | NFKC, whitespace, currency symbols, number words (`2k`, `1.5k`, `lakh`) → canonical; produce a trace; never alter product IDs | `normalize(text: str, ctx: NormalizeCtx) -> NormalizedText(original, text, trace)` |
| `codemix.py` | Token-level lexicon replacement for supported code-mixed words; unknown tokens untouched | `apply_codemix(tokens, lexicon) -> tuple[list[str], list[TraceItem]]` |
| `spellfix.py` | Correct out-of-vocabulary tokens **only** to unique, close catalogue-vocabulary matches (length ≥5, ratio ≥ threshold); never touch IDs/numbers | `spellfix(tokens, vocab, cfg) -> tuple[list[str], list[TraceItem]]` |
| `price_parser.py` | Extract price bounds with explicit inclusive/exclusive semantics | `parse_price_phrases(text, cfg) -> list[ConstraintOp]` |
| `vocab.py` | Catalogue-derived vocabularies (categories, attribute keys/values, title tokens) cached at startup | `CatalogueVocab.categories`, `.attr_values_by_key`, `.title_tokens`, `.has_token(t)` |
| `intent.py` | Rule-based intent classification and requested-field detection | `classify(nt: NormalizedText, ctx: IntentCtx) -> IntentResult(intent, requested_fields, policy_topic, confidence)` |
| `constraint_extractor.py` | Category, attribute, stock, rating, sort, product-ID, entity-text extraction | `extract(nt, intent, vocab, cfg) -> ExtractionResult(ops: list[ConstraintOp], product_ids, entity_text, text_terms)` |
| `reference_resolver.py` | Resolve "it/this/that/ye", ordinals ("second one") against session | `resolve(nt, state) -> RefResolution(kind, product_id|None, candidates)` |
| `llm_extractor.py` *(Tier 3)* | LLM fallback when rules return UNKNOWN; output validated against vocab | `llm_extract(nt, vocab, llm) -> ExtractionResult | None` |

### 2.7 Conversation (`app/conversation/`)

| File | Responsibility | Interface |
|---|---|---|
| `state.py` | Session state model | `ConversationState(session_id, constraints: ConstraintSet, focus_product_id, last_result_ids, pending: PendingClarification | None, turn_no, updated_at)` |
| `merger.py` | Merge `ConstraintOp`s into a `ConstraintSet` with conflict detection | `merge(cs: ConstraintSet, ops: list[ConstraintOp], *, topic_switch: bool) -> MergeResult(constraints, conflicts, notes)` |
| `store.py` | Bounded in-memory sessions with TTL, thread-safe | `SessionStore.get_or_create(sid) -> ConversationState`; `.save(state)`; `.reset(sid)`; limits from config |
| `clarification.py` | Create and resolve pending clarifications | `make_pending(spec, parsed) -> PendingClarification`; `resolve(pending, resolution|text) -> ResolvedClarification | None` |

### 2.8 Retrieval (`app/retrieval/`) — finds candidates, never asserts facts

| File | Responsibility | Interface |
|---|---|---|
| `filters.py` | Convert `ConstraintSet` to a structured filter; compute eligible ID set | `eligible_ids(cs: ConstraintSet, repo) -> EligibleSet(ids, excluded_unknown_stock: int)` |
| `lexical.py` | FTS5 BM25 (or pure-Python fallback) restricted to eligible IDs | `LexicalIndex.search(query: str, allowed: set[str], k: int) -> list[tuple[str, float]]` |
| `semantic.py` | Embed query, cosine over eligible IDs | `SemanticRetriever.search(query, allowed, k) -> list[tuple[str, float]]` (empty + `available=False` if no embedder) |
| `fusion.py` | RRF | `rrf(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]` |
| `attribute_verifier.py` | Confirm attribute constraints from structured attributes (preferred) or description text with negation guard | `verify(product, constraints) -> VerifyResult(ok, matches: list[AttrMatch])` |
| `hybrid.py` | Orchestrate: eligibility → lexical + semantic → fusion → verification → counts | `HybridRetriever.search(spec: SearchSpec) -> SearchResult` |
| `ambiguity.py` | Resolve entity references to one product or an ambiguous set | `resolve_entity(entity_text, result: SearchResult, cfg) -> EntityResolution(kind: RESOLVED|AMBIGUOUS|NONE, candidates)` |
| `relaxation.py` | When empty, find which single constraint removal yields results | `analyze(spec, repo) -> list[Relaxation(dropped_constraint, result_count)]` |
| `policy_retriever.py` | Lexical + (optional) semantic over sections; coverage test | `PolicyRetriever.search(question: str, topic: str | None) -> PolicyResult(sections, established: bool, reasons)` |

### 2.9 Decision (`app/decision/`)

| File | Responsibility | Interface |
|---|---|---|
| `rules.py` | Pure predicates for each rule D-01…D-17 | one function per rule; each returns `RuleOutcome | None` |
| `claim_planner.py` | Build typed `ClaimSpec`s from DB rows (values read here from repo, never from text) | `plan_claims(decision_ctx) -> list[ClaimSpec]` |
| `engine.py` | Apply rules in precedence order | `decide(parsed, state, search, policy, repo, cfg) -> DecisionPlan` |

### 2.10 Generation (`app/generation/`)

| File | Responsibility | Interface |
|---|---|---|
| `evidence_builder.py` | Create `Evidence` objects (product field, policy section, search manifest) and register IDs | `build(plan, repo, policy_repo) -> EvidenceBundle` |
| `templates.py` | Sentence templates per `ClaimType` and non-factual templates (clarify/refuse/greeting) | `render_claim(spec: ClaimSpec, bundle) -> str`; `render_nonfactual(kind, params) -> str` |
| `template_generator.py` | Deterministic generator | `TemplateGenerator.generate(plan, bundle) -> GeneratedResponse(claims, nonfactual)` |
| `llm_generator.py` *(Tier 2)* | Ask LLM to rephrase **given claims only** | `LlmGenerator.generate(plan, bundle, llm) -> GeneratedResponse | GenerationFailure` |
| `prompt.py` | Build Tier 2 prompt from structured facts; delimit data | `build_prompt(claim_specs, bundle, cfg) -> list[Message]` |
| `output_parser.py` | Parse model output into `[{claim_id, sentence}]`; strict JSON with bounded repair (strip fences, first balanced object) | `parse_llm_output(text) -> ParsedLlmOutput | ParseError` |
| `citation.py` | Render inline markers and assemble `answer_text` | `render_answer_text(claims, nonfactual, fmt) -> str`; `format_marker(evidence_ids, fmt) -> str` |
| `card_builder.py` | Build `ProductCard`s directly from DB rows | `build_cards(product_ids, repo, match_basis) -> list[ProductCard]` |

### 2.11 Validation (`app/validation/`) — independent of generation

| File | Responsibility | Interface |
|---|---|---|
| `scanners.py` | Extract currency amounts, numbers, durations, product-ID patterns, stock/rating/policy keywords from text | `scan_currency(text)`, `scan_numbers(text)`, `scan_ids(text, id_regex)`, `scan_keywords(text)` |
| `rules.py` | Implement V-01…V-12 | `check_vNN(...) -> list[Violation]` |
| `validator.py` | Run all rules; **re-read the DB** for facts | `Validator(repo, policy_repo, cfg).validate(resp, bundle, plan) -> ValidationReport` |
| `report.py` | Violation model | `Violation(rule, claim_id|None, detail)`, `ValidationReport(ok, violations)` |

### 2.12 Models (`app/models/`)

```python
class EmbedderAdapter(Protocol):
    model_id: str
    dim: int
    def embed_documents(self, texts: list[str]) -> "np.ndarray": ...   # float32, L2-normalized, shape (n, dim)
    def embed_query(self, text: str) -> "np.ndarray": ...              # shape (dim,)
    def count_tokens(self, text: str) -> int | None: ...               # None if the runtime cannot tell

@dataclass(frozen=True)
class LLMResult:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    token_source: Literal["measured", "estimated", "unavailable"]
    latency_ms: float
    truncated: bool

class LLMAdapter(Protocol):
    model_id: str
    def generate(self, messages: list[Message], *, max_tokens: int,
                 temperature: float = 0.0, stop: list[str] | None = None,
                 timeout_s: float = 30.0) -> LLMResult: ...         # raises LLMUnavailable / LLMTimeout
```

| File | Responsibility |
|---|---|
| `base.py` | Protocols above, `Message`, exceptions `LLMUnavailable`, `LLMTimeout`, `EmbedderUnavailable` |
| `mock.py` | `MockEmbedder` (deterministic hashing embedder), `MockLLM` (echo of facts), `ScriptedLLM(outputs: list[str])` for malformed/adversarial tests |
| `registry.py` | `load_models(settings) -> LoadedModels(embedder|None, llm|None)`; absent → `None`, never crash |
| `embedder_<runtime>.py`, `llm_<runtime>.py` | Real adapters; created only after UNK-08/UNK-09 are answered |

### 2.13 Metrics (`app/metrics/`)

| File | Responsibility | Interface |
|---|---|---|
| `timing.py` | Stage timers | `with timer("retrieve_lexical"):` → records ms into the current `Recorder` |
| `tokens.py` | Token accounting with provenance | `TokenUsage(input, output, source)`; `estimate_tokens(text) -> int` (labelled `estimated`) |
| `recorder.py` | Per-request metrics; JSONL sink | `Recorder.start(request_id)`, `.stage(name)`, `.finish() -> Metrics` |
| `memory.py` | RSS sampling if `psutil` is installed, else `resource` (Unix) | `rss_mb() -> float | None` |

### 2.14 Scripts and evaluation

| File | Responsibility |
|---|---|
| `scripts/inspect_inputs.py` | Day 1: report formats, field names, value distributions, ID uniqueness, policy headings, env capabilities (`--env`: Python, SQLite, FTS5, CPU/RAM). Output to `docs/DAY1_FINDINGS.md` draft. |
| `scripts/smoke_models.py` | Day 1: load models via candidate runtime, embed 10 strings, generate 1 short completion, report load time, latency, token availability |
| `scripts/build_index.py` | CLI for `build_pipeline.build_all`; writes `build_report.json` (timings, counts, quarantine summary) |
| `scripts/gen_dev_set.py` | Generate the team-owned dev set with SQL-computed gold from the catalogue (not from hidden tests) |
| `scripts/run_eval.py` | `--suite dev|official --mode template|llm`; writes JSON + Markdown reports |
| `scripts/benchmark.py` | Latency/memory benchmark harness (see EVALUATION_PLAN §8) |
| `scripts/check_offline.py` | Starts app with network blocked and runs smoke queries |
| `eval/test_set_adapter.py` | `load_cases(path, fmt) -> list[EvalCase]`; one adapter per official format once known |
| `eval/official_bridge.py` | Exposes the system in the shape the official `evaluate.py` requires (UNK-10) |
| `eval/auditor.py` | Independent post-hoc audit of final answers against SQLite (no imports from `generation`/`decision`) |
| `eval/metrics.py` | Metric computations |
| `eval/provisional_score.py` | The labelled-provisional composite |
| `eval/runner.py` | Run cases sequentially, collect responses + metrics |
| `eval/report.py` | Render Markdown/JSON from templates in `eval/report_templates/` |

## 3. Frontend specification (`frontend/src/`)

### 3.1 Component tree

```
<App>
 └─ <ChatProvider>                       // context: state + dispatch
     ├─ <Header/>                        // title, "New conversation" button, health indicator
     ├─ <ActiveConstraintsBar/>          // chips from response.active_constraints (removable)
     │    └─ <ConstraintChip/>
     ├─ <MessageList role="log" aria-live="polite">
     │    ├─ <UserMessage/>
     │    └─ <AssistantMessage>
     │         ├─ <DecisionBadge/>       // ANSWER | CLARIFY | REFUSE (text + icon, not colour only)
     │         ├─ <AnswerText/>          // renders sentences with <CitationChip/> markers
     │         │    └─ <CitationChip/>   // product ID or policy section; opens <EvidenceDrawer/>
     │         ├─ <ProductCardList/>
     │         │    └─ <ProductCard/>    // title, ID, price_display, stock label, rating, match_basis note
     │         ├─ <ClarificationChoices/> // buttons from clarification.options
     │         ├─ <RefusalNotice/>       // reason + detail; no retry spam
     │         └─ <SuggestionList/>      // relaxation suggestions (structured constraint ops)
     ├─ <LoadingIndicator/>             // role="status"
     ├─ <ErrorBanner/>                  // role="alert", retry button
     ├─ <Composer/>                     // textarea + send, 500-char counter
     └─ <EvidenceDrawer/>               // product record or policy section text from /api/products or /api/policy
```

### 3.2 State management

`useReducer` in `ChatProvider`. No global store library.

```ts
type ChatState = {
  sessionId: string;                 // crypto.randomUUID() stripped to [A-Za-z0-9_-]
  messages: Message[];               // user and assistant messages with full ChatResponse attached
  status: "idle" | "sending" | "error";
  error: { code: string; message: string; requestId?: string } | null;
  activeConstraints: ConstraintView[];   // always taken from the last response, never computed client-side
  drawer: { kind: "product" | "policy"; id: string } | null;
};
type Action =
  | { type: "SEND_START"; text: string }
  | { type: "SEND_OK"; response: ChatResponse }
  | { type: "SEND_ERR"; error: ApiErrorBody }
  | { type: "RESET_OK" } | { type: "OPEN_DRAWER"; kind: "product"|"policy"; id: string } | { type: "CLOSE_DRAWER" };
```

Rules:
- The frontend **never** formats, rounds or computes prices; it displays `price_display` supplied by the backend.
- The frontend never builds constraints from text; chips and clarification buttons send **structured** `constraint_ops` / `resolution` objects.

### 3.3 API integration (`src/api/`)

- `client.ts`: `postChat(req)`, `resetSession(sessionId)`, `getProduct(id)`, `getPolicySection(id)`, `getHealth()`. Uses `fetch`, 30 s timeout via `AbortController`, parses the error envelope.
- `types.ts`: hand-written TypeScript types mirroring `API_SPEC.md` (generation from OpenAPI is optional). A contract test (`test/contract.test.ts`) validates a recorded sample of each response example against the types.
- Dev proxy: Vite proxies `/api` to `http://localhost:8000`. Production: same origin.

### 3.4 Behaviour requirements

| Requirement | Specification |
|---|---|
| Loading | Disable composer, show `role="status"` text "Searching the catalogue…"; request timeout → ErrorBanner with retry that re-sends the same payload |
| Errors | Show `error.message` and `request_id`; never show stack traces; 503 `INDEX_NOT_READY` shows a dedicated "Catalogue index not ready" message |
| Product cards | Show title, product ID, `price_display` or "Price not available", stock label (`In stock` / `Out of stock` / `Stock status not available`), rating or "No rating", category; `match_basis=SEMANTIC_RELATED` shows "Closest by meaning — verify details" |
| Citations | Every sentence in `answer_text` is followed by chips; click opens drawer fetching the product (all fields) or policy section; policy chips show section ID and heading |
| Clarification | Buttons labelled with title + ID + price display; click sends `resolution`; typed free text also allowed |
| Refusal | Distinct visual block with the explicit reason; never styled as an answer |
| Multi-turn | `session_id` persists in memory for the tab; "New conversation" calls `/api/session/reset` and clears messages; chips remove constraints via `constraint_ops` |
| Decorative animation | None required; transitions ≤150 ms and disabled under `prefers-reduced-motion` |

### 3.5 Accessibility requirements

- Message list: `role="log"`, `aria-live="polite"`, new assistant message announced once.
- All controls reachable by keyboard; visible focus ring; Enter sends, Shift+Enter inserts newline.
- After a response, focus remains in the composer; clarification buttons are in tab order and have descriptive `aria-label`s (include product title, ID, price).
- Decision is conveyed by text label ("Answer", "Needs clarification", "Cannot answer"), not colour alone; contrast ≥ WCAG AA (4.5:1 text).
- Drawer: focus trap, `Esc` closes, focus returns to the invoking chip.
- Labels on textarea and buttons; `<html lang="en">`.
- Respect `prefers-reduced-motion`.

### 3.6 Frontend files

| File | Responsibility |
|---|---|
| `src/main.tsx`, `App.tsx` | Bootstrapping and layout |
| `src/api/client.ts`, `types.ts` | API integration |
| `src/state/chatReducer.ts`, `ChatContext.tsx` | State |
| `src/hooks/useChat.ts` | `send(text)`, `resolveClarification(optionId)`, `applyConstraintOp(op)`, `reset()` |
| `src/components/*` | As in the tree |
| `src/styles/*.css` | Plain CSS with variables; no UI framework |
| `src/test/*` | Vitest/RTL tests: reducer, ProductCard formatting of missing fields, citation chip rendering, clarification click payload, a11y roles |
