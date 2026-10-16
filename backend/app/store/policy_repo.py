import sqlite3
from app.domain.plans import PolicySection


class PolicyRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def _row_to_section(self, row: sqlite3.Row) -> PolicySection:
        return PolicySection(
            section_id=row["section_id"],
            ordinal=row["ordinal"],
            level=row["level"],
            heading=row["heading"],
            parent_id=row["parent_id"],
            body=row["body"],
            topic=row["topic"],
            char_start=row["char_start"],
            char_end=row["char_end"],
            content_hash=row["content_hash"],
        )

    def list_sections(self) -> list[PolicySection]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM policy_sections ORDER BY ordinal ASC")
        return [self._row_to_section(r) for r in cur.fetchall()]

    def get_section(self, section_id: str) -> PolicySection | None:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM policy_sections WHERE section_id = ?", (section_id,))
        row = cur.fetchone()
        return self._row_to_section(row) if row else None
