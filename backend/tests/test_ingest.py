"""
Focused tests for the catalogue-ingestion module (DATABASE_SCHEMA.md §4/§9).

Everything runs against temporary SQLite files under pytest's ``tmp_path`` so the
real development database (``data/build/truthcart.sqlite``) is never touched.
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
from pathlib import Path

import pytest

from app.domain.constraints import ConstraintSet, PriceBound
from app.domain.enums import StockStatus
from app.ingest.build_pipeline import (
    IngestResult,
    build_all,
    ingest_policy,
    ingest_records,
    read_raw_records,
    write_catalogue,
    index_products_fts,
)
from app.settings import load_settings
from app.store.db import fts5_available, init_db, open_ro, open_rw
from app.store.product_repo import ProductRepo


@pytest.fixture(scope="module")
def settings():
    return load_settings()


def _rec(**fields) -> tuple[int, str]:
    return fields.pop("_row", 1), json.dumps(fields)


def _records(*objs: dict) -> list[tuple[int, str]]:
    return [(i + 1, json.dumps(o)) for i, o in enumerate(objs)]


def _valid(pid="P-1", price="1499.00", **extra) -> dict:
    base = {
        "product_id": pid,
        "title": "A Test Product",
        "description": "A description.",
        "category": "Footwear",
        "price": price,
        "currency": "INR",
        "rating": 4.3,
        "rating_count": 10,
        "stock": "IN_STOCK",
        "attributes": {"color": "black"},
    }
    base.update(extra)
    return base


def _fresh_db(tmp_path: Path) -> sqlite3.Connection:
    conn = open_rw(tmp_path / "db.sqlite")
    init_db(conn)
    return conn


# --------------------------------------------------------------------------- valid
class TestValidRecords:
    def test_imports_and_normalizes(self, settings):
        res = ingest_records(_records(_valid()), settings)
        assert res.imported == 1
        assert res.processed == 1
        p = res.products[0]
        assert p["price_minor"] == 149900           # normalised to minor units, never raw
        assert p["category_norm"] == "footwear"      # lowercased
        assert p["stock_status"] == "IN_STOCK"
        assert p["attributes"][0]["key_norm"] == "color"

    def test_pid_assigned_deterministically(self, settings):
        recs = _records(_valid(pid="Z-9"), _valid(pid="A-1"))
        res = ingest_records(recs, settings)
        assert [p["product_id"] for p in res.products] == ["A-1", "Z-9"]
        assert [p["pid"] for p in res.products] == [1, 2]


# --------------------------------------------------------------------------- required fields
class TestMissingRequiredFields:
    def test_missing_id_quarantined(self, settings):
        recs = [(1, json.dumps({"title": "No id"}))]
        res = ingest_records(recs, settings)
        assert res.imported == 0
        assert res.quarantined[0].reason_code == "MISSING_ID"

    def test_missing_title_quarantined(self, settings):
        recs = [(1, json.dumps({"product_id": "P-1", "title": "   "}))]
        res = ingest_records(recs, settings)
        assert res.imported == 0
        assert res.quarantined[0].reason_code == "MISSING_TITLE"

    def test_invalid_id_format_quarantined(self, settings):
        recs = [(1, json.dumps({"product_id": "bad id!!", "title": "X"}))]
        res = ingest_records(recs, settings)
        assert res.quarantined[0].reason_code == "INVALID_ID_FORMAT"


# --------------------------------------------------------------------------- prices
class TestPrices:
    def test_invalid_price_keeps_row_as_null(self, settings):
        res = ingest_records(_records(_valid(price="abc")), settings)
        assert res.imported == 1
        assert res.products[0]["price_minor"] is None
        codes = [f.code for f in res.field_issues if f.field == "price"]
        assert "PRICE_UNPARSEABLE" in codes

    def test_unknown_price_never_zero(self, settings):
        res = ingest_records(_records(_valid(price=None)), settings)
        assert res.imported == 1
        assert res.products[0]["price_minor"] is None  # never coerced to 0

    def test_zero_price_rejected_as_issue(self, settings):
        res = ingest_records(_records(_valid(price="0")), settings)
        assert res.products[0]["price_minor"] is None
        assert any(f.code == "PRICE_ZERO" for f in res.field_issues)


# --------------------------------------------------------------------------- stock / rating
class TestStockAndRating:
    def test_unrecognized_stock_becomes_unknown(self, settings):
        res = ingest_records(_records(_valid(stock="maybe")), settings)
        assert res.products[0]["stock_status"] == "UNKNOWN"
        assert any(f.code == "STOCK_UNRECOGNIZED" for f in res.field_issues)

    def test_out_of_range_rating_null(self, settings):
        res = ingest_records(_records(_valid(rating=9.9)), settings)
        assert res.products[0]["rating"] is None
        assert any(f.code == "RATING_OUT_OF_RANGE" for f in res.field_issues)


# --------------------------------------------------------------------------- duplicates
class TestDuplicates:
    def test_identical_duplicate_kept_one_and_counted(self, settings):
        recs = _records(_valid(pid="D-1"), _valid(pid="D-1"))
        res = ingest_records(recs, settings)
        assert res.imported == 1
        assert res.duplicates_identical == 1
        assert res.duplicates_conflict == 0

    @pytest.mark.parametrize(
        "changed_field,changed_value",
        [
            ("description", "A conflicting description."),
            ("rating", 3.1),
            ("rating_count", 11),
        ],
    )
    def test_conflicting_stored_fields_quarantine_duplicate_ids(
        self, settings, changed_field, changed_value
    ):
        first = _valid(pid="D-1")
        second = {**first, changed_field: changed_value}

        result = ingest_records(_records(first, second), settings)

        assert result.imported == 0
        assert result.duplicates_identical == 0
        assert result.duplicates_conflict == 1
        assert [row.reason_code for row in result.quarantined] == [
            "DUPLICATE_ID_CONFLICT",
            "DUPLICATE_ID_CONFLICT",
        ]
        assert {row.source_row for row in result.quarantined} == {1, 2}

    def test_identical_record_with_different_key_order_is_still_deduplicated(self, settings):
        record = _valid(pid="D-1")
        reordered = dict(reversed(list(record.items())))
        result = ingest_records(_records(record, reordered), settings)

        assert result.imported == 1
        assert result.duplicates_identical == 1
        assert result.duplicates_conflict == 0

    def test_conflicting_duplicate_quarantines_all(self, settings):
        recs = _records(_valid(pid="D-1", price="100.00"), _valid(pid="D-1", price="200.00"))
        res = ingest_records(recs, settings)
        assert res.imported == 0
        assert res.duplicates_conflict == 1
        assert all(q.reason_code == "DUPLICATE_ID_CONFLICT" for q in res.quarantined)
        assert len(res.quarantined) == 2

    def test_third_conflicting_row_also_quarantined(self, settings):
        recs = _records(
            _valid(pid="D-1", price="100.00"),
            _valid(pid="D-1", price="200.00"),
            _valid(pid="D-1", price="100.00"),
        )
        res = ingest_records(recs, settings)
        assert res.imported == 0
        assert len(res.quarantined) == 3


# --------------------------------------------------------------------------- malformed / empty
class TestMalformedAndEmpty:
    def test_malformed_json_line_quarantined_not_crash(self, settings):
        recs = [(1, "{not valid json"), (2, json.dumps(_valid(pid="OK-1")))]
        res = ingest_records(recs, settings)          # must not raise
        assert res.imported == 1
        assert any(q.reason_code == "UNPARSEABLE_ROW" for q in res.quarantined)

    def test_non_object_json_quarantined(self, settings):
        recs = [(1, json.dumps(["just", "a", "list"]))]
        res = ingest_records(recs, settings)
        assert res.imported == 0
        assert res.quarantined[0].reason_code == "UNPARSEABLE_ROW"

    def test_empty_input(self, settings):
        res = ingest_records([], settings)
        assert res.processed == 0 and res.imported == 0 and res.rejected == 0

    def test_mixed_valid_invalid_buckets(self, settings):
        recs = [
            (1, "{bad json"),
            (2, json.dumps({"title": "no id"})),
            (3, json.dumps(_valid(pid="GOOD-1", price="nope"))),
        ]
        res = ingest_records(recs, settings)
        assert res.imported == 1 and res.rejected == 2
        assert res.products[0]["price_minor"] is None


# --------------------------------------------------------------------------- near duplicates
class TestNearDuplicates:
    def test_different_id_same_title_category_reported(self, settings):
        recs = _records(_valid(pid="A-1"), _valid(pid="B-2"))
        res = ingest_records(recs, settings)
        assert res.imported == 2
        assert res.near_duplicate_groups == [["A-1", "B-2"]]


# --------------------------------------------------------------------------- writes / rollback
class TestWritesAndRollback:
    def test_write_then_rollback_leaves_no_partial_rows(self, tmp_path, settings):
        conn = _fresh_db(tmp_path)
        # Baseline id sorts last, so the colliding batch inserts NEW-* first.
        baseline = ingest_records(_records(_valid(pid="Z-9")), settings)
        write_catalogue(conn, baseline)
        index_products_fts(conn, baseline, fts5_available(conn))
        conn.commit()

        # Batch that succeeds on NEW-1/NEW-2 then collides on Z-9 (existing product_id/pid).
        colliding = ingest_records(
            _records(_valid(pid="NEW-1"), _valid(pid="NEW-2"), _valid(pid="Z-9")),
            settings,
        )
        with pytest.raises(sqlite3.IntegrityError):
            write_catalogue(conn, colliding)
        conn.rollback()
        cur = conn.execute("SELECT product_id FROM products")
        assert {row[0] for row in cur.fetchall()} == {"Z-9"}  # NEW-* rolled back too
        conn.close()

    def test_failed_build_preserves_previous_db(self, tmp_path, settings):
        # Build once successfully into the redirected target.
        s2 = _redirected(settings, tmp_path)
        report = build_all(s2)
        db_path = tmp_path / "db.sqlite"
        assert db_path.exists()
        before = db_path.read_bytes()

        # Now point at a catalogue whose policy file is missing -> build raises,
        # but the previous DB must be untouched (atomic promotion).
        bad_paths = dict(s2.app.paths)
        bad_paths["raw_catalogue"] = "data/raw/does_not_exist.jsonl"
        s3 = dataclasses.replace(s2, app=dataclasses.replace(s2.app, paths=bad_paths))
        with pytest.raises(Exception):
            build_all(s3)
        assert db_path.read_bytes() == before


# --------------------------------------------------------------------------- index + filtering
class TestIndexAndFiltering:
    def test_built_db_is_searchable_and_filters(self, tmp_path, settings):
        s2 = _redirected(settings, tmp_path)
        report = build_all(s2)
        assert report["counts"]["products"] == 31
        assert report["summary"]["quarantined"] == 0

        conn = open_ro(tmp_path / "db.sqlite")
        repo = ProductRepo(conn)

        # Product lookup + FTS index count parity.
        p = repo.get("SH-BLK-001")
        assert p is not None and p.price_minor == 149900
        fts_count = conn.execute("SELECT COUNT(*) FROM products_fts").fetchone()[0]
        assert fts_count == 31

        # Budget + stock hard filter (regression of inherited behaviour):
        # every eligible id must be IN_STOCK and strictly under 200000, and the
        # null-price product must be excluded (unknown price is never treated as budget).
        elig = repo.eligible_ids(
            ConstraintSet(
                categories=frozenset({"footwear"}),
                price_max=PriceBound(amount_minor=200000, inclusive=False),
                stock=StockStatus.IN_STOCK,
            )
        ).ids
        assert elig
        for pid in elig:
            prod = repo.get(pid)
            assert prod.stock_status == StockStatus.IN_STOCK
            assert prod.price_minor is not None and prod.price_minor < 200000
        assert "SH-NOP-001" not in elig       # null price excluded
        assert "SH-BLK-004" not in elig        # out of stock excluded
        assert "SH-BLK-005" not in elig        # 2899.00 > cap excluded
        conn.close()

    def test_repeated_build_is_stable(self, tmp_path, settings):
        s2 = _redirected(settings, tmp_path)
        r1 = build_all(s2)
        conn = open_ro(tmp_path / "db.sqlite")
        first = {row[0]: row[1] for row in conn.execute("SELECT product_id, pid FROM products")}
        conn.close()
        r2 = build_all(s2)
        conn = open_ro(tmp_path / "db.sqlite")
        second = {row[0]: row[1] for row in conn.execute("SELECT product_id, pid FROM products")}
        conn.close()
        assert r1["counts"] == r2["counts"]
        assert first == second   # deterministic product_id -> pid mapping across rebuilds


# --------------------------------------------------------------------------- policy ingestion
class TestPolicyIngestion:
    def test_sections_and_citation_ids(self, tmp_path, settings):
        conn = _fresh_db(tmp_path)
        fts_ok = fts5_available(conn)
        sections, cov = ingest_policy(conn, settings, fts_ok)
        conn.commit()
        assert len(sections) == 11
        assert cov > 0.9
        ids = {s["section_id"] for s in sections}
        assert "1.1" in ids   # citation format pol:1.1 stays valid
        row = conn.execute("SELECT heading, body FROM policy_sections WHERE section_id='1.1'").fetchone()
        assert row is not None and "30 days" in row["body"]
        if fts_ok:
            assert conn.execute("SELECT COUNT(*) FROM policy_fts").fetchone()[0] == 11
        conn.close()


# --------------------------------------------------------------------------- helpers
def _redirected(settings, tmp_path: Path):
    """Return settings writing DB/manifest/report into ``tmp_path`` (reads real raw inputs)."""
    paths = dict(settings.app.paths)
    paths.update({
        "sqlite_db": str(tmp_path / "db.sqlite"),
        "manifest": str(tmp_path / "manifest.json"),
        "build_report": str(tmp_path / "report.json"),
    })
    return dataclasses.replace(settings, app=dataclasses.replace(settings.app, paths=paths))
