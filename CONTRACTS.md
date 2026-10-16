# CONTRACTS.md — Canonical API and internal module contracts (revision 2)

> **Status: design only. Nothing here is implemented or tested.** This is the **single source of truth** for enumerations, wire schemas, internal domain types and module interfaces. Other documents reference sections of this file (`CONTRACTS §n`) and must not redefine anything in it. Any change requires an entry in `docs/DECISIONS.md` and updates to every consumer.

## 0. Rules

| ID | Rule |
|---|---|
| C-1 | **Responses:** every field of every response model is required; "absent" is `null` or `[]`. Unknown fields are forbidden (`extra="forbid"`). |
| C-2 | **Requests:** only `message` is meaningful by default; other fields have the defaults stated below. Unknown fields are rejected. |
| C-3 | Enumerations are defined once, in §1. |
| C-4 | Examples in other documents are marked **complete** (must validate against these models; checked by test T-API-02) or **fragment** (illustrative only). |
| C-5 | Product IDs must not contain `:` (evidence IDs use it as separator). Enforced by `product_id_regex` (UNK-05). |
| C-6 | Facts (prices, stock, ratings, attributes, policy text, counts) enter a response only through `ClaimSpec`s, `CardSpec`s and `ClarificationSpec`s built from database rows by code. |
| C-7 | In the MVP **no language model produces any user-visible text.** There is no LLM adapter in the MVP. |

## 1. Enumerations

```python
class Decision(str, Enum):        ANSWER; CLARIFY; REFUSE
class Intent(str, Enum):          PRODUCT_FACT; EXISTENCE; SEARCH; POLICY; PRODUCT_WARRANTY; GREETING_HELP; RESET; OUT_OF_SCOPE; UNKNOWN
class RequestedField(str, Enum):  PRICE; STOCK; RATING; CATEGORY; DESCRIPTION; ATTRIBUTE; TITLE
class StockStatus(str, Enum):     IN_STOCK; OUT_OF_STOCK; UNKNOWN
class ClarifyReason(str, Enum):   AMBIGUOUS_PRODUCT; UNRESOLVED_REFERENCE; UNDER_SPECIFIED_QUERY; CONTRADICTORY_CONSTRAINTS; AMBIGUOUS_POLICY_TOPIC
class RefusalReason(str, Enum):   NO_CATALOGUE_MATCH; FIELD_MISSING; POLICY_NOT_ESTABLISHED; SEARCH_INCOMPLETE; OUT_OF_SCOPE; UNSUPPORTED_REQUEST; INSUFFICIENT_EVIDENCE; INTERNAL_VALIDATION_FAILURE
class Completeness(str, Enum):    EXHAUSTIVE; INCOMPLETE
class CardOrigin(str, Enum):      SEARCH_RESULT; EXISTENCE_MATCH; LOOKUP; CLARIFY_OPTION; RELATED
class AttrBasis(str, Enum):       STRUCTURED; TITLE_TEXT; DESCRIPTION_TEXT
class ClaimScope(str, Enum):      PRODUCT; CATALOGUE; GENERAL_POLICY
class EvidenceKind(str, Enum):    PRODUCT_FIELD; POLICY_SECTION; SEARCH_MANIFEST
class ClaimType(str, Enum):       PRODUCT_EXISTS; PRICE; STOCK; RATING; CATEGORY; ATTRIBUTE; DESCRIPTION_QUOTE;
                                  FIELD_UNAVAILABLE; PRODUCT_SUMMARY; RESULT_COUNT; ABSENCE; POLICY_QUOTE; POLICY_NOT_FOUND
class WarningCode(str, Enum):     SEMANTIC_UNAVAILABLE; UNKNOWN_STOCK_EXCLUDED; SPELLING_CORRECTED; CODEMIX_NORMALIZED;
                                  CONSTRAINTS_CARRIED_OVER; CONSTRAINTS_NOT_APPLIED_TO_LOOKUP; SEARCH_INCOMPLETE;
                                  RESULTS_TRUNCATED; FOCUS_PRODUCT_UNKNOWN
class OpKind(str, Enum):          SET; ADD; REMOVE; RESET
```

**Search completeness (canonical meaning).** A catalogue search is `EXHAUSTIVE` iff **all** of: (a) hard filters were evaluated by SQL over every product row; (b) attribute verification was applied to every product in the filtered set; (c) lexical term matching was computed over the whole catalogue with no top-k limit; (d) no backend error and no time-budget overrun occurred. Otherwise it is `INCOMPLETE`, with reasons. **Only an `EXHAUSTIVE` search with `exact_match_count == 0` may support an `ABSENCE` claim. Semantic scores never contribute to membership, counts or absence.**

