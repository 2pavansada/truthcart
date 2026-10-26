"""Catalogue + policy ingestion (DATABASE_SCHEMA.md §4, §9).

Reliable, reproducible load of the supported source formats into the existing
SQLite schema:

  * raw catalogue  -> JSONL, one object per record (``config/catalogue_mapping.yaml``)
  * policy         -> plain text with numbered ``N.M`` headings

Design guarantees (never invent values, DATABASE_SCHEMA §0/§4):
  * identity errors (missing/invalid id or title, unreadable row) -> quarantine the row;
  * field errors (bad price / rating / stock / category)          -> NULL the field + field_issue, keep row;
  * unknown price is stored as NULL, never 0;
  * duplicate ids: identical content keeps one + logs DUPLICATE_EXACT_DROPPED,
    conflicting content quarantines all rows with that id;
  * different ids with identical title+category are near-duplicates -> all kept, reported.

The build is atomic: it writes into a sibling temporary database and only
replaces the real one after a successful commit, so a failed import can never
leave the development database partially corrupted.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.ingest.field_parsers import parse_attributes, parse_price, parse_rating, parse_stock
from app.ingest.policy_parser import coverage_ratio, parse_policy
from app.ingest.sanitizer import clean_text, is_suspicious
from app.settings import Settings
from app.store.db import fts5_available, init_db, open_rw


@dataclass
class QuarantineRow:
    source_row: int
    product_id_raw: Any
    reason_code: str
    detail: str
    raw_json: str


@dataclass
class FieldIssue:
    product_id: str
    field: str
    code: str
    detail: str


@dataclass
class IngestResult:
    """Validated, normalized records plus the buckets that did not survive validation."""

    products: list[dict[str, Any]] = field(default_factory=list)
    quarantined: list[QuarantineRow] = field(default_factory=list)
    field_issues: list[FieldIssue] = field(default_factory=list)
    processed: int = 0
    duplicates_identical: int = 0
    duplicates_conflict: int = 0
    near_duplicate_groups: list[list[str]] = field(default_factory=list)

    @property
    def imported(self) -> int:
        return len(self.products)

    @property
    def rejected(self) -> int:
        return len(self.quarantined)

    def summary(self) -> dict[str, Any]:
        return {
            "processed": self.processed,
            "imported": self.imported,
            "rejected": self.rejected,
            "quarantined": len(self.quarantined),
            "duplicates_identical": self.duplicates_identical,
            "duplicates_conflict": self.duplicates_conflict,
            "field_issues": len(self.field_issues),
            "near_duplicate_groups": len(self.near_duplicate_groups),
        }


# --------------------------------------------------------------------------- reading
def read_raw_records(path: Path) -> list[tuple[int, str]]:
    """Read non-blank source lines as (dense_record_no, raw_line). Blank lines are skipped."""
    records: list[tuple[int, str]] = []
    row_no = 1
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue
            records.append((row_no, line_str))
            row_no += 1
    return records


# --------------------------------------------------------------------------- validation
def _title_key(title: str, category_norm: str | None) -> tuple[str, str]:
    norm = re.sub(r"\s+", " ", (title or "").strip().lower())
    return norm, (category_norm or "")


def ingest_records(raw_records: list[tuple[int, str]], settings: Settings) -> IngestResult:
    """Validate + normalize every raw record into products / quarantine / field issues.

    This is a pure function: it performs no database writes, so it can be tested in
    isolation against malformed, duplicate and mixed input.
    """
    result = IngestResult()
    mapping = settings.catalogue_mapping
    id_regex = re.compile(mapping.get("product_id_regex", r"^[A-Za-z0-9_.-]{1,40}$"))
    price_cfg = mapping.get("price")
    stock_map = mapping.get("stock", {}).get("value_map")
    rating_cfg = mapping.get("rating", {})
    synonyms = settings.attribute_synonyms

    # product_id -> currently-kept product dict (before conflict resolution)
    kept: dict[str, dict[str, Any]] = {}
    conflict_ids: set[str] = set()

    for row_no, raw_str in raw_records:
        result.processed += 1

        # 1. Row must be readable JSON object -> otherwise UNPARSEABLE_ROW.
        try:
            rec = json.loads(raw_str)
        except (json.JSONDecodeError, ValueError):
            result.quarantined.append(
                QuarantineRow(row_no, None, "UNPARSEABLE_ROW", "Line is not valid JSON", raw_str)
            )
            continue
        if not isinstance(rec, dict):
            result.quarantined.append(
                QuarantineRow(row_no, None, "UNPARSEABLE_ROW", "Record is not a JSON object", raw_str)
            )
            continue

        # 2. Identity validation -> quarantine.
        pid_raw = rec.get("product_id")
        if not pid_raw or not str(pid_raw).strip():
            result.quarantined.append(
                QuarantineRow(row_no, pid_raw, "MISSING_ID", "Product ID is missing", raw_str)
            )
            continue
        pid = str(pid_raw).strip()
        if ":" in pid or not id_regex.match(pid):
            result.quarantined.append(
                QuarantineRow(row_no, pid_raw, "INVALID_ID_FORMAT",
                              "Product ID failed regex or contains ':'", raw_str)
            )
            continue

        title_raw = rec.get("title")
        if not title_raw or not str(title_raw).strip():
            result.quarantined.append(
                QuarantineRow(row_no, pid, "MISSING_TITLE", "Title is empty", raw_str)
            )
            continue
        title = clean_text(str(title_raw))

        # 3. Field normalization -> NULL + field_issue, never a fabricated value.
        description = clean_text(str(rec["description"])) if rec.get("description") else None
        if not description:
            result.field_issues.append(FieldIssue(pid, "description", "DESCRIPTION_MISSING", "Description is null"))

        category = clean_text(str(rec["category"])) if rec.get("category") else None
        category_norm = category.lower().strip() if category else None
        if not category:
            result.field_issues.append(FieldIssue(pid, "category", "CATEGORY_MISSING", "Category is null"))

        price_minor, price_issue = parse_price(rec.get("price"), price_cfg)
        if price_issue:
            result.field_issues.append(FieldIssue(pid, "price", price_issue, f"Price error: {price_issue}"))
        currency = rec.get("currency") or (price_cfg or {}).get("assume_currency", "INR")

        rating, rating_issue = parse_rating(
            rec.get("rating"),
            float(rating_cfg.get("min", 0.0)),
            float(rating_cfg.get("max", 5.0)),
        )
        if rating_issue:
            result.field_issues.append(FieldIssue(pid, "rating", rating_issue, f"Rating error: {rating_issue}"))

        rating_count = None
        if rec.get("rating_count") is not None:
            try:
                rc = int(rec["rating_count"])
                if rc >= 0:
                    rating_count = rc
            except (ValueError, TypeError):
                pass

        stock_status, stock_issue = parse_stock(rec.get("stock"), stock_map)
        if stock_issue:
            result.field_issues.append(FieldIssue(pid, "stock", stock_issue, f"Stock error: {stock_issue}"))

        attrs = parse_attributes(rec.get("attributes"), synonyms)
        attrs_text = "; ".join(f"{a['key_raw']}: {a['value_raw']}" for a in attrs)

        suspicious = 1 if (is_suspicious(title) or is_suspicious(description) or is_suspicious(attrs_text)) else 0

        # Duplicate detection must cover the full source record. Hashing only a
        # subset lets conflicting descriptions, ratings, or other stored fields
        # pass as exact duplicates and silently keeps whichever row appeared first.
        canonical_record = json.dumps(
            rec,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        content_hash = hashlib.sha256(canonical_record.encode("utf-8")).hexdigest()

        prod_obj = {
            "product_id": pid,
            "title": title,
            "description": description,
            "category": category,
            "category_norm": category_norm,
            "category_path": None,
            "price_minor": price_minor,
            "currency": currency,
            "rating": rating,
            "rating_count": rating_count,
            "stock_status": stock_status.value,
            "stock_quantity": None,
            "suspicious_text": suspicious,
            "attributes_text": attrs_text,
            "source_row": row_no,
            "content_hash": content_hash,
            "raw_json": raw_str,
            "attributes": attrs,
        }

        # 4. Duplicate policy (DATABASE_SCHEMA §4.3).
        if pid in conflict_ids:
            # id already known-conflicting -> quarantine every further row too
            result.quarantined.append(
                QuarantineRow(row_no, pid, "DUPLICATE_ID_CONFLICT", "Conflicting content for same ID", raw_str)
            )
            continue
        prior = kept.get(pid)
        if prior is not None:
            if prior["content_hash"] == content_hash:
                result.duplicates_identical += 1  # keep the first (lowest source_row)
                continue
            conflict_ids.add(pid)
            result.duplicates_conflict += 1
            # quarantine BOTH the previously kept row and this one
            result.quarantined.append(
                QuarantineRow(prior["source_row"], pid, "DUPLICATE_ID_CONFLICT",
                              "Conflicting content for same ID", prior["raw_json"])
            )
            result.quarantined.append(
                QuarantineRow(row_no, pid, "DUPLICATE_ID_CONFLICT",
                              "Conflicting content for same ID", raw_str)
            )
            del kept[pid]
            continue

        kept[pid] = prod_obj

    # Deterministic order + stable pid assignment by sorted product_id.
    result.products = sorted(kept.values(), key=lambda p: p["product_id"])
    for idx, p in enumerate(result.products, start=1):
        p["pid"] = idx

    # Near-duplicate groups: different id, identical title + category (kept, reported only).
    groups: dict[tuple[str, str], list[str]] = {}
    for p in result.products:
        groups.setdefault(_title_key(p["title"], p["category_norm"]), []).append(p["product_id"])
    result.near_duplicate_groups = [sorted(ids) for ids in groups.values() if len(ids) > 1]

    return result


# --------------------------------------------------------------------------- writing
def write_catalogue(conn, result: IngestResult) -> None:
    """Insert quarantine, field issues, products, categories and attributes. Does NOT commit."""
    cur = conn.cursor()
    for q in result.quarantined:
        cur.execute(
            "INSERT INTO quarantine (source_row, product_id_raw, reason_code, detail, raw_json) VALUES (?, ?, ?, ?, ?)",
            (q.source_row, str(q.product_id_raw) if q.product_id_raw is not None else None,
             q.reason_code, q.detail, q.raw_json),
        )
    for fi in result.field_issues:
        cur.execute(
            "INSERT OR IGNORE INTO field_issues (product_id, field, code, detail) VALUES (?, ?, ?, ?)",
            (fi.product_id, fi.field, fi.code, fi.detail),
        )
    for p in result.products:
        cur.execute(
            """
            INSERT INTO products (
                pid, product_id, title, description, category, category_norm, category_path,
                price_minor, currency, rating, rating_count, stock_status, stock_quantity,
                suspicious_text, attributes_text, source_row, content_hash, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                p["pid"], p["product_id"], p["title"], p["description"], p["category"], p["category_norm"],
                p["category_path"], p["price_minor"], p["currency"], p["rating"], p["rating_count"],
                p["stock_status"], p["stock_quantity"], p["suspicious_text"], p["attributes_text"],
                p["source_row"], p["content_hash"], p["raw_json"],
            ),
        )
        if p["category_norm"]:
            cur.execute(
                "INSERT OR IGNORE INTO product_categories (product_id, level, name_norm) VALUES (?, 0, ?)",
                (p["product_id"], p["category_norm"]),
            )
        for attr in p["attributes"]:
            cur.execute(
                """
                INSERT OR IGNORE INTO product_attributes (
                    product_id, key_raw, key_norm, value_raw, value_norm, value_num, unit
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    p["product_id"], attr["key_raw"], attr["key_norm"],
                    attr["value_raw"], attr["value_norm"], attr["value_num"], attr["unit"],
                ),
            )


def index_products_fts(conn, result: IngestResult, fts_ok: bool) -> None:
    """(Re)build the products_fts index from the ingested rows. Does NOT commit."""
    if not fts_ok:
        return
    cur = conn.cursor()
    cur.execute("DELETE FROM products_fts")
    for p in result.products:
        cur.execute(
            "INSERT INTO products_fts (rowid, title, description, category, attributes_text) VALUES (?, ?, ?, ?, ?)",
            (p["pid"], p["title"], p["description"] or "", p["category"] or "", p["attributes_text"]),
        )


def ingest_policy(conn, settings: Settings, fts_ok: bool) -> tuple[list[dict[str, Any]], float]:
    """Parse and insert policy sections (+ policy_fts). Does NOT commit. Returns (sections, coverage)."""
    pol_path = settings.base_dir / settings.app.paths["policy"]
    with open(pol_path, "r", encoding="utf-8") as f:
        policy_text = f.read()

    sections = parse_policy(policy_text, settings.app.policy)
    cov = coverage_ratio(policy_text, sections)
    if cov < 0.99:
        print(f"Warning: policy coverage is {cov:.3f} (< 0.99)")

    cur = conn.cursor()
    if fts_ok:
        cur.execute("DELETE FROM policy_fts")
    for sec in sections:
        cur.execute(
            """
            INSERT INTO policy_sections (
                section_id, ordinal, level, heading, parent_id, body, topic,
                char_start, char_end, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sec["section_id"], sec["ordinal"], sec["level"], sec["heading"],
                sec["parent_id"], sec["body"], sec["topic"],
                sec["char_start"], sec["char_end"], sec["content_hash"],
            ),
        )
        if fts_ok:
            cur.execute(
                "INSERT INTO policy_fts (rowid, heading, body) VALUES (?, ?, ?)",
                (sec["ordinal"], sec["heading"], sec["body"]),
            )
    return sections, cov


