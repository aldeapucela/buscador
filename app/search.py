"""Búsqueda híbrida: vectorial + léxica (FTS5), fusión RRF, reranker local y ponderaciones."""

import math
import os
import re
import time

import sqlite_vec

from app import models

# El reranker mejora el orden (+0,06 de MRR) pero cuesta ~400 ms por candidato en un núcleo.
# En oracle-server, que tiene UNO, son 12 s por consulta: allí va apagado con RERANKER=0.
# Apagarlo no cambia el recall@8 (1,00 en el set dorado), solo el orden dentro del top 8.
RERANKER_POR_DEFECTO = os.environ.get("RERANKER", "1") != "0"

# 30 candidatos en vez de 50: medido en la Fase 1, es 2,4x más rápido y el MRR sube
# (0,963 → 0,980), porque RRF ya deja arriba lo bueno y la cola solo añade ruido.
CANDIDATOS = 30
K_RRF = 60               # constante estándar de Reciprocal Rank Fusion
PESO_FUENTE = {          # un artículo editado no vale lo mismo que un mensaje de chat
    "otrapucela": 1.2,
    "forum": 1.0,
    "evento": 1.0,
    "chat": 0.8,
}
PENALIZACION_CADUCADO = 0.5


def _consulta_fts(texto):
    """Query segura para FTS5: tokens entrecomillados unidos por OR."""
    tokens = re.findall(r"\w+", texto, re.UNICODE)
    return " OR ".join(f'"{t}"' for t in tokens if len(t) > 1)


def _candidatos_vectoriales(db, consulta, scope, limite):
    vec = models.vectorizar_consulta(consulta)
    # Se pide de más porque el filtro de visibilidad se aplica después del KNN.
    filas = db.execute(
        """SELECT v.chunk_id, v.distance
             FROM vec_chunks v
            WHERE v.embedding MATCH ? AND k = ?""",
        (sqlite_vec.serialize_float32(vec), limite * 4),
    ).fetchall()
    return [r["chunk_id"] for r in filas]


def _candidatos_lexicos(db, consulta, limite):
    match = _consulta_fts(consulta)
    if not match:
        return []
    filas = db.execute(
        """SELECT rowid FROM chunks_fts
            WHERE chunks_fts MATCH ?
            ORDER BY rank
            LIMIT ?""",
        (match, limite * 4),
    ).fetchall()
    return [r["rowid"] for r in filas]


def _rrf(listas, k=K_RRF):
    puntos = {}
    for lista in listas:
        for posicion, chunk_id in enumerate(lista):
            puntos[chunk_id] = puntos.get(chunk_id, 0.0) + 1.0 / (k + posicion + 1)
    return sorted(puntos, key=puntos.get, reverse=True)


def _metadatos(db, chunk_ids, scope, sources):
    """Trae chunk + documento, aplicando visibilidad y filtro de fuentes EN SQL."""
    if not chunk_ids:
        return {}
    marcas = ",".join("?" * len(chunk_ids))
    sql = f"""SELECT c.id AS chunk_id, c.text, d.id AS doc_id, d.url, d.title,
                     d.source_type, d.created_at, d.expires_at, d.visibility
                FROM chunks c JOIN documents d ON d.id = c.document_id
               WHERE c.id IN ({marcas}) AND d.deleted = 0"""
    params = list(chunk_ids)
    if scope != "group":
        # El scope público nunca ve contenido del grupo. Se filtra aquí, no en el frontend.
        sql += " AND d.visibility = 'public'"
    if sources:
        sql += f" AND d.source_type IN ({','.join('?' * len(sources))})"
        params += list(sources)
    return {r["chunk_id"]: dict(r) for r in db.execute(sql, params)}


def _sigmoide(x):
    return 1 / (1 + math.exp(-x))


def buscar(db, consulta, scope="public", sources=None, limite=8, usar_reranker=None):
    t0 = time.time()
    if usar_reranker is None:
        usar_reranker = RERANKER_POR_DEFECTO
    consulta = (consulta or "").strip()
    if not consulta:
        return {"query": consulta, "took_ms": 0, "results": []}

    fusionados = _rrf([
        _candidatos_vectoriales(db, consulta, scope, CANDIDATOS),
        _candidatos_lexicos(db, consulta, CANDIDATOS),
    ])
    metas = _metadatos(db, fusionados[: CANDIDATOS * 4], scope, sources)
    candidatos = [metas[cid] for cid in fusionados if cid in metas][:CANDIDATOS]
    if not candidatos:
        return {"query": consulta, "took_ms": int((time.time() - t0) * 1000), "results": []}

    if usar_reranker:
        pares = [(consulta, c["text"]) for c in candidatos]
        for c, punt in zip(candidatos, models.reranker().predict(pares)):
            c["base"] = _sigmoide(float(punt))
    else:
        for posicion, c in enumerate(candidatos):
            c["base"] = 1.0 / (posicion + 1)

    ahora = time.strftime("%Y-%m-%d")
    for c in candidatos:
        caducado = bool(c["expires_at"] and c["expires_at"][:10] < ahora)
        c["expired"] = caducado
        c["score"] = (
            c["base"]
            * PESO_FUENTE.get(c["source_type"], 1.0)
            * (PENALIZACION_CADUCADO if caducado else 1.0)
        )

    # Un resultado por documento: nos quedamos con su mejor chunk.
    mejor_por_doc = {}
    for c in sorted(candidatos, key=lambda x: x["score"], reverse=True):
        mejor_por_doc.setdefault(c["doc_id"], c)

    resultados = [
        {
            "title": c["title"],
            "url": c["url"],
            "snippet": _fragmento(c["text"]),
            "source_type": c["source_type"],
            "date": (c["created_at"] or "")[:10],
            "score": round(c["score"], 4),
            "expired": c["expired"],
        }
        for c in list(mejor_por_doc.values())[:limite]
    ]
    return {
        "query": consulta,
        "took_ms": int((time.time() - t0) * 1000),
        "results": resultados,
    }


def _fragmento(texto, maximo=280):
    texto = " ".join(texto.split())
    if len(texto) <= maximo:
        return texto
    corte = texto.rfind(" ", 0, maximo)
    return texto[: corte if corte > 0 else maximo] + "…"
