import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.domain.enums import StockStatus
from app.ingest.field_parsers import parse_attributes, parse_price, parse_rating, parse_stock
from app.ingest.policy_parser import coverage_ratio, parse_policy
from app.ingest.sanitizer import clean_text, is_suspicious
from app.settings import Settings
from app.store.db import fts5_available, init_db, open_rw


def build_all(settings: Settings) -> dict[str, Any]:
    t0 = time.time()
    durations = {}

    cat_path = settings.base_dir / settings.app.paths["raw_catalogue"]
    pol_path = settings.base_dir / settings.app.paths["policy"]
    db_path = settings.base_dir / settings.app.paths["sqlite_db"]
    manifest_path = settings.base_dir / settings.app.paths["manifest"]
    report_path = settings.base_dir / settings.app.paths["build_report"]

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    conn = open_rw(db_path)
    init_db(conn)

    # 1. Read catalogue records
    t_read_start = time.time()
    raw_records: list[tuple[int, dict[str, Any], str]] = []
    with open(cat_path, "r", encoding="utf-8") as f:
        row_no = 1
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue
            record = json.loads(line_str)
            raw_records.append((row_no, record, line_str))
            row_no += 1
    durations["read"] = round((time.time() - t_read_start) * 1000, 2)

    # 2. Map and validate records
    id_regex = re.compile(settings.catalogue_mapping.get("product_id_regex", r"^[A-Za-z0-9_.-]{1,40}$"))
    quarantined = []
    field_issues = []
    parsed_products = []
    seen_ids: dict[str, dict[str, Any]] = {}

    for row_no, rec, raw_str in raw_records:
        pid_raw = rec.get("product_id")
        if not pid_raw:
            quarantined.append((row_no, pid_raw, "MISSING_ID", "Product ID is missing", raw_str))
            continue

        pid = str(pid_raw).strip()
        if ":" in pid or not id_regex.match(pid):
            quarantined.append((row_no, pid_raw, "INVALID_ID_FORMAT", "Product ID failed regex or contains ':'", raw_str))
            continue

        title_raw = rec.get("title")
        if not title_raw or not str(title_raw).strip():
            quarantined.append((row_no, pid, "MISSING_TITLE", "Title is empty", raw_str))
            continue
        title = clean_text(str(title_raw))

        desc_raw = rec.get("description")
        description = clean_text(str(desc_raw)) if desc_raw else None
        if not description:
            field_issues.append((pid, "description", "DESCRIPTION_MISSING", "Description is null"))

        cat_raw = rec.get("category")
        category = clean_text(str(cat_raw)) if cat_raw else None
        category_norm = category.lower().strip() if category else None
        if not category:
            field_issues.append((pid, "category", "CATEGORY_MISSING", "Category is null"))

        price_minor, price_issue = parse_price(rec.get("price"), settings.catalogue_mapping.get("price"))
        if price_issue:
            field_issues.append((pid, "price", price_issue, f"Price error: {price_issue}"))

        currency = rec.get("currency") or settings.catalogue_mapping.get("price", {}).get("assume_currency", "INR")

        rating, rating_issue = parse_rating(rec.get("rating"))
        if rating_issue:
            field_issues.append((pid, "rating", rating_issue, f"Rating error: {rating_issue}"))

        rating_count = None
        if rec.get("rating_count") is not None:
            try:
                rc = int(rec["rating_count"])
                if rc >= 0:
                    rating_count = rc
            except ValueError:
                pass

        stock_status, stock_issue = parse_stock(rec.get("stock"), settings.catalogue_mapping.get("stock", {}).get("value_map"))
        if stock_issue:
            field_issues.append((pid, "stock", stock_issue, f"Stock error: {stock_issue}"))

        attrs = parse_attributes(rec.get("attributes"), settings.attribute_synonyms)
        attrs_text = "; ".join(f"{a['key_raw']}: {a['value_raw']}" for a in attrs)

        suspicious = 1 if (is_suspicious(title) or is_suspicious(description) or is_suspicious(attrs_text)) else 0

        content_digest_str = f"{pid}|{title}|{category}|{price_minor}|{currency}|{stock_status.value}|{attrs_text}"
        content_hash = hashlib.sha256(content_digest_str.encode("utf-8")).hexdigest()

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

        # Deduplication check
        if pid in seen_ids:
            prev = seen_ids[pid]
            if prev["content_hash"] == content_hash:
                # identical exact duplicate, skip
                continue
            else:
                # conflicting duplicate -> quarantine both
                quarantined.append((row_no, pid, "DUPLICATE_ID_CONFLICT", "Different content for same ID", raw_str))
                quarantined.append((prev["source_row"], pid, "DUPLICATE_ID_CONFLICT", "Different content for same ID", prev["raw_json"]))
                parsed_products = [p for p in parsed_products if p["product_id"] != pid]
                continue

        seen_ids[pid] = prod_obj
        parsed_products.append(prod_obj)

    # Sort products deterministically by product_id and assign pid
    parsed_products.sort(key=lambda p: p["product_id"])
    for idx, p in enumerate(parsed_products, start=1):
        p["pid"] = idx

    # 3. Insert products into SQLite
    cur = conn.cursor()
    for q in quarantined:
        cur.execute(
            "INSERT INTO quarantine (source_row, product_id_raw, reason_code, detail, raw_json) VALUES (?, ?, ?, ?, ?)",
            (q[0], q[1], q[2], q[3], q[4]),
        )

    for fi in field_issues:
        cur.execute(
            "INSERT OR IGNORE INTO field_issues (product_id, field, code, detail) VALUES (?, ?, ?, ?)",
            fi,
        )

    for p in parsed_products:
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

    # Populate products_fts
    fts_ok = fts5_available(conn)
    if fts_ok:
        for p in parsed_products:
            cur.execute(
                "INSERT INTO products_fts (rowid, title, description, category, attributes_text) VALUES (?, ?, ?, ?, ?)",
                (p["pid"], p["title"], p["description"] or "", p["category"] or "", p["attributes_text"]),
            )

    # 4. Ingest Policy
    with open(pol_path, "r", encoding="utf-8") as f:
        policy_text = f.read()

    policy_sections = parse_policy(policy_text, settings.app.policy)
    cov = coverage_ratio(policy_text, policy_sections)
    if cov < 0.99:
        print(f"Warning: policy coverage is {cov:.3f} (< 0.99)")

    for sec in policy_sections:
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

    # 5. Populate catalogue_meta
    built_at = datetime.now(timezone.utc).isoformat()
    manifest_hash = hashlib.sha256(
        f"{len(parsed_products)}:{len(quarantined)}:{len(policy_sections)}:{built_at}".encode("utf-8")
    ).hexdigest()

    meta_entries = {
        "product_count": str(len(parsed_products)),
        "quarantined_count": str(len(quarantined)),
        "policy_section_count": str(len(policy_sections)),
        "built_at": built_at,
        "manifest_hash": manifest_hash,
        "lexical_backend": "FTS5" if fts_ok else "PY_BM25",
        "semantic_available": "false",
    }
    for k, v in meta_entries.items():
        cur.execute("INSERT OR REPLACE INTO catalogue_meta (key, value) VALUES (?, ?)", (k, v))

    conn.commit()
    conn.close()

    total_duration = round((time.time() - t0) * 1000, 2)

    # Build manifest and report files
    manifest_data = {
        "schema_version": 1,
        "built_at_utc": built_at,
        "manifest_hash": manifest_hash,
        "counts": {
            "products": len(parsed_products),
            "quarantined": len(quarantined),
            "policy_sections": len(policy_sections),
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
        "quarantine_summary": [
            {"row": q[0], "id": q[1], "code": q[2], "detail": q[3]} for q in quarantined
        ],
        "field_issues_count": len(field_issues),
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)

    return report_data
