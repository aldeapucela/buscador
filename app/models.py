"""Modelos locales de embeddings y reranking. Se cargan la primera vez que se usan."""

import os

MODELO_EMBEDDINGS = os.environ.get("MODELO_EMBEDDINGS", "intfloat/multilingual-e5-small")
MODELO_RERANKER = os.environ.get(
    "MODELO_RERANKER", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
)

_embedder = None
_reranker = None


def embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer

        _embedder = SentenceTransformer(MODELO_EMBEDDINGS, device="cpu")
    return _embedder


# Truncar el pasaje a 256 tokens no solo va 1,5x más rápido: en el set dorado de la Fase 1
# el MRR sube (0,963 → 0,967), porque el reranker se despista con pasajes largos.
# Revisar este valor en la Fase 2: los chunks de chat son más largos que 256 tokens.
MAX_TOKENS_RERANKER = 256


def reranker():
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder

        _reranker = CrossEncoder(MODELO_RERANKER, device="cpu", max_length=MAX_TOKENS_RERANKER)
    return _reranker


def vectorizar_pasajes(textos, batch_size=32, progreso=False):
    # e5 exige los prefijos "passage: " / "query: "; sin ellos la calidad cae bastante.
    return embedder().encode(
        [f"passage: {t}" for t in textos],
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=progreso,
    )


def vectorizar_consulta(texto):
    return embedder().encode(
        f"query: {texto}", normalize_embeddings=True
    )
