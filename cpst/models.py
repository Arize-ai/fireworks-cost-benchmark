"""Model matrix config: load models.yaml and build OpenAI-compatible clients."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from openai import OpenAI

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "models.yaml"


@dataclass
class ModelSpec:
    key: str
    provider: str
    model: str
    base_url: str
    api_key_env: str
    input_per_mtok: float | None
    output_per_mtok: float | None

    def client(self) -> OpenAI:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"Missing {self.api_key_env} for model '{self.key}'. Set it in .env."
            )
        return OpenAI(base_url=self.base_url, api_key=api_key)

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float | None:
        """USD cost for a run. completion_tokens already includes reasoning."""
        if self.input_per_mtok is None or self.output_per_mtok is None:
            return None
        return (
            prompt_tokens / 1_000_000 * self.input_per_mtok
            + completion_tokens / 1_000_000 * self.output_per_mtok
        )


@dataclass
class Defaults:
    max_steps: int = 40
    command_timeout_sec: float = 120.0
    token_cap: int = 200_000
    wall_clock_cap_sec: float = 900.0


def _load() -> tuple[dict[str, ModelSpec], Defaults]:
    spec = yaml.safe_load(CONFIG_PATH.read_text())
    d = spec.get("defaults", {})
    defaults = Defaults(
        max_steps=int(d.get("max_steps", 40)),
        command_timeout_sec=float(d.get("command_timeout_sec", 120)),
        token_cap=int(d.get("token_cap", 200_000)),
        wall_clock_cap_sec=float(d.get("wall_clock_cap_sec", 900)),
    )
    models: dict[str, ModelSpec] = {}
    for key, m in spec["models"].items():
        pricing = m.get("pricing") or {}
        models[key] = ModelSpec(
            key=key,
            provider=m["provider"],
            model=m["model"],
            base_url=m["base_url"],
            api_key_env=m["api_key_env"],
            input_per_mtok=pricing.get("input_per_mtok"),
            output_per_mtok=pricing.get("output_per_mtok"),
        )
    return models, defaults


def load_models() -> dict[str, ModelSpec]:
    return _load()[0]


def load_defaults() -> Defaults:
    return _load()[1]


def get_model(key: str) -> ModelSpec:
    models = load_models()
    if key not in models:
        raise KeyError(f"Unknown model '{key}'. Known: {', '.join(models)}")
    return models[key]
