"""API del buscador. Sirve al bot de Telegram (n8n), a las webs y, más adelante, a un MCP."""

import os
import sqlite3
import time

from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.db import (
    anotar_tombstone,
    borrar_documento,
    conectar,
    documentos_con_mensaje,
    documentos_de_autor,
)
from app.pii import hash_autor
from app.router import responder
from app.search import buscar

API_KEY = os.environ.get("API_KEY")

app = FastAPI(title="Buscador Aldea Pucela")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://([a-z0-9-]+\.)?(aldeapucela\.org|otrapucela\.org)",
    allow_methods=["GET"],
    allow_headers=["*"],
)

_db = None


def db():
    """Una conexión por proceso. /forget escribe, así que no se pone en modo solo lectura."""
    global _db
    if _db is None:
        _db = conectar()
    return _db


def _exige_clave(x_api_key):
    if not API_KEY or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="hace falta X-API-Key")


@app.on_event("startup")
def precalentar():
    # Cargar los modelos aquí evita que la primera búsqueda real pague ~10 s de arranque.
    buscar(db(), "precalentando el buscador", limite=1)


@app.get("/healthz")
def healthz():
    fila = db().execute(
        "SELECT (SELECT count(*) FROM documents WHERE deleted=0), (SELECT count(*) FROM chunks)"
    ).fetchone()
    return {"ok": True, "documentos": fila[0], "chunks": fila[1]}


@app.get("/search")
def search(
    q: str = Query(..., min_length=1, max_length=500),
    scope: str = Query("public", pattern="^(public|group)$"),
    sources: str | None = None,
    limit: int = Query(8, ge=1, le=25),
    x_api_key: str | None = Header(None),
):
    # El scope 'group' enseña contenido del chat: solo para el bot, dentro del grupo.
    if scope == "group":
        _exige_clave(x_api_key)
    fuentes = [s for s in (sources or "").split(",") if s] or None
    try:
        return buscar(db(), q, scope=scope, sources=fuentes, limite=limit)
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"error de índice: {e}") from e


@app.get("/ask")
def ask(
    q: str = Query(..., min_length=1, max_length=500),
    scope: str = Query("public", pattern="^(public|group)$"),
    limit: int = Query(8, ge=1, le=25),
    x_api_key: str | None = Header(None),
):
    """Como /search, pero pasando antes por el enrutador de intención.

    Es lo que debe llamar el bot: /finde y /contratos se responden con datos exactos y el
    resto cae en el buscador. La respuesta trae `tipo` para saber cómo formatearla.
    """
    if scope == "group":
        _exige_clave(x_api_key)
    try:
        return responder(db(), q, scope=scope, limite=limit)
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"error de índice: {e}") from e


@app.post("/forget")
def forget(
    payload: dict = Body(...),
    x_api_key: str | None = Header(None),
):
    """Saca contenido del índice y anota una lápida para que no vuelva al reindexar.

    Acepta uno de: {"message_id": N} | {"user_id": N} | {"source_id": "..."}.

    Cuando se olvida un mensaje se borra la ventana de conversación entera que lo contiene,
    no solo ese mensaje: es lo prudente en cuanto a privacidad y, al reindexar, la ventana se
    reconstruye sin él.
    """
    _exige_clave(x_api_key)
    conn = db()
    ahora = time.strftime("%Y-%m-%dT%H:%M:%S")

    if "message_id" in payload:
        clave = str(payload["message_id"])
        anotar_tombstone(conn, "chat_msg", clave, ahora)
        docs = documentos_con_mensaje(conn, clave)
    elif "user_id" in payload:
        clave = hash_autor(payload["user_id"])
        anotar_tombstone(conn, "chat_user", clave, ahora)
        docs = documentos_de_autor(conn, clave)
    elif "source_id" in payload:
        clave = str(payload["source_id"])
        anotar_tombstone(conn, "document", clave, ahora)
        docs = [
            r[0] for r in conn.execute("SELECT id FROM documents WHERE source_id = ?", (clave,))
        ]
    else:
        raise HTTPException(status_code=400, detail="indica message_id, user_id o source_id")

    chunks = sum(borrar_documento(conn, doc_id) for doc_id in docs)
    conn.commit()
    return {"ok": True, "documentos_borrados": len(docs), "chunks_borrados": chunks}
