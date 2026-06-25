"""
Embedding generálás — sentence-transformers, all-MiniLM-L6-v2, 384-dim.
Egyszer tölt be, aztán gyors CPU inference.
"""
import logging

log = logging.getLogger(__name__)

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        log.info("[EMBEDDER] Modell betöltés: all-MiniLM-L6-v2")
        _model = SentenceTransformer("all-MiniLM-L6-v2")
        log.info("[EMBEDDER] Modell kész")
    return _model


def embed(text: str) -> list[float]:
    """Visszaad egy 384-dimenziós float listát."""
    model = _get_model()
    vec = model.encode(text, normalize_embeddings=True)
    return vec.tolist()


def embed_batch(texts: list[str]) -> list[list[float]]:
    model = _get_model()
    vecs = model.encode(texts, normalize_embeddings=True, batch_size=32)
    return [v.tolist() for v in vecs]
