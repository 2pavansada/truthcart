from dataclasses import dataclass, field
import json
import sqlite3
from typing import Any, Iterable
from app.domain.constraints import ConstraintSet
from app.domain.enums import StockStatus
from app.schemas.product import CatalogueMeta


@dataclass(frozen=True)
class Product:
    pid: int
    product_id: str
    title: str
    description: str | None
    category: str | None
    category_norm: str | None
    category_path: str | None
    price_minor: int | None
    currency: str | None
    rating: float | None
    rating_count: int | None
    stock_status: StockStatus
    stock_quantity: int | None
    suspicious_text: int
    attributes_text: str
    source_row: int
    content_hash: str
    raw_json: str

    @property
    def price_display(self) -> str | None:
        if self.price_minor is None:
            return None
        rupees = self.price_minor / 100.0
        # Format with Indian grouping if desired or standard comma
        curr = "₹" if self.currency == "INR" else f"{self.currency or ''} "
        return f"{curr}{rupees:,.2f}"

    @property
    def stock_label(self) -> str:
        if self.stock_status == StockStatus.IN_STOCK:
            return "In stock"
        elif self.stock_status == StockStatus.OUT_OF_STOCK:
            return "Out of stock"
        return "Stock status not available"


@dataclass(frozen=True)
class EligibleSet:
    ids: frozenset[str]
    excluded_unknown_stock: int = 0


@dataclass(frozen=True)
class CatalogueVocab:
    categories: frozenset[str]
    attr_values_by_key: dict[str, frozenset[str]]
    generic_words: frozenset[str] = field(default_factory=frozenset)
    title_tokens: frozenset[str] = field(default_factory=frozenset)

    def has_term(self, t: str) -> bool:
        t_low = t.lower()
        return (
            t_low in self.categories
            or t_low in self.title_tokens
            or any(t_low in vals for vals in self.attr_values_by_key.values())
        )


class ProductRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def _row_to_product(self, row: sqlite3.Row) -> Product:
        return Product(
            pid=row["pid"],
            product_id=row["product_id"],
            title=row["title"],
            description=row["description"],
            category=row["category"],
            category_norm=row["category_norm"],
            category_path=row["category_path"],
            price_minor=row["price_minor"],
            currency=row["currency"],
            rating=row["rating"],
            rating_count=row["rating_count"],
            stock_status=StockStatus(row["stock_status"]),
            stock_quantity=row["stock_quantity"],
            suspicious_text=row["suspicious_text"],
            attributes_text=row["attributes_text"],
            source_row=row["source_row"],
            content_hash=row["content_hash"],
            raw_json=row["raw_json"],
        )

    def get(self, product_id: str) -> Product | None:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM products WHERE product_id = ?", (product_id,))
        row = cur.fetchone()
        return self._row_to_product(row) if row else None

    def get_many(self, ids: Iterable[str]) -> dict[str, Product]:
        id_list = list(ids)
        if not id_list:
            return {}
        placeholders = ",".join(["?"] * len(id_list))
        cur = self.conn.cursor()
        cur.execute(f"SELECT * FROM products WHERE product_id IN ({placeholders})", id_list)
        rows = cur.fetchall()
        return {r["product_id"]: self._row_to_product(r) for r in rows}

    def attributes(self, product_id: str) -> list[dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT key_raw, key_norm, value_raw, value_norm, value_num, unit
            FROM product_attributes WHERE product_id = ?
            """,
            (product_id,),
        )
        return [dict(r) for r in cur.fetchall()]

    def eligible_ids(self, c: ConstraintSet) -> EligibleSet:
        """SQL evaluation over ALL products for hard constraints."""
        where_clauses = []
        params = []

        if c.categories:
            placeholders = ",".join(["?"] * len(c.categories))
            where_clauses.append(f"category_norm IN ({placeholders})")
            params.extend(c.categories)

        if c.price_min:
            op = ">=" if c.price_min.inclusive else ">"
            where_clauses.append(f"price_minor {op} ?")
            params.append(c.price_min.amount_minor)

        if c.price_max:
            op = "<=" if c.price_max.inclusive else "<"
            # Explicitly exclude NULL-price products: NULL compared to any value is NULL (falsy),
            # but we make the intent clear and prevent regressions if SQLite behaviour changes.
            where_clauses.append(f"price_minor IS NOT NULL AND price_minor {op} ?")
            params.append(c.price_max.amount_minor)

        if c.rating_min is not None:
            where_clauses.append("rating >= ?")
            params.append(c.rating_min)

        # Build base filter without stock condition to compute excluded_unknown_stock if needed
        base_where = " AND ".join(where_clauses) if where_clauses else "1=1"

        excluded_unknown = 0
        if c.stock == StockStatus.IN_STOCK:
            # count unknown
            cur = self.conn.cursor()
            cur.execute(
                f"SELECT COUNT(*) FROM products WHERE {base_where} AND stock_status = 'UNKNOWN'",
                params,
            )
            excluded_unknown = cur.fetchone()[0]

            where_clauses.append("stock_status = 'IN_STOCK'")
        elif c.stock == StockStatus.OUT_OF_STOCK:
            where_clauses.append("stock_status = 'OUT_OF_STOCK'")

        final_where = " AND ".join(where_clauses) if where_clauses else "1=1"
        cur = self.conn.cursor()
        cur.execute(f"SELECT product_id FROM products WHERE {final_where}", params)
        rows = cur.fetchall()
        eligible = frozenset(r[0] for r in rows)
        return EligibleSet(ids=eligible, excluded_unknown_stock=excluded_unknown)

    def vocab(self) -> CatalogueVocab:
        cur = self.conn.cursor()
        cur.execute("SELECT DISTINCT category_norm FROM products WHERE category_norm IS NOT NULL")
        cats = frozenset(r[0] for r in cur.fetchall())

        cur.execute("SELECT DISTINCT key_norm, value_norm FROM product_attributes")
        attrs_map: dict[str, set[str]] = {}
        for k, v in cur.fetchall():
            attrs_map.setdefault(k, set()).add(v)

        cur.execute("SELECT title FROM products")
        tokens = set()
        for (t,) in cur.fetchall():
            for tok in t.lower().split():
                clean_tok = tok.strip(".,;:()[]{}\"'")
                if len(clean_tok) > 2:
                    tokens.add(clean_tok)

        return CatalogueVocab(
            categories=cats,
            attr_values_by_key={k: frozenset(v) for k, v in attrs_map.items()},
            title_tokens=frozenset(tokens),
        )

    def meta(self) -> CatalogueMeta:
        cur = self.conn.cursor()
        cur.execute("SELECT key, value FROM catalogue_meta")
        meta_dict = dict(cur.fetchall())

        cur.execute("SELECT category, COUNT(*) FROM products GROUP BY category")
        cat_counts = [{"name": r[0] or "Uncategorized", "count": r[1]} for r in cur.fetchall()]

        cur.execute("SELECT MIN(price_minor), MAX(price_minor), currency FROM products WHERE price_minor IS NOT NULL")
        p_row = cur.fetchone()
        price_range = {
            "min_minor": p_row[0] if p_row else None,
            "max_minor": p_row[1] if p_row else None,
            "currency": p_row[2] if p_row else "INR",
        }

        cur.execute("SELECT stock_status, COUNT(*) FROM products GROUP BY stock_status")
        stock_dist = {r[0]: r[1] for r in cur.fetchall()}

        return CatalogueMeta(
            product_count=int(meta_dict.get("product_count", 0)),
            quarantined_count=int(meta_dict.get("quarantined_count", 0)),
            field_issue_counts={},
            categories=cat_counts,
            price_range=price_range,
            stock_distribution=stock_dist,
            policy_section_count=int(meta_dict.get("policy_section_count", 0)),
            built_at=meta_dict.get("built_at", ""),
            manifest_hash=meta_dict.get("manifest_hash", ""),
            embedder=None,
            embedding_dim=None,
            semantic_available=False,
            lexical_backend=meta_dict.get("lexical_backend", "FTS5"),
        )