## 2. Wire contracts

### 2.1 Requests

```python
class ConstraintOpIn(Model):
    op: Literal["SET","ADD","REMOVE","RESET"]
    key: Literal["category","price_min","price_max","stock","rating_min","attribute","term","sort","all"]
    value: Any | None = None
```

| `key` | Allowed `op` | `value` |
|---|---|---|
| `price_min`, `price_max` | SET, REMOVE | SET: `{"amount_minor": int ≥ 0, "inclusive": bool}`; REMOVE: null |
| `stock` | SET, REMOVE | `"IN_STOCK"` \| `"OUT_OF_STOCK"` \| null (REMOVE) |
| `rating_min` | SET, REMOVE | number within the configured rating range |
| `category` | ADD, REMOVE | canonical category name from `/api/catalogue/meta`; REMOVE with null removes all |
| `attribute` | ADD, REMOVE | `{"key": str \| null, "value": str}`; REMOVE with null removes all |
| `term` | ADD, REMOVE | non-empty string ≤ 60 chars; REMOVE with null removes all |
| `sort` | SET, REMOVE | `{"field": "price"\|"rating", "direction": "asc"\|"desc"}` |
| `all` | RESET | null |

```python
class ClientContext(Model): focus_product_id: str | None = None
class Resolution(Model):    clarification_id: str; option_id: str
class ChatOptions(Model):   max_results: int = 5            # 1..10
class ChatRequest(Model):
    session_id: str | None = None                            # ^[A-Za-z0-9_-]{8,64}$ ; server creates one if null
    message: str = ""                                        # trimmed, 0..500 chars; may be empty only if constraint_ops or resolution non-empty
    client_context: ClientContext = ClientContext()
    constraint_ops: list[ConstraintOpIn] = []                # max 10
    resolution: Resolution | None = None
    options: ChatOptions = ChatOptions()
class ResetRequest(Model):  session_id: str
```

There is **no** `generation_mode` field in the MVP (C-7); sending one is a `VALIDATION_ERROR`.

### 2.2 Evidence

```python
class ProductFieldEvidence(Model):
    evidence_id: str                                  # "prod:<product_id>:<field>"
    kind: Literal["PRODUCT_FIELD"]
    product_id: str
    field: str                                        # title|price|stock|rating|category|description|attr.<key_norm>
    value: str | int | float | None                   # raw stored value: price -> int minor units; stock -> enum string
    display_value: str | None
    available: bool                                   # False iff value is None (or stock UNKNOWN)
    excerpt: str | None                               # verbatim matched span ≤200 chars for TITLE_TEXT / DESCRIPTION_TEXT bases and DESCRIPTION_QUOTE; else None

class PolicyEvidence(Model):
    evidence_id: str                                  # "pol:<section_id>"
    kind: Literal["POLICY_SECTION"]
    section_id: str; heading: str
    excerpt: str                                      # verbatim span of the section body that a claim relies on

class SearchEvidence(Model):
    evidence_id: str                                  # "search:<search_id>"
    kind: Literal["SEARCH_MANIFEST"]
    search: CatalogueSearchManifest | PolicySearchManifest      # discriminated by `scope`

Evidence = ProductFieldEvidence | PolicyEvidence | SearchEvidence   # discriminated by `kind`
```

```python
class Relaxation(Model): dropped: str; label: str; exact_match_count: int

class CatalogueSearchManifest(Model):
    search_id: str                                    # "s_<turn_id>"
    scope: Literal["CATALOGUE"]
    intent: Intent
    query_text: str                                   # normalized text actually searched
    lookup_ids: list[str]                             # explicit IDs looked up (empty for searches)
    applied_constraints: list[ConstraintView]         # hard constraints actually applied this turn
    term_requirements: list[str]                      # lexical terms required (topical or entity terms)
    term_min_coverage: float
    completeness: Completeness
    incomplete_reasons: list[str]                     # e.g. LEXICAL_BACKEND_ERROR, SEARCH_BUDGET_EXCEEDED; [] when EXHAUSTIVE
    eligible_count: int                               # after hard filters
    exact_match_count: int                            # eligible ∩ attribute-verified ∩ term-covered (exact; valid only if EXHAUSTIVE)
    tied_count: int | None                            # entity lookups: products tied at best coverage
    returned_count: int
    excluded_unknown_stock: int
    relaxations: list[Relaxation]                     # only when exact_match_count == 0 and EXHAUSTIVE
    ranking: dict[str, bool]                          # {"lexical": bool, "semantic": bool}: affects ORDER only
    related_count: int                                # semantic RELATED candidates (never counted as matches)

class PolicySearchManifest(Model):
    search_id: str                                    # "p_<turn_id>"
    scope: Literal["POLICY"]
    query_terms: list[str]
    topic: str | None
    sections_checked: list[str]                       # all section IDs examined
    min_coverage: float
    best_coverage: float | None
    matched_section_ids: list[str]
    completeness: Completeness
    incomplete_reasons: list[str]
```

