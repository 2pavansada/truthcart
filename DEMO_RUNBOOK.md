# TruthCart AI — Three-Minute Demo Runbook

The timings below target an approximately three-minute walkthrough. No official hackathon presentation limit or complete judging rubric has been provided, so adjust pacing to the event instructions when they are confirmed.

## Before the demo

1. Build the frontend with `npm run build` from `frontend/`.
2. From `backend/`, build the local fixture with `python scripts/build_index.py` and start the app with `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`.
3. Open `http://127.0.0.1:8000` and confirm the header says **Online**.
4. Click **New Chat** before starting. Keep the browser at a readable zoom and avoid using browser autofill.
5. Confirm that the audience understands this is a **31-product demo catalogue with 11 policy sections**, not the planned 5,000-product catalogue.
6. Open the six-slide deck on the presentation machine and advance through all six slides once. Confirm that the viewer, projector, or shared-screen path works before the audience arrives.

**Core message:** TruthCart keeps earlier filters active on follow-up turns and marks missing catalogue evidence as missing instead of guessing.

**Presenter wording:** Describe these as behaviors observed on the current demo fixture, not a universal guarantee. Avoid presenting the interface slogans “No Hallucinations” or “Doesn't Lie” as proof that every answer is error-free.

**Playback check on the checked machine:** PowerPoint showed an unlicensed/read-and-print-only notice, and slideshow playback was not verified. Confirm playback in the viewer selected for the event; do not discover this at the podium.

## Deck playback rehearsal

This 90-second pass is a rehearsal target only; no official event limit has been provided. Start the timer on slide 1, advance manually, and rehearse returning to the browser without losing the prepared tab.

The deck and live walkthrough below are separate rehearsal blocks: about 4 minutes 30 seconds together, plus the screen handoff. This is a preparation target, not an assumed presentation limit.

| Time | Slide | Narration cue |
|---|---|---|
| 0:00–0:15 | 1 — Product answers with evidence attached | “This is a local demo using 31 products and 11 policy sections.” |
| 0:15–0:30 | 2 — Failure modes | “We focus on unsupported facts and constraint drift across follow-up questions.” |
| 0:30–0:45 | 3 — Evidence-first answer path | “The system parses, retrieves, enforces hard filters, then answers, clarifies, or refuses.” |
| 0:45–1:00 | 4 — Constraints persist | “The follow-up keeps the budget, footwear category, and black colour while adding in-stock.” Introduce the live reproduction. |
| 1:00–1:15 | 5 — Trust behaviors | “Product and policy claims have evidence; tied matches clarify; unsupported orders are refused.” Briefly point to the dated evaluation snapshot and its two ordinary-path known gaps. |
| 1:15–1:30 | 6 — Scope | “The 5,000-product catalogue is planned, not this demo. We make no latency or token-cost claim.” |

## Live interface walkthrough

Rehearse this as a separate three-minute block. Keep one request in flight at a time and narrate only after the visible response arrives.

| Time | Action and expected result | Narration |
|---|---|---|
| 0:00–0:12 | Start in **New Chat**. Set scope: 31 demo products and 11 policy sections. | “I’ll show a filter carried across turns, then a case where the source has no answer.” |
| 0:12–0:37 | Send `Show me black shoes under 2000`. Expect five catalogue matches with IDs, prices, and stock labels. | “The first answer includes the catalogue’s different stock states.” |
| 0:37–1:00 | Send `Only in stock` in the same chat. Expect SH-BLK-001, SH-BLK-002, SH-BLK-003; footwear, black, and under ₹2,000 stay active. Out-of-stock and unknown-stock items are excluded. | “The follow-up adds in-stock without dropping the budget, category, or colour. Unknown stock does not count as in stock.” |
| 1:00–1:12 | Open **View Evidence** on RoadRun Men’s Black Running Shoes. | “The product ID and displayed fields come from this catalogue record.” |
| 1:12–1:36 | Click **New Chat**. Send `What is the price of SH-NOP-001?`. Expect `FIELD MISSING`; open evidence if time permits and point out price is unavailable. | “The product is known, but its price is not in the source. TruthCart reports the gap instead of guessing.” |
| 1:36–1:52 | Click **New Chat**. Send `What is the return policy`. Expect citations `[policy:1.1]` and `[policy:1.3]`, no product cards. | “Policy answers point to the supplied policy sections.” |
| 1:52–2:14 | Click **New Chat**. Send `What is the price of the blue running shoe?`. Expect `CLARIFY / AMBIGUOUS_PRODUCT` with three grounded options and no chosen price. | “It asks which product the shopper means rather than picking one.” |
| 2:14–2:34 | In that chat, send `Please place an order for SH-BLK-001`. Expect `REFUSE / UNSUPPORTED_REQUEST`, no product cards. | “It does not pretend to place an order.” |
| 2:34–3:00 | Stop sending requests. Close with the current implementation limits. | “This is local, lexical, and template-based. The 5,000-product catalogue is planned. We have no verified latency or token-cost result.” |

