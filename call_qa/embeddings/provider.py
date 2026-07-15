"""Embedding providers with an explicit, versioned index contract.

Query and document embeddings are intentionally separate operations.  Models
such as E5 use different prefixes for the two roles and Vertex accepts distinct
``task_type`` values.  Mixing the roles silently reduces retrieval quality, so
the provider/model/dimension tuple is exposed and validated at every boundary.
"""
from __future__ import annotations

import functools
import math
from typing import Iterable

from .. import config
from ..evaluation.fingerprint import content_hash


def configured_contract() -> dict:
    """Pure, secret-free identity of the query/document embedding contract."""
    if config.EMBEDDINGS_PROVIDER == "selfhost":
        model = config.SELFHOST_EMBED_MODEL
        details = {
            "normalize": True, "query_prefix": "query: ",
            "document_prefix": "passage: ", "library": "sentence-transformers",
        }
    elif config.EMBEDDINGS_PROVIDER == "vertex":
        model = config.VERTEX_EMBED_MODEL
        details = {
            "region": config.VERTEX_REGION, "query_task_type": "RETRIEVAL_QUERY",
            "document_task_type": "RETRIEVAL_DOCUMENT", "auto_truncate": False,
            "output_dimensionality": int(config.EMBED_DIM),
        }
    else:
        model, details = "unsupported", {"provider": config.EMBEDDINGS_PROVIDER}
    return {
        "provider": config.EMBEDDINGS_PROVIDER, "model": model,
        "dim": int(config.EMBED_DIM), "config": details,
        "config_hash": content_hash(details),
    }


class EmbeddingError(RuntimeError):
    """Base error for an invalid embedding request/response."""


class EmbeddingDimensionError(EmbeddingError):
    """A vector does not match the configured pgvector index dimension."""


class EmbeddingResponseError(EmbeddingError):
    """The provider returned a malformed or incomplete response."""


def validate_embeddings(vectors: Iterable[Iterable[float]], *, expected_count: int,
                        expected_dim: int) -> list[list[float]]:
    """Return finite float vectors or raise with a precise contract error."""
    materialized = list(vectors)
    if len(materialized) != expected_count:
        raise EmbeddingResponseError(
            f"embedding count mismatch: expected {expected_count}, got {len(materialized)}")

    out: list[list[float]] = []
    for index, raw in enumerate(materialized):
        try:
            vector = [float(value) for value in raw]
        except (TypeError, ValueError) as exc:
            raise EmbeddingResponseError(f"embedding {index} is not a numeric vector") from exc
        if len(vector) != expected_dim:
            raise EmbeddingDimensionError(
                f"embedding {index} dimension mismatch: expected {expected_dim}, got {len(vector)}")
        if not all(math.isfinite(value) for value in vector):
            raise EmbeddingResponseError(f"embedding {index} contains a non-finite value")
        out.append(vector)
    return out


class EmbeddingProvider:
    """Provider protocol used by indexing and retrieval."""

    provider_name = "unknown"
    model_name = "unknown"
    dim: int = config.EMBED_DIM

    @property
    def metadata(self) -> dict:
        contract = configured_contract()
        metadata = {
            "provider": str(self.provider_name),
            "model": str(self.model_name),
            "dim": int(self.dim),
        }
        if (metadata["provider"], metadata["model"], metadata["dim"]) == (
                contract["provider"], contract["model"], contract["dim"]):
            metadata.update(config=contract["config"], config_hash=contract["config_hash"])
        return metadata

    def embed_query(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed_document(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Backward-compatible alias. New code must choose a role explicitly."""
        return self.embed_query(texts)

    def _validated(self, vectors, texts: list[str]) -> list[list[float]]:
        return validate_embeddings(vectors, expected_count=len(texts), expected_dim=self.dim)


class VertexEmbeddings(EmbeddingProvider):
    """Google Vertex multilingual embeddings through the REST ``predict`` API."""

    provider_name = "vertex"

    def __init__(self, region=None, model=None, dim=None):
        from google.oauth2 import service_account
        import google.auth.transport.requests as gtr

        sa = config.google_sa_info()
        if not sa:
            raise RuntimeError("нет GOOGLE_APPLICATION_CREDENTIALS_CONTENT")
        self._creds = service_account.Credentials.from_service_account_info(
            sa, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        self._authreq = gtr.Request()
        self._project = sa["project_id"]
        self._region = region or config.VERTEX_REGION
        self.model_name = model or config.VERTEX_EMBED_MODEL
        self.dim = int(dim or config.EMBED_DIM)

    def _token(self) -> str:
        if not self._creds.valid:
            self._creds.refresh(self._authreq)
        return self._creds.token

    def _request(self, texts: list[str], *, task_type: str) -> list[list[float]]:
        if not texts:
            return []
        import httpx

        url = (f"https://{self._region}-aiplatform.googleapis.com/v1/projects/{self._project}"
               f"/locations/{self._region}/publishers/google/models/{self.model_name}:predict")
        body = {
            "instances": [{"content": text, "task_type": task_type} for text in texts],
            "parameters": {"autoTruncate": False, "outputDimensionality": self.dim},
        }
        response = httpx.post(
            url, json=body, timeout=60.0,
            headers={"Authorization": f"Bearer {self._token()}",
                     "Content-Type": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        try:
            vectors = [prediction["embeddings"]["values"]
                       for prediction in payload["predictions"]]
        except (KeyError, TypeError) as exc:
            raise EmbeddingResponseError("Vertex response has no embedding predictions") from exc
        return self._validated(vectors, texts)

    def embed_query(self, texts: list[str]) -> list[list[float]]:
        return self._request(texts, task_type="RETRIEVAL_QUERY")

    def embed_document(self, texts: list[str]) -> list[list[float]]:
        return self._request(texts, task_type="RETRIEVAL_DOCUMENT")


class SelfHostEmbeddings(EmbeddingProvider):
    """Sentence-Transformers E5 provider for strict data residency."""

    provider_name = "selfhost"

    def __init__(self, model_name=None):
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name or config.SELFHOST_EMBED_MODEL
        self._m = SentenceTransformer(self.model_name)
        self.dim = int(self._m.get_sentence_embedding_dimension())

    def _encode(self, texts: list[str], *, prefix: str) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._m.encode(
            [prefix + text for text in texts], normalize_embeddings=True).tolist()
        return self._validated(vectors, texts)

    def embed_query(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts, prefix="query: ")

    def embed_document(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts, prefix="passage: ")


def _validate_provider_contract(provider: EmbeddingProvider) -> EmbeddingProvider:
    if int(provider.dim) != int(config.EMBED_DIM):
        meta = provider.metadata
        raise EmbeddingDimensionError(
            "embedding provider/index dimension mismatch: "
            f"{meta['provider']}/{meta['model']} emits {meta['dim']}, "
            f"configured index expects {config.EMBED_DIM}")
    return provider


@functools.lru_cache(maxsize=1)
def get_provider() -> EmbeddingProvider:
    if config.EMBEDDINGS_PROVIDER == "selfhost":
        provider = SelfHostEmbeddings()
    elif config.EMBEDDINGS_PROVIDER == "vertex":
        provider = VertexEmbeddings()
    else:
        raise EmbeddingError(
            f"unsupported EMBEDDINGS_PROVIDER={config.EMBEDDINGS_PROVIDER!r}; "
            "expected 'vertex' or 'selfhost'")
    return _validate_provider_contract(provider)