### 2.3 Claims, cards, clarification, refusal

```python
class Claim(Model):
    claim_id: str                                     # "c1","c2",…
    type: ClaimType
    scope: ClaimScope
    product_id: str | None                            # set iff scope == PRODUCT
    value: dict                                       # per §3
    sentence: str                                     # exactly render_claim(spec); no citation markers
    evidence_ids: list[str]                           # ≥1

class CardFact(Model):
    field: Literal["price","stock"]                   # MVP card facts (config `card_fields`)
    label: str                                        # "Price" | "Stock"
    value: str | int | float | None                   # raw
    display: str                                      # "₹1,299.00" | "In stock" | "Not available"
    available: bool
    evidence_id: str                                  # PRODUCT_FIELD evidence for this fact (value may be null)

class ProductCard(Model):
    product_id: str
    title: str
    title_evidence_id: str
    origin: CardOrigin
    applied_search_id: str | None                     # manifest whose applied_constraints this card was verified against; null for LOOKUP, CLARIFY_OPTION
    facts: list[CardFact]

class ClarificationOption(Model):
    option_id: str
    kind: Literal["PRODUCT","CONSTRAINT_OPS","POLICY_TOPIC"]
    label: str                                        # rendered by template; every fact in it is evidenced
    product_id: str | None
    constraint_ops: list[ConstraintOpIn]
    policy_topic: str | None
    evidence_ids: list[str]                           # evidence for every fact shown in `label`

class ClarificationBlock(Model):
    clarification_id: str
    reason: ClarifyReason
    options: list[ClarificationOption]                # ≤ clarify_max_options

class RefusalBlock(Model): reason: RefusalReason

class Suggestion(Model):
    claim_id: str                                     # the RESULT_COUNT(context=RELAXED) claim this button belongs to
    label: str                                        # e.g. "Remove: In stock only" (constraint label, allowlisted)
    constraint_ops: list[ConstraintOpIn]

class ConstraintView(Model): key: str; label: str; op_to_remove: ConstraintOpIn
class Metrics(Model):
    request_id: str; total_ms: float
    stages: dict[str, float]                          # normalize, parse, merge, retrieve, decide, evidence, generate, validate (ms)
    tokens: dict[str, Any]                            # {"embedder_input": int|None, "source": "measured"|"estimated"|"unavailable"}
    validator_trips: int
```

### 2.4 ChatResponse

```python
class ChatResponse(Model):
    session_id: str
    turn_id: int                                      # starts at 1 per session
    decision: Decision
    answer_text: str                                  # exactly render_answer_text(...) (§4)
    claims: list[Claim]
    evidence: list[Evidence]                          # every ID referenced by claims, cards, options
    products: list[ProductCard]
    clarification: ClarificationBlock | None          # non-null iff decision == CLARIFY
    refusal: RefusalBlock | None                      # non-null iff decision == REFUSE
    active_constraints: list[ConstraintView]
    suggestions: list[Suggestion]
    warnings: list[WarningCode]
    metrics: Metrics
```

### 2.5 Other wire models

```python
class ErrorBody(Model):  code: str; message: str; details: list[dict] | None; request_id: str
class ErrorEnvelope(Model): error: ErrorBody
class ProductDetail(Model):
    product_id: str; title: str; description: str | None; category: str | None
    price_minor: int | None; currency: str | None; price_display: str | None
    rating: float | None; rating_count: int | None
    stock_status: StockStatus; stock_label: str; stock_quantity: int | None
    attributes: list[dict]                            # {key, value, raw_key, raw_value}
    unavailable_fields: list[str]; flags: dict[str, bool]     # {"suspicious_text": bool}
class PolicySectionDetail(Model): section_id: str; heading: str; body: str; parent_id: str | None
class CatalogueMeta(Model):
    product_count: int; quarantined_count: int; field_issue_counts: dict[str, int]
    categories: list[dict]                            # {name, count}
    price_range: dict                                 # {min_minor, max_minor, currency} (nulls allowed)
    stock_distribution: dict[str, int]; policy_section_count: int
    built_at: str; manifest_hash: str
    embedder: str | None; embedding_dim: int | None
    semantic_available: bool; lexical_backend: Literal["FTS5","PY_BM25"]
class ReadinessReport(Model):
    ready: bool; checks: dict[str, bool]              # database, manifest, policy, fts5, vectors, embedder
    degraded: list[str]
class ResetResponse(Model): session_id: str; reset: bool
class HealthResponse(Model): status: Literal["ok"]
```

