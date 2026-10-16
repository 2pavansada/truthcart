import hashlib
import re
from typing import Any

_NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$")


def parse_policy(text: str, cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    lines = text.splitlines()
    sections = []
    ordinal = 1
    current_sec: dict[str, Any] | None = None
    body_lines: list[str] = []

    topic_keywords = {
        "returns": ["return", "refund", "exchange"],
        "shipping": ["shipping", "delivery", "dispatch", "cancellation"],
        "warranty": ["warranty", "guarantee", "defect"],
    }
    if cfg and "topic_keywords" in cfg:
        topic_keywords = cfg["topic_keywords"]

    def assign_topic(heading: str, body: str) -> str | None:
        combined = (heading + " " + body).lower()
        for top, kws in topic_keywords.items():
            if any(kw in combined for kw in kws):
                return top
        return None

    def flush_section():
        nonlocal current_sec, body_lines, ordinal
        if current_sec:
            body = "\n".join(body_lines).strip()
            current_sec["body"] = body
            current_sec["topic"] = assign_topic(current_sec["heading"], body)
            sec_text = current_sec["heading"] + "\n" + body
            current_sec["content_hash"] = hashlib.sha256(sec_text.encode("utf-8")).hexdigest()
            sections.append(current_sec)
            current_sec = None
            body_lines = []

    pos = 0
    for line in lines:
        stripped = line.strip()
        m = _NUMBERED_HEADING.match(stripped)
        if m:
            flush_section()
            sec_num = m.group(1)
            heading_text = m.group(2).strip()
            level = len(sec_num.split("."))
            parent_id = None
            if level > 1:
                parts = sec_num.split(".")
                parent_id = ".".join(parts[:-1])

            char_start = pos
            current_sec = {
                "section_id": sec_num,
                "ordinal": ordinal,
                "level": level,
                "heading": heading_text,
                "parent_id": parent_id,
                "char_start": char_start,
                "char_end": char_start + len(line),
            }
            ordinal += 1
        elif current_sec:
            body_lines.append(line)
        pos += len(line) + 1  # newline

    flush_section()

    # Fallback if no numbered headings matched: create a single root section
    if not sections and stripped:
        sections.append({
            "section_id": "S01",
            "ordinal": 1,
            "level": 1,
            "heading": "General Store Policy",
            "parent_id": None,
            "body": text.strip(),
            "topic": assign_topic("General Store Policy", text),
            "char_start": 0,
            "char_end": len(text),
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        })

    return sections


def coverage_ratio(text: str, sections: list[dict[str, Any]]) -> float:
    text_non_ws = len(re.sub(r"\s+", "", text))
    if text_non_ws == 0:
        return 1.0

    covered_text = "".join(s["heading"] + s["body"] for s in sections)
    covered_non_ws = len(re.sub(r"\s+", "", covered_text))
    return min(1.0, covered_non_ws / text_non_ws)
