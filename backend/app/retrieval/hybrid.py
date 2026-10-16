from typing import Sequence
from app.domain.constraints import ConstraintOp, ConstraintSet
from app.domain.enums import CardOrigin, Completeness, Intent, OpKind
from app.domain.plans import (
    Candidate,
    EntityResolution,
    IdLookupResult,
    SearchResult,
    SearchSpec,
)
from app.retrieval.ambiguity import classify_ties
from app.retrieval.attribute_verifier import verify_attributes
from app.retrieval.lexical import LexicalIndex
from app.retrieval.relaxation import analyze_relaxations
from app.schemas.chat import ConstraintOpIn, ConstraintView
from app.schemas.evidence import CatalogueSearchManifest
from app.store.product_repo import ProductRepo


class HybridRetriever:
    def __init__(self, product_repo: ProductRepo, lexical_index: LexicalIndex):
        self.product_repo = product_repo
        self.lexical_index = lexical_index

    def _constraints_to_views(self, c: ConstraintSet) -> list[ConstraintView]:
        views = []
        if c.categories:
            for cat in sorted(c.categories):
                views.append(ConstraintView(
                    key="category",
                    label=f"Category: {cat.capitalize()}",
                    op_to_remove=ConstraintOpIn(op="REMOVE", key="category", value=cat),
                ))
        if c.price_min:
            rupees = c.price_min.amount_minor / 100.0
            views.append(ConstraintView(
                key="price_min",
                label=f"Price above ₹{rupees:,.2f}",
                op_to_remove=ConstraintOpIn(op="REMOVE", key="price_min"),
            ))
        if c.price_max:
            rupees = c.price_max.amount_minor / 100.0
            views.append(ConstraintView(
                key="price_max",
                label=f"Price under ₹{rupees:,.2f}",
                op_to_remove=ConstraintOpIn(op="REMOVE", key="price_max"),
            ))
        if c.stock:
            views.append(ConstraintView(
                key="stock",
                label="In stock only" if c.stock.value == "IN_STOCK" else "Out of stock only",
                op_to_remove=ConstraintOpIn(op="REMOVE", key="stock"),
            ))
        if c.rating_min:
            views.append(ConstraintView(
                key="rating_min",
                label=f"Rated ≥ {c.rating_min}★",
                op_to_remove=ConstraintOpIn(op="REMOVE", key="rating_min"),
            ))
        for a in c.attributes:
            label = f"{a.key}: {a.value}" if a.key else a.value
            views.append(ConstraintView(
                key="attribute",
                label=label,
                op_to_remove=ConstraintOpIn(op="REMOVE", key="attribute", value={"key": a.key, "value": a.value}),
            ))
        return views

    def search_catalogue(self, spec: SearchSpec, turn_id: int) -> SearchResult:
        c = spec.constraints

        # 1. SQL hard filters over all rows
        eligible = self.product_repo.eligible_ids(c)

        # 2. Attribute verification for every eligible row
        exact_ids = []
        candidate_matches: dict[str, Any] = {}
        products_map = self.product_repo.get_many(eligible.ids)

        for pid in eligible.ids:
            prod = products_map.get(pid)
            if not prod:
                continue
            attrs = self.product_repo.attributes(pid)
            res = verify_attributes(prod, attrs, c.attributes)
            if res.ok:
                exact_ids.append(pid)
                candidate_matches[pid] = res.matches

        # 3. Lexical terms matching if terms exist
        terms = list(c.terms) or list(spec.entity_terms)
        if terms and exact_ids:
            # Rank exact matches by BM25
            ranked_scores = self.lexical_index.rank(terms, frozenset(exact_ids), k=spec.top_k)
        else:
            # Deterministic order by pid
            ranked_scores = [(pid, 1.0) for pid in sorted(exact_ids)[:spec.top_k]]

        ranked_candidates = []
        for pid, score in ranked_scores:
            prod = products_map[pid]
            # Match location check
            loc = "TITLE_OR_ATTR"
            ranked_candidates.append(Candidate(
                product_id=pid,
                term_coverage=1.0,
                match_location=loc,
                fused_score=score,
                attr_matches=candidate_matches.get(pid, ()),
            ))

        # 4. Relaxations analysis if exact matches are 0
        relaxations = []
        if len(exact_ids) == 0:
            def check_verify(p_id, atts):
                p = self.product_repo.get(p_id)
                if not p:
                    return False
                return verify_attributes(p, self.product_repo.attributes(p_id), atts).ok

            relaxations = analyze_relaxations(spec, self.product_repo, check_verify)

        manifest = CatalogueSearchManifest(
            search_id=f"s_{turn_id}",
            scope="CATALOGUE",
            intent=spec.intent,
            query_text=" ".join(terms) if terms else "all",
            lookup_ids=[],
            applied_constraints=self._constraints_to_views(c),
            term_requirements=terms,
            term_min_coverage=0.67,
            completeness=Completeness.EXHAUSTIVE,
            incomplete_reasons=[],
            eligible_count=len(eligible.ids),
            exact_match_count=len(exact_ids),
            tied_count=None,
            returned_count=len(ranked_candidates),
            excluded_unknown_stock=eligible.excluded_unknown_stock,
            relaxations=relaxations,
            ranking={"lexical": True, "semantic": False},
            related_count=0,
        )

        return SearchResult(
            manifest=manifest,
            exact_ids=tuple(sorted(exact_ids)),
            ranked=tuple(ranked_candidates),
            related=(),
        )

    def lookup_ids(self, ids: Sequence[str], turn_id: int) -> IdLookupResult:
        found = self.product_repo.get_many(ids)
        missing = [pid for pid in ids if pid not in found]

        manifest = CatalogueSearchManifest(
            search_id=f"s_{turn_id}",
            scope="CATALOGUE",
            intent=Intent.PRODUCT_FACT,
            query_text=", ".join(ids),
            lookup_ids=list(ids),
            applied_constraints=[],
            term_requirements=[],
            term_min_coverage=1.0,
            completeness=Completeness.EXHAUSTIVE,
            incomplete_reasons=[],
            eligible_count=len(found),
            exact_match_count=len(found),
            tied_count=None,
            returned_count=len(found),
            excluded_unknown_stock=0,
            relaxations=[],
            ranking={"lexical": False, "semantic": False},
            related_count=0,
        )

        return IdLookupResult(found=found, missing=missing, manifest=manifest)

    def resolve_entity(
        self,
        intent: Intent,
        entity_terms: Sequence[str],
        constraints: ConstraintSet,
        turn_id: int,
    ) -> EntityResolution:
        spec = SearchSpec(
            intent=intent,
            constraints=constraints,
            entity_terms=tuple(entity_terms),
            top_k=5,
        )
        search_res = self.search_catalogue(spec, turn_id)
        return classify_ties(search_res.ranked, max_options=5, manifest=search_res.manifest)