## 3. Claim types (value shape, evidence, template)

`T-xx` template IDs are defined in `GROUNDING_AND_REFUSAL.md §4` and are the **only** way a `sentence` is produced.

| `type` | `scope` | `value` | Required evidence | Template |
|---|---|---|---|---|
| `PRODUCT_EXISTS` | PRODUCT | `{}` | `prod:ID:title` | T-01 |
| `PRICE` | PRODUCT | `{"price_minor": int, "currency": str\|null}` | `prod:ID:price` (available), `prod:ID:title` | T-02 |
| `STOCK` | PRODUCT | `{"stock_status": "IN_STOCK"\|"OUT_OF_STOCK"}` | `prod:ID:stock` (available), title | T-03 |
| `RATING` | PRODUCT | `{"rating": float, "rating_count": int\|null}` | `prod:ID:rating`, title | T-04 |
| `CATEGORY` | PRODUCT | `{"category": str}` | `prod:ID:category`, title | T-05 |
| `ATTRIBUTE` | PRODUCT | `{"key": str\|null, "value": str, "basis": AttrBasis}` | `STRUCTURED` → `prod:ID:attr.<key_norm>`; `TITLE_TEXT` → `prod:ID:title` **with `excerpt`**; `DESCRIPTION_TEXT` → `prod:ID:description` **with `excerpt`**; plus title | T-06a/b/c by basis |
| `DESCRIPTION_QUOTE` | PRODUCT | `{"quote": str}` (verbatim substring of description) | `prod:ID:description` with `excerpt == quote`, title | T-07 |
| `FIELD_UNAVAILABLE` | PRODUCT | `{"field": "price"\|"stock"\|"rating"\|"category"\|"description"\|"attr.<key_norm>"}` | that field's evidence with `available=false`, title | T-08 |
| `PRODUCT_SUMMARY` | PRODUCT | `{"price_minor": int\|null, "currency": str\|null, "stock_status": "IN_STOCK"\|"OUT_OF_STOCK"\|null}` | `prod:ID:price`, `prod:ID:stock`, title (null values ⇒ `available=false` evidence) | T-09 |
| `RESULT_COUNT` | CATALOGUE | `{"count": int, "context": "SEARCH"\|"EXISTENCE"\|"AMBIGUOUS"\|"RELAXED", "relaxed_constraint": str\|null}` | `search:<id>` (EXHAUSTIVE); for AMBIGUOUS also `prod:ID:title` of each option | T-10a/b/c/d |
| `ABSENCE` | CATALOGUE | `{"searched": str}` | `search:<id>` with `completeness == EXHAUSTIVE` and `exact_match_count == 0` | T-11 |
| `POLICY_QUOTE` | GENERAL_POLICY | `{"quote": str, "section_id": str}` | `pol:<section_id>` with `excerpt == quote` | T-12 |
| `POLICY_NOT_FOUND` | GENERAL_POLICY | `{"topic": str\|null, "searched": str}` | `search:<id>` policy manifest, EXHAUSTIVE, `matched_section_ids == []` | T-13 |

`RESULT_COUNT.count` equals: `exact_match_count` (SEARCH, EXISTENCE), `tied_count` (AMBIGUOUS), `relaxations[dropped].exact_match_count` (RELAXED).

## 4. Evidence IDs, citation markers, answer-text assembly

- **Grammar:** `prod:<product_id>:<field>`, `pol:<section_id>`, `search:<search_id>` (`s_<turn>` catalogue, `p_<turn>` policy).
- **Marker:** after each claim sentence, ` [` + comma-separated tokens + `]`. Tokens are derived from the claim's `evidence_ids`: product evidence → bare `product_id` (deduplicated, first-appearance order); then policy evidence → `policy:<section_id>`; then search evidence → `search:<search_id>`.
- **`citation.search_marker`** (config, default `SHOW`): `SHOW` renders `search:` tokens; `HIDE` omits them from `answer_text` (a claim that then has no token is rendered without a bracket). Structured `evidence` is **always** complete. The choice is made on Day 1 from the official evaluator contract (UNK-17); see `EVALUATION_PLAN §2`.
- **Assembly:** `answer_text = " ".join(segment_text)` over `DecisionPlan.segments` in order, where a claim segment is `sentence + marker` and a non-factual segment is its template text. No other text may appear.

