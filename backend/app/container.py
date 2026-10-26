from dataclasses import dataclass
from pathlib import Path
from typing import Any
from app.conversation.store import SessionStore
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.lexical import LexicalIndex
from app.retrieval.policy_retriever import PolicyRetriever
from app.schemas.chat import ReadinessReport
from app.settings import Settings, load_settings
from app.store.db import fts5_available, open_ro
from app.store.policy_repo import PolicyRepo
from app.store.product_repo import CatalogueVocab, ProductRepo
from app.validation.validator import Validator


@dataclass
class Container:
    settings: Settings
    product_repo: ProductRepo
    policy_repo: PolicyRepo
    vocab: CatalogueVocab
    lexical: LexicalIndex
    retriever: HybridRetriever
    policy_retriever: PolicyRetriever
    sessions: SessionStore
    validator: Validator

    def ready(self) -> ReadinessReport:
        db_ok = True
        fts_ok = self.lexical.has_fts
        meta = self.product_repo.meta()
        cat_ok = meta.product_count > 0
        pol_ok = meta.policy_section_count > 0

        checks = {
            "database": db_ok,
            "manifest": True,
            "policy": pol_ok,
            "fts5": fts_ok,
            "vectors": False,
            "embedder": False,
        }
        degraded = []
        if not fts_ok:
            degraded.append("fts5_unavailable")
        degraded.append("vectors_disabled")

        return ReadinessReport(
            ready=db_ok and cat_ok and pol_ok,
            checks=checks,
            degraded=degraded,
        )


def build_container(settings: Settings | None = None) -> Container:
    settings = settings or load_settings()
    db_path = settings.base_dir / settings.app.paths["sqlite_db"]

    conn = open_ro(db_path)
    product_repo = ProductRepo(conn)
    policy_repo = PolicyRepo(conn)
    vocab = product_repo.vocab()
    lexical = LexicalIndex(conn)
    retriever = HybridRetriever(
        product_repo, lexical, settings.thresholds.term_min_coverage
    )
    policy_retriever = PolicyRetriever(policy_repo, settings.thresholds.policy_min_coverage)
    sessions = SessionStore(
        ttl_seconds=settings.app.limits.get("session_ttl_s", 1800),
        max_sessions=settings.app.limits.get("max_sessions", 1000),
    )
    validator = Validator(product_repo, policy_repo)

    return Container(
        settings=settings,
        product_repo=product_repo,
        policy_repo=policy_repo,
        vocab=vocab,
        lexical=lexical,
        retriever=retriever,
        policy_retriever=policy_retriever,
        sessions=sessions,
        validator=validator,
    )
