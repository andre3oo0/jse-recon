"""Paths and config loading, resolved from the repo root so tools run the same from anywhere."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
SQL_DIR = ROOT / "sql"
DATA_DIR = ROOT / "data"
LANDING_DIR = DATA_DIR / "landing"
DB_PATH = DATA_DIR / "warehouse.db"


def load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


def universe() -> dict:
    return load_yaml("universe.yaml")


def tolerance_rules() -> dict:
    return load_yaml("tolerance_rules.yaml")


def sources() -> dict:
    return load_yaml("sources.yaml")


def reference_weights() -> list[dict]:
    return load_yaml("reference_weights.yaml")["weights"]