## 5. Internal domain types

```python
@dataclass(frozen=True) class TraceItem: kind: str; before: str; after: str
@dataclass(frozen=True) class NormalizedText: original: str; text: str; trace: tuple[TraceItem, ...]

@dataclass(frozen=True) class PriceBound: amount_minor: int; inclusive: bool
@dataclass(frozen=True) class AttrConstraint: key: str | None; value: str            # key None = any attribute / title / description
@dataclass(frozen=True) class SortSpec: field: Literal["price","rating"]; direction: Literal["asc","desc"]

@dataclass(frozen=True)
class ConstraintSet:
    categories: frozenset[str]                  # canonical category_norm values, OR semantics
    price_min: PriceBound | None
    price_max: PriceBound | None
    stock: StockStatus | None                   # IN_STOCK | OUT_OF_STOCK
    rating_min: float | None
    attributes: tuple[AttrConstraint, ...]      # AND
    terms: tuple[str, ...]                      # required lexical terms (coverage rule applies)
    sort: SortSpec | None

@dataclass(frozen=True) class ConstraintOp: op: OpKind; key: str; value: Any; origin: Literal["USER_TEXT","UI_ACTION","CLARIFICATION"]

@dataclass(frozen=True)
class ParsedQuery:
    original: str; normalized: NormalizedText
    intent: Intent
    requested_fields: tuple[RequestedField, ...]; attribute_key: str | None
    product_ids: tuple[str, ...]                # explicit IDs in text
    entity_terms: tuple[str, ...]               # PRODUCT_FACT / EXISTENCE name terms
    references_focus: bool                      # it / this / that
    policy_topic: str | None
    constraint_ops: tuple[ConstraintOp, ...]    # explicit this turn
    this_turn_constraints: ConstraintSet        # constraints stated in this turn only (used by EXISTENCE)
    confidence: float

@dataclass(frozen=True)
class AttrMatch:
    constraint: AttrConstraint
    basis: AttrBasis
    evidence_field: str                         # "attr.<key_norm>" | "title" | "description"  (must agree with basis)
    matched_text: str | None                    # verbatim span for text bases

@dataclass(frozen=True)
class Candidate:
    product_id: str
    term_coverage: float
    match_location: Literal["TITLE_OR_ATTR","CATEGORY","DESCRIPTION_ONLY"]
    lexical_rank: int | None; semantic_rank: int | None; fused_score: float
    attr_matches: tuple[AttrMatch, ...]

@dataclass(frozen=True)
class SearchSpec: intent: Intent; constraints: ConstraintSet; entity_terms: tuple[str, ...]; top_k: int; apply_semantic_related: bool

@dataclass(frozen=True)
class SearchResult:
    manifest: CatalogueSearchManifest
    exact_ids: tuple[str, ...]                  # ALL exact matches (unordered set semantics, sorted for determinism)
    ranked: tuple[Candidate, ...]               # top_k exact matches in display order
    related: tuple[Candidate, ...]              # RELATED candidates (≤ related_suggestions.max)

@dataclass(frozen=True)
class EntityResolution: kind: Literal["RESOLVED","AMBIGUOUS","NONE","DESCRIPTION_MENTION_ONLY"]; candidates: tuple[Candidate, ...]; manifest: CatalogueSearchManifest

@dataclass(frozen=True) class PolicyResult: manifest: PolicySearchManifest; sections: tuple[PolicySection, ...]; established: bool

@dataclass(frozen=True) class ClaimSpec: claim_id: str; type: ClaimType; scope: ClaimScope; product_id: str | None; value: dict; evidence_ids: tuple[str, ...]
@dataclass(frozen=True) class CardSpec: product_id: str; origin: CardOrigin; applied_search_id: str | None
@dataclass(frozen=True) class OptionSpec: option_id: str; kind: str; product_id: str | None; constraint_ops: tuple[ConstraintOpIn, ...]; policy_topic: str | None
@dataclass(frozen=True) class ClarificationSpec: reason: ClarifyReason; options: tuple[OptionSpec, ...]
@dataclass(frozen=True) class SegmentClaim: claim_id: str
@dataclass(frozen=True) class SegmentNF: kind: str; params: dict[str, str | int]     # kind ∈ NF-xx ids (GROUNDING §4)
Segment = SegmentClaim | SegmentNF

@dataclass(frozen=True)
class DecisionPlan:
    decision: Decision; intent: Intent
    reason: ClarifyReason | RefusalReason | None
    segments: tuple[Segment, ...]               # fixes the order of answer_text
    claim_specs: tuple[ClaimSpec, ...]
    card_specs: tuple[CardSpec, ...]
    clarification: ClarificationSpec | None
    suggestions: tuple[tuple[str, tuple[ConstraintOpIn, ...], str], ...]   # (claim_id, ops, label)
    manifests: tuple[CatalogueSearchManifest | PolicySearchManifest, ...]
    constraints_after: ConstraintSet
    state_update: StateUpdate                   # focus, last_result_ids, pending clarification, constraints to persist
    warnings: tuple[WarningCode, ...]

@dataclass(frozen=True) class PlanDigest: claim_ids: tuple[str,...]; types: tuple[ClaimType,...]; values: tuple[dict,...]; evidence_ids: tuple[tuple[str,...],...]   # what the validator may know about the plan
@dataclass(frozen=True) class DraftResponse: <all ChatResponse fields except session_id, turn_id, metrics, warnings, active_constraints>
@dataclass(frozen=True) class Violation: rule: str; claim_id: str | None; detail: str
@dataclass(frozen=True) class ValidationReport: ok: bool; violations: tuple[Violation, ...]
```

