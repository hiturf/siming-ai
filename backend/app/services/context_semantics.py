"""Local semantic-vector primitives used by context orchestration."""
from __future__ import annotations

import math
from array import array
from collections.abc import Sequence
from typing import Any


LOCAL_EMBEDDING_MODEL = "intfloat/multilingual-e5-small"


def normalise_score(value: float | None, values: Sequence[float]) -> float:
    if value is None or not values:
        return 0.0
    low = min(values)
    high = max(values)
    if math.isclose(low, high):
        return 1.0 if value > 0 else 0.0
    return max(0.0, min(1.0, (value - low) / (high - low)))


class LocalSemanticRuntime:
    """Optional local FastEmbed runtime without a cloud embedding endpoint."""

    _model: Any = None
    _initialisation_error: str | None = None

    @classmethod
    def status(cls) -> dict[str, Any]:
        try:
            import fastembed  # noqa: F401
        except Exception as exc:
            return {
                "available": False,
                "model": LOCAL_EMBEDDING_MODEL,
                "reason": f"FastEmbed unavailable: {exc.__class__.__name__}",
            }
        if cls._initialisation_error:
            return {
                "available": False,
                "model": LOCAL_EMBEDDING_MODEL,
                "reason": cls._initialisation_error,
            }
        return {"available": True, "model": LOCAL_EMBEDDING_MODEL, "reason": ""}

    @classmethod
    def _get_model(cls):
        if cls._model is not None:
            return cls._model
        try:
            from fastembed import TextEmbedding

            cls._model = TextEmbedding(model_name=LOCAL_EMBEDDING_MODEL)
            return cls._model
        except Exception as exc:
            cls._initialisation_error = f"FastEmbed could not initialise: {exc}"
            raise RuntimeError(cls._initialisation_error) from exc

    @classmethod
    def embed(cls, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = cls._get_model().embed(list(texts))
        return [[float(value) for value in vector] for vector in vectors]


def pack_float32(vector: Sequence[float]) -> bytes:
    return array("f", (float(value) for value in vector)).tobytes()


def unpack_float32(blob: bytes, dimension: int) -> list[float]:
    if not blob or dimension <= 0:
        return []
    values = array("f")
    values.frombytes(blob)
    return [float(value) for value in values[:dimension]]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return dot / (left_norm * right_norm)
