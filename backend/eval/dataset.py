"""Test-set adapter (EVALUATION_PLAN.md §3).

Defines the evaluation case schema and a loader for the team-owned dev set.
Cases are decoupled from the runtime Pydantic models: decisions and reasons are
stored as plain strings so the dataset never depends on app internals.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class HardFilters:
    """Applicable hard filters an ANSWER's returned products must satisfy.

    These are asserted independently by the auditor against the trusted
    catalogue; they are the "expected evidence properties" for search cases.
    """

    category: str | None = None
    price_max_minor: int | None = None
    price_min_minor: int | None = None
    stock: str | None = None  # "IN_STOCK" / "OUT_OF_STOCK"
    rating_min: float | None = None
    attributes: tuple[tuple[str, str], ...] = ()

    @staticmethod
    def from_dict(d: dict[str, Any] | None) -> "HardFilters | None":
        if not d:
            return None
        attrs = tuple(
            (a["key"], a["value"]) for a in d.get("attributes", []) if a.get("key")
        )
        return HardFilters(
            category=d.get("category"),
            price_max_minor=d.get("price_max_minor"),
            price_min_minor=d.get("price_min_minor"),
            stock=d.get("stock"),
            rating_min=d.get("rating_min"),
            attributes=attrs,
        )


@dataclass(frozen=True)
class ExpectedBehaviour:
    """Every field optional; the dev set fills what it can compute deterministically."""

    decision: str | None = None
    reason: str | None = None  # ClarifyReason or RefusalReason value
    hard_filters: HardFilters | None = None
    must_include_products: tuple[str, ...] = ()
    must_exclude_products: tuple[str, ...] = ()
    exact_products: tuple[str, ...] | None = None  # exact returned-id set
    expect_absence: bool | None = None  # a catalogue/policy ABSENCE claim required
    no_definitive_absence: bool = False  # absence claim must NOT be produced

    @staticmethod
    def from_dict(d: dict[str, Any] | None) -> "ExpectedBehaviour":
        d = d or {}
        return ExpectedBehaviour(
            decision=d.get("decision"),
            reason=d.get("reason"),
            hard_filters=HardFilters.from_dict(d.get("hard_filters")),
            must_include_products=tuple(d.get("must_include_products", [])),
            must_exclude_products=tuple(d.get("must_exclude_products", [])),
            exact_products=tuple(d["exact_products"]) if d.get("exact_products") is not None else None,
            expect_absence=d.get("expect_absence"),
            no_definitive_absence=bool(d.get("no_definitive_absence", False)),
        )


@dataclass(frozen=True)
class Turn:
    message: str = ""
    constraint_ops: tuple[dict[str, Any], ...] = ()
    client_context: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Turn":
        return Turn(
            message=d.get("message", ""),
            constraint_ops=tuple(d.get("constraint_ops", [])),
            client_context=d.get("client_context", {}),
        )


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    rules: tuple[str, ...]  # decision rule ids exercised (D-01..D-17)
    tags: tuple[str, ...]
    turns: tuple[Turn, ...]
    expected: ExpectedBehaviour
    source: str = "dev"
    # status: "active" (must pass) | "known_gap" (spec branch not yet implemented;
    # reported honestly, never counted as a passing requirement)
    status: str = "active"
    gap_note: str | None = None
    rationale: str | None = None

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "EvalCase":
        return EvalCase(
            case_id=d["case_id"],
            rules=tuple(d.get("rules", [])),
            tags=tuple(d.get("tags", [])),
            turns=tuple(Turn.from_dict(t) for t in d.get("turns", [])),
            expected=ExpectedBehaviour.from_dict(d.get("expected")),
            source=d.get("source", "dev"),
            status=d.get("status", "active"),
            gap_note=d.get("gap_note"),
            rationale=d.get("rationale"),
        )


def load_cases(path: Path) -> list[EvalCase]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return [EvalCase.from_dict(c) for c in raw["cases"]]


DEFAULT_DEV_SET = Path(__file__).resolve().parent / "dev_set" / "cases.json"
