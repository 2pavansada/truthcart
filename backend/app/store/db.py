import sqlite3
from pathlib import Path


def open_ro(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def open_rw(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def fts5_available(conn: sqlite3.Connection) -> bool:
    try:
        cur = conn.cursor()
        cur.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_test USING fts5(c);")
        cur.execute("DROP TABLE IF EXISTS _fts5_test;")
        return True
    except Exception:
        return False


def init_db(conn: sqlite3.Connection) -> None:
    ddl = """
    PRAGMA foreign_keys = ON;

    CREATE TABLE IF NOT EXISTS catalogue_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS products (
        pid INTEGER PRIMARY KEY,
        product_id TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        description TEXT,
        category TEXT,
        category_norm TEXT,
        category_path TEXT,
        price_minor INTEGER CHECK (price_minor IS NULL OR price_minor >= 0),
        currency TEXT,
        rating REAL,
        rating_count INTEGER CHECK (rating_count IS NULL OR rating_count >= 0),
        stock_status TEXT NOT NULL CHECK (stock_status IN ('IN_STOCK','OUT_OF_STOCK','UNKNOWN')),
        stock_quantity INTEGER,
        suspicious_text INTEGER NOT NULL DEFAULT 0,
        attributes_text TEXT NOT NULL DEFAULT '',
        source_row INTEGER NOT NULL,
        content_hash TEXT NOT NULL,
        raw_json TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_norm);
    CREATE INDEX IF NOT EXISTS idx_products_price ON products(price_minor);
    CREATE INDEX IF NOT EXISTS idx_products_stock ON products(stock_status);
    CREATE INDEX IF NOT EXISTS idx_products_rating ON products(rating);

    CREATE TABLE IF NOT EXISTS product_categories (
        product_id TEXT NOT NULL REFERENCES products(product_id),
        level INTEGER NOT NULL,
        name_norm TEXT NOT NULL,
        PRIMARY KEY (product_id, level)
    );
    CREATE INDEX IF NOT EXISTS idx_pc_name ON product_categories(name_norm);

    CREATE TABLE IF NOT EXISTS product_attributes (
        product_id TEXT NOT NULL REFERENCES products(product_id),
        key_raw TEXT NOT NULL,
        key_norm TEXT NOT NULL,
        value_raw TEXT NOT NULL,
        value_norm TEXT NOT NULL,
        value_num REAL,
        unit TEXT,
        PRIMARY KEY (product_id, key_norm, value_norm)
    );
    CREATE INDEX IF NOT EXISTS idx_attr_kv ON product_attributes(key_norm, value_norm);
    CREATE INDEX IF NOT EXISTS idx_attr_v ON product_attributes(value_norm);

    CREATE TABLE IF NOT EXISTS field_issues (
        product_id TEXT NOT NULL,
        field TEXT NOT NULL,
        code TEXT NOT NULL,
        detail TEXT,
        PRIMARY KEY (product_id, field, code)
    );

    CREATE TABLE IF NOT EXISTS quarantine (
        q_id INTEGER PRIMARY KEY,
        source_row INTEGER,
        product_id_raw TEXT,
        reason_code TEXT NOT NULL,
        detail TEXT,
        raw_json TEXT
    );

    CREATE TABLE IF NOT EXISTS policy_sections (
        section_id TEXT PRIMARY KEY,
        ordinal INTEGER NOT NULL UNIQUE,
        level INTEGER NOT NULL,
        heading TEXT NOT NULL,
        parent_id TEXT REFERENCES policy_sections(section_id),
        body TEXT NOT NULL,
        topic TEXT,
        char_start INTEGER NOT NULL,
        char_end INTEGER NOT NULL,
        content_hash TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS build_runs (
        run_id TEXT PRIMARY KEY,
        started_at TEXT,
        finished_at TEXT,
        durations_json TEXT,
        counts_json TEXT
    );
    """
    conn.executescript(ddl)

    # Initialize FTS tables if available
    try:
        conn.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS products_fts USING fts5(
            title, description, category, attributes_text,
            tokenize = 'porter unicode61 remove_diacritics 2'
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS policy_fts USING fts5(
            heading, body,
            tokenize = 'porter unicode61 remove_diacritics 2'
        );
        """)
    except Exception as e:
        print(f"Warning: FTS5 virtual tables could not be created: {e}")
    conn.commit()