**If a response runs late:** protect the follow-up filter and missing-evidence examples. Skip the live policy, ambiguity, and order turns; slide 5 summarizes those observed behaviors. Do not rush or narrate a result before it appears.

## Backup talking points

- **Price protection:** Prices are catalogue fields checked against evidence; the 2026-10-18 evaluation run reported zero price-hallucination findings. This is evidence from that run, not a universal guarantee.
- **Hard filtering:** The budget-and-stock follow-up retains the earlier filters and excludes both out-of-stock and unknown-stock products. The case is covered by the real orchestrator evaluation path.
- **Grounded policy:** The return-policy response cites the supplied sections; it is not generated from an external policy service.
- **Safe uncertainty:** Tied product matches cause clarification. Unsupported order actions are refused without inventing a completed action. A live smoke check also returned `FIELD MISSING` for SH-NOP-001’s price and CL-JNS-001’s warranty; those are verified observations, not promises about every request.
- **Current limits:** 31 demo products and 11 policy sections; lexical retrieval and template generation; no semantic retrieval, order workflow, payment integration, or measured latency/token cost.
- **Evaluation snapshot (2026-10-18):** 290 backend tests passed; 30/30 active evaluation cases passed; two ordinary-path known gaps remain visible; G-AC-01 passed 17/17; G-AC-03 recorded zero validator trips; price-hallucination and critical-grounding gates passed.

## If the live UI misbehaves

1. If a response has not appeared after about 10 seconds, stop and wait once. Do not resend the prompt or stack more turns while the first request may still be running.
2. If the header says **Online**, use **New Chat** and retry that exact prompt once. If the header is offline or the retry stalls, stop the live demo and switch to the deck; do not spend the presentation troubleshooting.
3. On Slide 4, explain the previously observed result: five black-shoe matches under ₹2,000 became three after “Only in stock,” while the category, colour, and budget remained active. On Slide 5, summarize the verified policy citation, ambiguity clarification, and order refusal behaviors.
4. For the missing-evidence example, say explicitly: “In the earlier smoke check, asking for SH-NOP-001’s price returned FIELD MISSING; I’m reporting that recorded result, not showing a live response.” If useful, add that its evidence drawer marked price unavailable. Do not pass off a saved result as live or a screenshot as current UI output.
5. Do not infer a missing price, attribute, or stock status; do not claim an order was placed. If the slides also fail, deliver the same two points verbally: earlier filters stayed active, and missing evidence was reported as missing.

## Evidence sources

- `backend/eval/dev_set/cases.json` — active budget-and-stock carry-over and ambiguity cases.
- `backend/app/orchestrator.py` — request pipeline and state carry-over.
- `backend/app/retrieval/lexical.py` and `backend/app/store/product_repo.py` — lexical membership and trusted hard-filter path.
- `backend/app/validation/validator.py` and `backend/eval/auditor.py` — response validation and independent evaluation audit.
- `IMPLEMENTATION_STATUS.md` — current dated verification results.