## 6. Module interfaces (MVP)

Package `backend/app/`. **Signatures are normative.** Files are listed in `folder_structure.md`.

### 6.1 Wiring
```python
# settings.py
def load_settings(config_dir: Path) -> Settings
# container.py
def build_container(settings: Settings) -> Container          # holds db, product_repo, policy_repo, vocab, lexical, semantic | None, sessions
# orchestrator.py
class Orchestrator:
    def __init__(self, container: Container) -> None
    def handle(self, req: ChatRequest, request_id: str) -> ChatResponse      # never raises for validation/model failures
    def reset(self, session_id: str) -> None
```

### 6.2 Ingestion (build time)
```python
# ingest/loader.py
def read_catalogue(settings: Settings) -> Iterator[RawProduct]
def read_policy_text(settings: Settings) -> str
# ingest/parsers.py
@dataclass(frozen=True) class Parsed(Generic[T]): value: T | None; issue: str | None
def parse_price(raw: Any, cfg: PriceCfg) -> Parsed[int]            # minor units
def parse_rating(raw: Any, cfg: RatingCfg) -> Parsed[float]
def parse_stock(raw: Any, cfg: StockCfg) -> Parsed[StockStatus]
def parse_attributes(raw: Any, cfg: AttrCfg) -> list[Attribute]
# ingest/validate.py
def validate_and_dedupe(rows: Iterable[RawProduct], cfg: ValidationCfg) -> ValidationOutcome
# ingest/policy_parser.py
def parse_policy(text: str, cfg: PolicyCfg) -> list[PolicySectionRow]
def coverage_ratio(text: str, sections: list[PolicySectionRow]) -> float
# ingest/sanitizer.py
def is_suspicious(text: str) -> bool
def clean_text(text: str) -> str
# ingest/build.py
def build_all(settings: Settings, embedder: EmbedderAdapter | None) -> BuildReport
def verify_manifest(settings: Settings) -> ManifestStatus
```

### 6.3 Store
```python
# store/db.py
def open_ro(path: Path) -> sqlite3.Connection
def open_rw(path: Path) -> sqlite3.Connection
def fts5_available(conn: sqlite3.Connection) -> bool
# store/repos.py
class ProductRepo:
    def get(self, product_id: str) -> Product | None
    def get_many(self, ids: Iterable[str]) -> dict[str, Product]
    def attributes(self, product_id: str) -> list[Attribute]
    def eligible_ids(self, c: ConstraintSet) -> EligibleSet          # SQL over ALL rows; hard filters only (category, price, stock, rating)
    def vocab(self) -> CatalogueVocab
    def meta(self) -> CatalogueMeta
class PolicyRepo:
    def list_sections(self) -> list[PolicySection]
    def get_section(self, section_id: str) -> PolicySection | None
@dataclass(frozen=True) class EligibleSet: ids: frozenset[str]; excluded_unknown_stock: int
```

