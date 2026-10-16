# TruthCart AI — The Conversational Shopping Assistant That Doesn't Lie

> **STATUS: DESIGN ONLY.** No application code exists. Nothing in this repository has been implemented, run, tested or benchmarked. Every command below marked *(planned)* is a specification, not a verified instruction. Every item in a "Day 1 verification" list is an unresolved question, not a fact.

## 1. What this is

A grounded shopping assistant over a fixed catalogue of 5,000 products and one plain-text policy document. It answers only from those two sources, cites every factual claim, asks for clarification when the question is ambiguous, and refuses when the evidence does not exist.

The central design rule:

> **The language model never supplies a fact.** Code retrieves and verifies facts. Templates (or, optionally, an instruction model constrained to rephrase them) turn facts into sentences. A validator rejects any sentence whose claims are not backed by evidence retrieved in the same turn.

## 2. Document map

| # | File | Read it for |
|---|---|---|
| 1 | `README.md` | This overview |
| 2 | `docs/architecture.md` | Components, diagrams, stack decisions, degradation ladder, security, requirement traceability, unknowns register (UNK-xx) |
| 3 | `docs/folder_structure.md` | Every directory/file, its responsibility and public interface; frontend specification |
| 4 | `docs/API_SPEC.md` | Endpoints, JSON schemas, validation rules, error codes, examples |
| 5 | `docs/DATABASE_SCHEMA.md` | Canonical product schema, ingestion/validation rules, SQLite DDL, index build, policy sections |
| 6 | `docs/RAG_PIPELINE.md` | Retrieval comparison, the 11-step pipeline with module contracts, ranking, ambiguity, multi-turn state |
| 7 | `docs/GROUNDING_AND_REFUSAL.md` | ANSWER/CLARIFY/REFUSE decision rules, claim types, validator rules, templates, prompt-injection defence |
| 8 | `docs/EVALUATION_PLAN.md` | Official-evaluator integration, dev set, metrics, provisional score, latency/token/cost measurement |
| 9 | `docs/TESTING_PLAN.md` | Test matrix, fixtures, LLM-independent factual tests, offline and malformed-output tests |
| 10 | `docs/IMPLEMENTATION_PLAN.md` | MVP tiers, Day 1 checklist, 7-day plan for four people, integration checkpoints, milestones |

## 3. MVP tiers (mandatory vs optional)

| Tier | Contents | Status |
|---|---|---|
| **Tier 0** | Ingestion, SQLite + FTS5, rule-based query understanding, deterministic filters, decision engine, template responses, validator, chat API, basic UI | **Mandatory** |
| **Tier 1** | Supplied embedding model, semantic retrieval, RRF fusion, ambiguity detection using fused ranking, multi-turn state | **Mandatory** |
| **Tier 2** | Supplied instruction model *rephrases* pre-validated claims; falls back to templates on any validation failure | **Recommended; becomes mandatory if the hackathon rules require using the instruction model (UNK-13)** |
| **Tier 3** | LLM fallback for constraint extraction, compare intent, ordinal references ("the second one"), FAISS/Chroma swap-in, extended Hinglish lexicon | Optional |

The system must be demonstrable at Tier 0 by end of Day 3 (see fallback MVP in `IMPLEMENTATION_PLAN.md`).

## 4. Planned quick start *(planned — unverified)*

```bash
# 1. Put the hackathon files under data/raw/ (never edit them)
python backend/scripts/inspect_inputs.py            # Day 1: report what the files actually look like
# 2. Fill config/catalogue_mapping.yaml from that report
python backend/scripts/build_index.py               # ingest, validate, build SQLite + vectors + manifest
uvicorn app.main:create_app --factory --port 8000   # run from backend/
# Frontend (dev):  cd frontend && npm install && npm run dev
# Frontend (offline demo): npm run build, then FastAPI serves frontend/dist
python backend/scripts/run_eval.py --suite dev      # reproducible evaluation report
pytest backend/tests
```

## 5. Non-negotiable principles

1. Every factual sentence carries ≥1 citation (product ID or policy section) that the validator has checked against this turn's evidence.
2. Prices, stock, ratings and verified attributes come only from SQLite rows. Semantic similarity never establishes a fact.
3. Missing data is reported as unavailable; it is never imputed.
4. Ambiguity → CLARIFY. No evidence → REFUSE (scoped to "the supplied catalogue/policy"). Otherwise ANSWER.
5. Core operation needs no network, no paid API, no cloud service.
6. Hidden test answers are never hard-coded; a CI test enforces this.
7. No feature is described as done until a test or measured run in the repository demonstrates it.

## 6. Handing this to Google Antigravity

Implement incrementally by milestone (M0 → M8 in `IMPLEMENTATION_PLAN.md`). Do not change the architecture or the schemas in `docs/` without recording the change in `docs/DECISIONS.md` and updating every affected document.
