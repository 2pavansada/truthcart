from typing import Sequence
from app.domain.plans import Candidate, EntityResolution


def classify_ties(
    candidates: Sequence[Candidate],
    max_options: int = 5,
    manifest: any = None,
) -> EntityResolution:
    if not candidates:
        return EntityResolution(kind="NONE", candidates=(), manifest=manifest)

    # Filter to candidates that have TITLE_OR_ATTR match
    title_matches = [c for c in candidates if c.match_location == "TITLE_OR_ATTR"]
    if not title_matches:
        desc_matches = [c for c in candidates if c.match_location == "DESCRIPTION_ONLY"]
        if desc_matches:
            return EntityResolution(
                kind="DESCRIPTION_MENTION_ONLY",
                candidates=tuple(desc_matches[:max_options]),
                manifest=manifest,
            )
        return EntityResolution(kind="NONE", candidates=(), manifest=manifest)

    # Sort candidates by term_coverage descending, then fused_score descending, then product_id ascending
    sorted_c = sorted(
        title_matches,
        key=lambda c: (c.term_coverage, c.fused_score, -ord(c.product_id[0]) if c.product_id else 0),
        reverse=True,
    )

    top_cov = sorted_c[0].term_coverage
    # Candidates tied at top coverage (or within 0.05)
    tied = [c for c in sorted_c if abs(c.term_coverage - top_cov) <= 0.05]

    if len(tied) == 1:
        return EntityResolution(
            kind="RESOLVED",
            candidates=(tied[0],),
            manifest=manifest,
        )
    else:
        return EntityResolution(
            kind="AMBIGUOUS",
            candidates=tuple(tied[:max_options]),
            manifest=manifest,
        )
