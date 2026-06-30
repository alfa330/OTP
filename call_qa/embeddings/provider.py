"""Абстракция эмбеддингов: cloud (Vertex) ↔ self-host меняется настройкой EMBEDDINGS_PROVIDER.
Так юридическое решение по резидентности не блокирует код."""
from __future__ import annotations
import functools

from .. import config


class EmbeddingProvider:
    dim: int = config.EMBED_DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class VertexEmbeddings(EmbeddingProvider):
    """Google Vertex `text-multilingual-embedding-002` (asia-southeast1).
    Тем же сервис-аккаунтом, что GCS/STT. Проверено на kk/ru (cross-lang ~0.92–0.95)."""

    def __init__(self, region=None, model=None):
        from google.oauth2 import service_account
        import vertexai
        sa = config.google_sa_info()
        if not sa:
            raise RuntimeError("нет GOOGLE_APPLICATION_CREDENTIALS_CONTENT")
        creds = service_account.Credentials.from_service_account_info(
            sa, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        vertexai.init(project=sa["project_id"], location=region or config.VERTEX_REGION, credentials=creds)
        from vertexai.language_models import TextEmbeddingModel
        # TODO(prod): vertexai.language_models помечен deprecated (removal 2026-06-24);
        #   перейти на google-genai / aiplatform текущий путь.
        self._model = TextEmbeddingModel.from_pretrained(model or config.VERTEX_EMBED_MODEL)

    def embed(self, texts: list[str]) -> list[list[float]]:
        from vertexai.language_models import TextEmbeddingInput
        inp = [TextEmbeddingInput(text=t, task_type="SEMANTIC_SIMILARITY") for t in texts]
        try:
            embs = self._model.get_embeddings(inp)
        except Exception:
            embs = [self._model.get_embeddings([x])[0] for x in inp]
        return [list(e.values) for e in embs]


class SelfHostEmbeddings(EmbeddingProvider):
    """Резерв на случай строгой резидентности (данные не покидают РК).
    multilingual-e5-small (~470MB, влезает в 2GB-инстанс). НЕ BGE-M3 (тяжёлый для Render)."""

    def __init__(self, model_name="intfloat/multilingual-e5-small"):
        from sentence_transformers import SentenceTransformer
        self._m = SentenceTransformer(model_name)
        self.dim = self._m.get_sentence_embedding_dimension()

    def embed(self, texts: list[str]) -> list[list[float]]:
        # e5 рекомендует префикс; для коротких разборов берём 'query:'
        return self._m.encode(["query: " + t for t in texts], normalize_embeddings=True).tolist()


@functools.lru_cache(maxsize=1)
def get_provider() -> EmbeddingProvider:
    return SelfHostEmbeddings() if config.EMBEDDINGS_PROVIDER == "selfhost" else VertexEmbeddings()