def _write_meta_and_counts(conn, product_count: int, quarantined_count: int,
                           policy_count: int) -> tuple[str, str]:
    built_at = datetime.now(timezone.utc).isoformat()
    manifest_hash = hashlib.sha256(
        f"{product_count}:{quarantined_count}:{policy_count}:{built_at}".encode("utf-8")
    ).hexdigest()
    fts_backend = "FTS5" if fts5_available(conn) else "PY_BM25"
    meta_entries = {
        "product_count": str(product_count),
        "quarantined_count": str(quarantined_count),
        "policy_section_count": str(policy_count),
        "built_at": built_at,
        "manifest_hash": manifest_hash,
        "lexical_backend": fts_backend,
        "semantic_available": "false",
    }
    cur = conn.cursor()
    for k, v in meta_entries.items():
        cur.execute("INSERT OR REPLACE INTO catalogue_meta (key, value) VALUES (?, ?)", (k, v))
    return built_at, manifest_hash


def _dump_quarantine(path: Path, result: IngestResult) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for q in result.quarantined:
            f.write(json.dumps({
                "source_row": q.source_row,
                "product_id_raw": q.product_id_raw,
                "reason_code": q.reason_code,
                "detail": q.detail,
                "raw_json": q.raw_json,
            }) + "\n")