### 6.4 Query understanding
```python
# nlp/normalizer.py
def normalize(text: str, cfg: NormalizeCfg, vocab: CatalogueVocab) -> NormalizedText
# nlp/price_parser.py
def parse_price_phrases(text: str, cfg: PriceSemanticsCfg) -> list[ConstraintOp]
# nlp/vocab.py
class CatalogueVocab: categories; attr_values_by_key; generic_words: frozenset[str]; def has_term(self, t: str) -> bool
# nlp/intent.py
def classify_intent(nt: NormalizedText, view: StateView, cfg: IntentCfg) -> IntentResult
# nlp/extractor.py   — the only entry point used by the orchestrator
def parse_query(message: str, view: StateView, vocab: CatalogueVocab, cfg: NlpCfg) -> ParsedQuery
```
`StateView` = read-only `(has_focus: bool, constraints: ConstraintSet, last_result_ids: tuple[str,...])`.

### 6.5 Conversation
```python
# conversation/state.py
@dataclass class PendingClarification: clarification_id: str; reason: ClarifyReason; options: tuple[OptionSpec,...]; parsed: ParsedQuery
@dataclass class ConversationState: session_id; constraints: ConstraintSet; focus_product_id: str | None; last_result_ids: tuple[str,...]; pending: PendingClarification | None; turn_no: int; updated_at: float
class SessionStore:
    def get_or_create(self, session_id: str | None) -> ConversationState
    def save(self, state: ConversationState) -> None
    def reset(self, session_id: str) -> None
# conversation/merger.py
@dataclass(frozen=True) class MergeResult: constraints: ConstraintSet; conflicts: tuple[Conflict, ...]; topic_switch: bool; carried_over: bool
def merge(current: ConstraintSet, ops: Sequence[ConstraintOp], *, additive_cue: bool, vocab: CatalogueVocab) -> MergeResult
```

### 6.6 Retrieval
```python
# retrieval/lexical.py
class LexicalIndex:                                                  # FTS5, or pure-Python fallback with identical results
    def match_terms(self, terms: Sequence[str], columns: Sequence[str]) -> dict[str, frozenset[str]]
        # exhaustive, no limit; raises LexicalError on backend failure
    def rank(self, terms: Sequence[str], allowed: frozenset[str], k: int) -> list[tuple[str, float]]   # BM25 order only
# retrieval/verifier.py
def verify_attributes(product: Product, attrs: Sequence[Attribute], wanted: Sequence[AttrConstraint], cfg: VerifierCfg) -> VerifyResult   # VerifyResult(ok: bool, matches: tuple[AttrMatch,...])
# retrieval/semantic.py
class NumpyVectorIndex:  @classmethod def load(cls, dir: Path) -> "NumpyVectorIndex"; model_id: str; dim: int
class SemanticRanker:
    available: bool
    def rank(self, query: str, allowed: frozenset[str], k: int) -> list[tuple[str, float]]      # cosine order; ordering/RELATED only
# retrieval/hybrid.py
class HybridRetriever:
    def search_catalogue(self, spec: SearchSpec, turn_id: int) -> SearchResult
    def resolve_entity(self, intent: Intent, entity_terms: Sequence[str], constraints: ConstraintSet, turn_id: int) -> EntityResolution
    def lookup_ids(self, ids: Sequence[str], turn_id: int) -> IdLookupResult     # IdLookupResult(found: dict[str, Product], missing: list[str], manifest: CatalogueSearchManifest)
# retrieval/ambiguity.py
def classify_ties(candidates: Sequence[Candidate], max_options: int) -> EntityResolution-like (kind, ordered options)
# retrieval/relaxation.py
def analyze(spec: SearchSpec, retriever: HybridRetriever) -> list[Relaxation]
# retrieval/policy.py
class PolicyRetriever:
    def search(self, question_terms: Sequence[str], topic: str | None, turn_id: int) -> PolicyResult
```

### 6.7 Decision
```python
# decision/engine.py
def decide(ctx: DecisionContext) -> DecisionPlan        # applies D-01…D-18 in the precedence order of GROUNDING §3
# decision/claim_planner.py
def plan_claims(kind: str, data: Mapping[str, Any], repo: ProductRepo) -> list[ClaimSpec]    # values are read from rows here
```
`DecisionContext(parsed, state_before, merge: MergeResult, search: SearchResult | None, entity: EntityResolution | None, id_lookup: IdLookupResult | None, policy: PolicyResult | None, turn_id: int, cfg)`.

