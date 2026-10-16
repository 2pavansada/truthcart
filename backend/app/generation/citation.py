from typing import Mapping, Sequence
from app.domain.plans import DecisionPlan, SegmentClaim, SegmentNF
from app.generation.templates import render_claim, render_nf
from app.schemas.evidence import Evidence


def format_marker(
    evidence_ids: Sequence[str],
    show_search_marker: bool = True,
) -> str:
    tokens = []
    seen = set()

    # 1. Product IDs
    for ev_id in evidence_ids:
        if ev_id.startswith("prod:"):
            parts = ev_id.split(":", 2)
            if len(parts) >= 2:
                pid = parts[1]
                if pid not in seen:
                    tokens.append(pid)
                    seen.add(pid)

    # 2. Policy sections
    for ev_id in evidence_ids:
        if ev_id.startswith("pol:"):
            sec_id = ev_id[4:]
            tok = f"policy:{sec_id}"
            if tok not in seen:
                tokens.append(tok)
                seen.add(tok)

    # 3. Search manifests
    if show_search_marker:
        for ev_id in evidence_ids:
            if ev_id.startswith("search:"):
                s_id = ev_id[7:]
                tok = f"search:{s_id}"
                if tok not in seen:
                    tokens.append(tok)
                    seen.add(tok)

    if not tokens:
        return ""
    return " [" + ", ".join(tokens) + "]"


def render_answer_text(
    plan: DecisionPlan,
    claim_sentences: Mapping[str, str],
    bundle: Mapping[str, Evidence],
    show_search_marker: bool = True,
) -> str:
    segment_texts = []
    claims_by_id = {c.claim_id: c for c in plan.claim_specs}

    for seg in plan.segments:
        if isinstance(seg, SegmentClaim):
            cid = seg.claim_id
            sent = claim_sentences.get(cid, "")
            claim_spec = claims_by_id.get(cid)
            marker = ""
            if claim_spec:
                marker = format_marker(claim_spec.evidence_ids, show_search_marker)
            segment_texts.append(sent + marker)
        elif isinstance(seg, SegmentNF):
            nf_text = render_nf(seg.kind, seg.params)
            if nf_text:
                segment_texts.append(nf_text)

    return " ".join(segment_texts).strip()
