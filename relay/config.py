"""Config loading. Model ids, prices, limits and seeds live in YAML, never in code."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_raw(path: str | os.PathLike) -> dict:
    """Load a YAML config. A config may say `extends: other.yaml` to inherit."""
    p = Path(path)
    if not p.is_absolute():
        p = (Path.cwd() / p) if (Path.cwd() / p).exists() else ROOT / p
    with open(p) as f:
        raw = yaml.safe_load(f) or {}
    parent = raw.pop("extends", None)
    if parent:
        base = load_raw(p.parent / parent)
        raw = _deep_merge(base, raw)
    raw.setdefault("_path", str(p))
    raw["_path"] = str(p)
    return raw


@dataclass
class Config:
    raw: dict[str, Any]

    @classmethod
    def load(cls, path: str | os.PathLike = "config.yaml") -> "Config":
        return cls(load_raw(path))

    # -- convenience accessors -------------------------------------------
    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    @property
    def root(self) -> Path:
        return Path(self.raw["_path"]).parent

    def path(self, key: str) -> Path:
        p = Path(self.raw["paths"][key])
        return p if p.is_absolute() else self.root / p

    @property
    def seed(self) -> int:
        return int(self.raw["experiment"]["seed"])

    @property
    def backends(self) -> dict[str, dict]:
        return self.raw["backends"]

    def backend(self, backend_id: str) -> dict:
        if backend_id not in self.backends:
            raise KeyError(f"backend {backend_id!r} is not defined in {self.raw['_path']}")
        return self.backends[backend_id]

    def price(self, backend_id: str) -> tuple[float, float]:
        p = self.backend(backend_id).get("price_per_mtok") or {}
        return float(p.get("input", 0.0)), float(p.get("output", 0.0))

    def cost_usd(self, backend_id: str, prompt_tokens: int, completion_tokens: int) -> float:
        pin, pout = self.price(backend_id)
        return round((prompt_tokens * pin + completion_tokens * pout) / 1e6, 10)

    def max_tokens(self, stage: str) -> int:
        return int(self.raw["generation"]["max_tokens"][stage])

    @property
    def synthetic(self) -> bool:
        return any(b.get("kind") == "sim" for b in self.backends.values())