# --------------------------------------------------------------------------- orchestration
def build_all(settings: Settings) -> dict[str, Any]:
    """Full reproducible build. Atomic: writes a temp DB, then replaces the target on success.

    A failure at any stage leaves the previous database untouched.
    """
    t0 = time.time()
    durations: dict[str, float] = {}

    cat_path = settings.base_dir / settings.app.paths["raw_catalogue"]
    db_path = settings.base_dir / settings.app.paths["sqlite_db"]
    manifest_path = settings.base_dir / settings.app.paths["manifest"]
    report_path = settings.base_dir / settings.app.paths["build_report"]
    quarantine_path = db_path.parent / "quarantine.jsonl"

    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Build into a sibling temp DB so the existing good database survives any failure.
    tmp_path = db_path.with_name(f".{db_path.name}.tmp.{os.getpid()}")
    if tmp_path.exists():
        tmp_path.unlink()

    conn = open_rw(tmp_path)
    try:
        init_db(conn)
        fts_ok = fts5_available(conn)

        t_read = time.time()
        raw_records = read_raw_records(cat_path)
        durations["read"] = round((time.time() - t_read) * 1000, 2)

        t_val = time.time()
        result = ingest_records(raw_records, settings)
        durations["validate"] = round((time.time() - t_val) * 1000, 2)

        # All writes run inside one implicit transaction; commit happens only at the end.
        write_catalogue(conn, result)
        index_products_fts(conn, result, fts_ok)
        sections, _cov = ingest_policy(conn, settings, fts_ok)
        built_at, manifest_hash = _write_meta_and_counts(
            conn, result.imported, len(result.quarantined), len(sections)
        )
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        if tmp_path.exists():
            tmp_path.unlink()
        raise
    else:
        conn.close()
        os.replace(tmp_path, db_path)  # atomic promotion

    _dump_quarantine(quarantine_path, result)

    total_duration = round((time.time() - t0) * 1000, 2)

    manifest_data = {
        "schema_version": 1,
        "built_at_utc": built_at,
        "manifest_hash": manifest_hash,
        "counts": {
            "products": result.imported,
            "quarantined": len(result.quarantined),
            "policy_sections": len(sections),
        },
        "lexical_backend": "FTS5" if fts_ok else "PY_BM25",
        "semantic_available": False,
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    report_data = {
        "durations_ms": durations,
        "total_ms": total_duration,
        "counts": manifest_data["counts"],
        "summary": result.summary(),
        "quarantine_summary": [
            {"row": q.source_row, "id": q.product_id_raw, "code": q.reason_code, "detail": q.detail}
            for q in result.quarantined
        ],
        "near_duplicate_groups": result.near_duplicate_groups,
        "field_issues_count": len(result.field_issues),
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)

    return report_data
