"""Text embeddings.

Production: a local ONNX model through fastembed (no API key, runs on
the laptop). Tests: HashEmbedder, deterministic and dependency-free.
Vectors are L2-normalized (dot product = cosine).
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import math
import re
from functools import lru_cache
from typing import Protocol

from jobpilot.core.config import REPO_ROOT, get_config


class Embedder(Protocol):
    """Anything that turns texts into fixed-size vectors."""

    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per text, in order."""


class FastEmbedder:
    """The production embedder: a local ONNX model run by fastembed."""

    def __init__(self, model: str | None = None) -> None:
        """Configure the model; it is loaded on first use.

        Args:
            model: A fastembed model name; defaults to the config value.
        """
        cfg = get_config().embeddings
        self.model_name = model or cfg.model
        self.dim = cfg.dim
        self._model = None

    def _load(self):  # type: ignore[no-untyped-def]
        """Load the model once (downloading it to the cache the first time)."""
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(
                self.model_name, cache_dir=str(REPO_ROOT / get_config().embeddings.cache_dir)
            )
        return self._model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts in a thread so the event loop stays free."""
        if not texts:
            return []

        def run() -> list[list[float]]:
            """Embed in the calling thread and convert to plain lists."""
            return [v.tolist() for v in self._load().embed(texts)]

        return await asyncio.to_thread(run)  # onnxruntime releases the GIL


class HashEmbedder:
    """Bag of words + bigrams hashed into a fixed-size vector. Shared words => similar vectors."""

    def __init__(self, dim: int = 384) -> None:
        """Create a hash embedder with ``dim`` dimensions."""
        self.dim = dim

    def _one(self, text: str) -> list[float]:
        """Embed one text: hash each word and word pair into a signed bucket."""
        words = re.findall(r"[a-z0-9+#]+", text.lower())
        vec = [0.0] * self.dim
        for token in words + [f"{a} {b}" for a, b in itertools.pairwise(words)]:
            h = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big")
            # The hash's top bit picks the sign, so collisions tend to
            # cancel.
            vec[h % self.dim] += 1.0 if h >> 63 else -1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed every text (no I/O, so no thread needed)."""
        return [self._one(t) for t in texts]


def cosine(a: list[float], b: list[float]) -> float:
    """Return the cosine similarity of two normalized vectors (their dot product)."""
    return sum(x * y for x, y in zip(a, b, strict=True))


def scale(value: float, low: float, high: float) -> float:
    """Map a similarity onto 0..1 between `low` (0) and `high` (1), clamped."""
    return max(0.0, min(1.0, (value - low) / (high - low)))


@lru_cache
def default_embedder() -> FastEmbedder:
    """Return the process-wide production embedder (created once)."""
    return FastEmbedder()
