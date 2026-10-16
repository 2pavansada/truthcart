import re
from typing import Sequence
from app.domain.enums import Completeness
from app.domain.plans import PolicyResult, PolicySection
from app.schemas.evidence import PolicySearchManifest
from app.store.policy_repo import PolicyRepo


class PolicyRetriever:
    def __init__(self, repo: PolicyRepo, min_coverage: float = 0.50):
        self.repo = repo
        self.min_coverage = min_coverage

    def search(self, question_terms: Sequence[str], topic: str | None, turn_id: int) -> PolicyResult:
        all_sections = self.repo.list_sections()
        sections_checked = [s.section_id for s in all_sections]

        clean_terms = [t.lower().strip(".,;:?!'\"") for t in question_terms if len(t.strip(".,;:?!'\"")) > 2]

        matched_sections: list[tuple[PolicySection, float]] = []

        for sec in all_sections:
            sec_text = (sec.heading + " " + sec.body).lower()

            if topic and sec.topic and sec.topic == topic:
                # boost/prioritize topic matches
                topic_bonus = 0.5
            else:
                topic_bonus = 0.0

            if not clean_terms:
                if topic and sec.topic == topic:
                    matched_sections.append((sec, 1.0))
                continue

            hits = sum(1 for t in clean_terms if re.search(r"\b" + re.escape(t) + r"\b", sec_text))
            cov = (hits / len(clean_terms)) + topic_bonus
            if cov >= self.min_coverage:
                matched_sections.append((sec, cov))

        matched_sections.sort(key=lambda x: x[1], reverse=True)
        best_cov = matched_sections[0][1] if matched_sections else None
        established = len(matched_sections) > 0 and (best_cov or 0) >= self.min_coverage

        matched_ids = [s[0].section_id for s in matched_sections]

        manifest = PolicySearchManifest(
            search_id=f"p_{turn_id}",
            scope="POLICY",
            query_terms=clean_terms,
            topic=topic,
            sections_checked=sections_checked,
            min_coverage=self.min_coverage,
            best_coverage=best_cov,
            matched_section_ids=matched_ids,
            completeness=Completeness.EXHAUSTIVE,
            incomplete_reasons=[],
        )

        return PolicyResult(
            manifest=manifest,
            sections=tuple(s[0] for s in matched_sections),
            established=established,
        )