### 6.8 Generation (deterministic only)
```python
# generation/evidence.py
def build_evidence(plan: DecisionPlan, products: ProductRepo, policy: PolicyRepo) -> EvidenceBundle     # ids -> Evidence
# generation/templates.py
def render_claim(spec: ClaimSpec, bundle: EvidenceBundle) -> str
def render_nf(kind: str, params: Mapping[str, str | int]) -> str
def render_option_label(opt: OptionSpec, bundle: EvidenceBundle) -> str
# generation/citation.py
def markers(evidence_ids: Sequence[str], cfg: CitationCfg) -> str
def render_answer_text(plan: DecisionPlan, claim_sentences: Mapping[str, str], bundle: EvidenceBundle, cfg: CitationCfg) -> str
# generation/cards.py
def build_cards(plan: DecisionPlan, bundle: EvidenceBundle, products: ProductRepo, cfg: CardCfg) -> list[ProductCard]
# generation/__init__.py
def generate_response(plan: DecisionPlan, products: ProductRepo, policy: PolicyRepo, cfg: GenCfg) -> tuple[DraftResponse, PlanDigest]
```

### 6.9 Validation (independent)
```python
# validation/validator.py   — imports only schemas, store.repos, validation.*; NOT generation, decision, retrieval, nlp
class Validator:
    def __init__(self, products: ProductRepo, policy: PolicyRepo, cfg: ValidatorCfg) -> None
    def validate(self, draft: DraftResponse, digest: PlanDigest) -> ValidationReport
# validation/rules.py:        def check_v01(...) … def check_v13(...) -> list[Violation]
# validation/scanners.py:     scan_currency, scan_numbers, scan_ids, scan_keywords (own parsers; never imports render helpers)
# validation/constraint_check.py:  def satisfies(product: Product, attrs: Sequence[Attribute], applied: Sequence[ConstraintView], cfg) -> bool     # independent re-implementation
```

### 6.10 Models, metrics, evaluation
```python
# models/base.py
class EmbedderAdapter(Protocol):
    model_id: str; dim: int
    def embed_documents(self, texts: list[str]) -> "np.ndarray"      # float32, L2-normalised (n, dim)
    def embed_query(self, text: str) -> "np.ndarray"                 # (dim,)
    def count_tokens(self, text: str) -> int | None
# models/mock.py:      class MockEmbedder (deterministic hashing embedder)
# models/registry.py:  def load_embedder(settings) -> EmbedderAdapter | None      # None if absent/failing; never raises
# metrics.py:          class Recorder (start, stage(name) context manager, finish() -> Metrics); def rss_mb() -> float | None
# eval/cases.py:       def load_cases(path: Path, fmt: str) -> list[EvalCase]
# eval/official_bridge.py:  class OfficialBridge: def __init__(self, orch: Orchestrator, mapping: OfficialMapping); def answer(self, q: OfficialQuestion) -> OfficialAnswer
# eval/auditor.py:     def audit(response: dict, db_path: Path) -> list[Finding]        # independent; imports nothing from app.generation/decision/validation
# eval/metrics.py:     def compute(results: list[CaseResult]) -> MetricsReport
# eval/report.py:      def render(report: MetricsReport, template_dir: Path) -> str
```
`LLMAdapter` is **not defined in the MVP**. If a post-MVP use is approved (see `IMPLEMENTATION_PLAN §10`), it must be added here first.

## 7. Configuration registry (names only; values tuned per `RAG_PIPELINE §8`)

| File | Keys |
|---|---|
| `config/app.yaml` | `paths.*`, `limits.max_message_chars=500`, `limits.max_body_bytes=16384`, `limits.max_sessions=1000`, `limits.session_ttl_s=1800`, `results.top_k_display=5`, `results.clarify_max_options=5`, `results.related_max=3`, `related_suggestions.enabled`, `search.budget_ms`, `card_fields=[price,stock]`, `citation.product_format`, `citation.policy_format`, `citation.search_marker=SHOW\|HIDE`, `matching.lookup_columns`, `matching.search_columns`, `logging.log_message_text=false`, `cors.origins`, `policy.heading_strategy`, `policy.topic_keywords`, `policy.synonyms` |
| `config/decision_thresholds.yaml` | `term_min_coverage=0.67`, `tau_sem`, `rrf_k=60`, `policy_min_coverage`, `spell_ratio`, `price.under_is_inclusive=false` |
| `config/catalogue_mapping.yaml` | see `DATABASE_SCHEMA §3` |
| `config/models.yaml` | `embedder.*` (path, runtime, query/doc prefixes, doc_template, max_chars) |
| `config/lexicons/` | `codemix_terms.yaml`, `intent_cues.yaml`, `out_of_scope.yaml`, `generic_words.txt`, `stopwords.txt`, `term_synonyms.yaml` |
| `config/` | `attribute_synonyms.yaml` (ingest-time key merging), `category_aliases.yaml` |
