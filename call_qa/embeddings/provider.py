"""Абстракция эмбеддингов: cloud (Vertex) ↔ self-host меняется настройкой EMBEDDINGS_PROVIDER.
Vertex вызывается СЫРЫМ HTTP (google-auth для токена + httpx) — без google-cloud-aiplatform,
консистентно с тем, как вызывается Claude. Ноль новых зависимостей (google-auth идёт с
google-cloud-storage, httpx уже есть)."""
from __future__ import annotations
import functools

from .. import config


class EmbeddingProvider:
    dim: int = config.EMBED_DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class VertexEmbeddings(EmbeddingProvider):
    """Google Vertex text-multilingual-embedding-002 (asia-southeast1) через REST predict.
    Тем же сервис-аккаунтом, что GCS/STT. Проверено на kk/ru (cross-lang ~0.92–0.95)."""

    def __init__(self, region=None, model=None):
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
        self._model = model or config.VERTEX_EMBED_MODEL

    def _token(self) -> str:
        if not self._creds.valid:
            self._creds.refresh(self._authreq)
        return self._creds.token

    def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx
        url = (f"https://{self._region}-aiplatform.googleapis.com/v1/projects/{self._project}"
               f"/locations/{self._region}/publishers/google/models/{self._model}:predict")
        body = {"instances": [{"content": t, "task_type": "SEMANTIC_SIMILARITY"} for t in texts]}
        r = httpx.post(url, json=body, timeout=60.0,
                       headers={"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"})
        r.raise_for_status()
        return [p["embeddings"]["values"] for p in r.json()["predictions"]]


class SelfHostEmbeddings(EmbeddingProvider):
    """Резерв на случай строгой резидентности (данные не покидают РК).
    multilingual-e5-small (~470MB, влезает в 2GB-инстанс). НЕ BGE-M3 (тяжёлый для Render)."""

    def __init__(self, model_name="intfloat/multilingual-e5-small"):
        from sentence_transformers import SentenceTransformer
        self._m = SentenceTransformer(model_name)
        self.dim = self._m.get_sentence_embedding_dimension()

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._m.encode(["query: " + t for t in texts], normalize_embeddings=True).tolist()


@functools.lru_cache(maxsize=1)
def get_provider() -> EmbeddingProvider:
    return SelfHostEmbeddings() if config.EMBEDDINGS_PROVIDER == "selfhost" else VertexEmbeddings()
