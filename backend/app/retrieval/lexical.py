import re
import sqlite3
from typing import Sequence
from app.store.db import fts5_available


class LexicalError(Exception):
    pass


class LexicalIndex:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.has_fts = fts5_available(conn)

    def match_terms(self, terms: Sequence[str], columns: Sequence[str] | None = None) -> dict[str, frozenset[str]]:
        """For each term, return the set of product_ids containing that term."""
        results: dict[str, frozenset[str]] = {}
        cur = self.conn.cursor()

        for term in terms:
            clean_term = re.sub(r"[^\w]", "", term).strip().lower()
            if not clean_term:
                continue

            if self.has_fts:
                try:
                    # Escape FTS5 query token
                    fts_query = f'"{clean_term}"'
                    cur.execute(
                        """
                        SELECT p.product_id
                        FROM products_fts f
                        JOIN products p ON p.pid = f.rowid
                        WHERE products_fts MATCH ?
                        """,
                        (fts_query,),
                    )
                    ids = frozenset(r[0] for r in cur.fetchall())
                    results[term] = ids
                    continue
                except Exception:
                    pass

            # Fallback exact substring LIKE over title, description, category, attributes_text
            pattern = f"%{clean_term}%"
            cur.execute(
                """
                SELECT product_id
                FROM products
                WHERE LOWER(title) LIKE ?
                   OR LOWER(description) LIKE ?
                   OR LOWER(category) LIKE ?
                   OR LOWER(attributes_text) LIKE ?
                """,
                (pattern, pattern, pattern, pattern),
            )
            results[term] = frozenset(r[0] for r in cur.fetchall())

        return results

    def rank(self, terms: Sequence[str], allowed: frozenset[str], k: int = 5) -> list[tuple[str, float]]:
        """Return allowed product_ids ranked by BM25 (or term match frequency), top k."""
        if not allowed or not terms:
            return [(pid, 1.0) for pid in list(allowed)[:k]]

        clean_terms = [re.sub(r"[^\w]", "", t).strip().lower() for t in terms if re.sub(r"[^\w]", "", t).strip()]
        if not clean_terms:
            return [(pid, 1.0) for pid in list(allowed)[:k]]

        cur = self.conn.cursor()
        if self.has_fts:
            try:
                fts_expr = " OR ".join(f'"{t}"' for t in clean_terms)
                cur.execute(
                    """
                    SELECT p.product_id, bm25(products_fts, 8.0, 1.0, 3.0, 4.0) as rank_score
                    FROM products_fts f
                    JOIN products p ON p.pid = f.rowid
                    WHERE products_fts MATCH ?
                    ORDER BY rank_score ASC
                    """,
                    (fts_expr,),
                )
                ranked = []
                for pid, score in cur.fetchall():
                    if pid in allowed:
                        # lower bm25 score means better match in sqlite fts5
                        # convert to positive similarity
                        sim = 1.0 / (1.0 + max(0.0, float(score)))
                        ranked.append((pid, sim))
                        if len(ranked) >= k:
                            break
                return ranked
            except Exception:
                pass

        # Fallback scoring
        scored = []
        term_matches = self.match_terms(clean_terms)
        for pid in allowed:
            hit_count = sum(1 for t, pids in term_matches.items() if pid in pids)
            score = float(hit_count)
            scored.append((pid, score))

        scored.sort(key=lambda x: (x[1], x[0]), reverse=True)
        return scored[:k]
