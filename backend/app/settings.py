from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import yaml


@dataclass(frozen=True)
class AppConfig:
    paths: dict[str, str]
    limits: dict[str, Any]
    results: dict[str, Any]
    card_fields: list[str]
    citation: dict[str, Any]
    policy: dict[str, Any]
    cors: dict[str, Any]


@dataclass(frozen=True)
class ThresholdsConfig:
    term_min_coverage: float = 0.67
    policy_min_coverage: float = 0.50
    top_k_display: int = 5
    clarify_max_options: int = 5
    price_under_is_inclusive: bool = False


@dataclass(frozen=True)
class Settings:
    base_dir: Path
    config_dir: Path
    app: AppConfig
    thresholds: ThresholdsConfig
    catalogue_mapping: dict[str, Any]
    attribute_synonyms: dict[str, str]
    category_aliases: dict[str, str]
    codemix: dict[str, str]
    intent_cues: dict[str, list[str]]
    stopwords: frozenset[str]


def load_settings(config_dir: Path | None = None) -> Settings:
    if config_dir is None:
        # Check current directory or parent directory for config
        candidates = [
            Path.cwd() / "config",
            Path.cwd().parent / "config",
            Path(__file__).resolve().parent.parent.parent / "config",
        ]
        for c in candidates:
            if c.exists() and (c / "app.yaml").exists():
                config_dir = c
                break
        if config_dir is None:
            config_dir = Path("config").resolve()

    base_dir = config_dir.parent

    with open(config_dir / "app.yaml", "r", encoding="utf-8") as f:
        app_raw = yaml.safe_load(f)

    with open(config_dir / "catalogue_mapping.yaml", "r", encoding="utf-8") as f:
        mapping_raw = yaml.safe_load(f)

    with open(config_dir / "decision_thresholds.yaml", "r", encoding="utf-8") as f:
        thresh_raw = yaml.safe_load(f)

    synonyms_path = config_dir / "attribute_synonyms.yaml"
    synonyms = {}
    if synonyms_path.exists():
        with open(synonyms_path, "r", encoding="utf-8") as f:
            syn_raw = yaml.safe_load(f) or {}
            synonyms = syn_raw.get("synonyms", {})

    aliases_path = config_dir / "category_aliases.yaml"
    aliases = {}
    if aliases_path.exists():
        with open(aliases_path, "r", encoding="utf-8") as f:
            al_raw = yaml.safe_load(f) or {}
            aliases = al_raw.get("aliases", {})

    codemix_path = config_dir / "lexicons" / "codemix_terms.yaml"
    codemix = {}
    if codemix_path.exists():
        with open(codemix_path, "r", encoding="utf-8") as f:
            codemix = yaml.safe_load(f) or {}

    cues_path = config_dir / "lexicons" / "intent_cues.yaml"
    intent_cues = {}
    if cues_path.exists():
        with open(cues_path, "r", encoding="utf-8") as f:
            intent_cues = yaml.safe_load(f) or {}

    stopwords_path = config_dir / "lexicons" / "stopwords.txt"
    stopwords = set()
    if stopwords_path.exists():
        with open(stopwords_path, "r", encoding="utf-8") as f:
            for line in f:
                w = line.strip().lower()
                if w:
                    stopwords.add(w)

    app_cfg = AppConfig(
        paths=app_raw.get("paths", {}),
        limits=app_raw.get("limits", {}),
        results=app_raw.get("results", {}),
        card_fields=app_raw.get("card_fields", ["price", "stock"]),
        citation=app_raw.get("citation", {}),
        policy=app_raw.get("policy", {}),
        cors=app_raw.get("cors", {"origins": ["*"]}),
    )

    thresh_cfg = ThresholdsConfig(
        term_min_coverage=float(thresh_raw.get("term_min_coverage", 0.67)),
        policy_min_coverage=float(thresh_raw.get("policy_min_coverage", 0.50)),
        top_k_display=int(thresh_raw.get("top_k_display", 5)),
        clarify_max_options=int(thresh_raw.get("clarify_max_options", 5)),
        price_under_is_inclusive=bool(thresh_raw.get("price", {}).get("under_is_inclusive", False)),
    )

    return Settings(
        base_dir=base_dir,
        config_dir=config_dir,
        app=app_cfg,
        thresholds=thresh_cfg,
        catalogue_mapping=mapping_raw,
        attribute_synonyms=synonyms,
        category_aliases=aliases,
        codemix=codemix,
        intent_cues=intent_cues,
        stopwords=frozenset(stopwords),
    )
