# TruthCart AI

TruthCart is a local shopping-assistant prototype. It answers product and policy questions from a supplied demo catalogue and policy file, carries supported filters across turns, and asks or refuses when the request cannot be answered safely.

## Current demo scope

- **31 products** in the current demo catalogue and **11 policy sections**. The planned 5,000-product catalogue is not the current dataset.
- Lexical SQLite/FTS retrieval, rule-based query parsing, deterministic constraint handling, and template-generated responses.
- Product facts and policy answers include source evidence. Product filters are enforced before result cards are returned.
- Optional local signup/login uses a separate auth database; product shopping remains available to guests.
- The application runs locally and does not require a paid API. Package installation may require network access.
- Semantic retrieval, the official production catalogue, order placement, and payment processing are not implemented.

The latest verified checks are recorded in [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md). On 2026-10-18, the backend suite passed **310 tests**; the evaluation runner passed **30/30 active cases**, with **2 ordinary-path known gaps** still reported. The frontend suite passed **8 tests** and its production build succeeded. No latency or token-cost benchmark is claimed.

## Run the demo on Windows

Build the frontend first so FastAPI can serve its production bundle:

```powershell
Set-Location frontend
npm ci
npm run build
```

In a second PowerShell window, install and start the backend from the repository root:

```powershell
Set-Location backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts/build_index.py
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. The API health endpoint is <http://127.0.0.1:8000/api/health> and readiness is <http://127.0.0.1:8000/api/ready>.

For the exact judge-facing sequence, expected outcomes, and backup talking points, see [DEMO_RUNBOOK.md](DEMO_RUNBOOK.md). The editable six-slide deck is [presentation/TruthCart_Hackathon_Pitch.pptx](presentation/TruthCart_Hackathon_Pitch.pptx).

## Run checks

From `backend/`:

```powershell
python -m pytest -q
python -m eval.runner --suite dev
```

The evaluation report distinguishes active cases from known gaps. The requirement-coverage audit is separate: complete branch auditing needs run-scoped pytest evidence paired with the matching evaluation run ID. See `backend/eval/requirement_coverage.py` and the latest audit in `IMPLEMENTATION_STATUS.md`.

## Source documents

- [CONTRACTS.md](CONTRACTS.md) — product, conversation, and response contracts.
- [GROUNDING_AND_REFUSAL.md](GROUNDING_AND_REFUSAL.md) — grounding, clarification, refusal, and validator requirements.
- [EVALUATION_PLAN.md](EVALUATION_PLAN.md) and [TESTING_PLAN.md](TESTING_PLAN.md) — evaluation and test approach.
- [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) — dated implementation and verification evidence.
