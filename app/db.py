"""Acceso a SQLite: esquema, conexión y borrado con propagación."""

import json
import os
import sqlite3

import sqlite_vec

RUTA_DB = os.environ.get("BUSCADOR_DB", "data/index.db")
DIMS = 384  # multilingual-e5-small

ESQUEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  source_type TEXT NOT NULL,          -- 'otrapucela' | 'forum' | 'evento' | 'chat'
  source_id TEXT NOT NULL,
  url TEXT NOT NULL,
  title TEXT,
  author_hash TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  expires_at TEXT,
  visibility TEXT NOT NULL DEFAULT 'public',   -- 'public' | 'group'
  signature TEXT NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0,
  UNIQUE(source_type, source_id)
);

CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  text TEXT NOT NULL,
  msg_ids TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text, content='chunks', content_rowid='id',
  tokenize="unicode61 remove_diacritics 2"
);
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
  INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TABLE IF NOT EXISTS tombstones (
  kind TEXT NOT NULL,                 -- 'chat_msg' | 'chat_user' | 'document'
  key TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (kind, key)
);
"""


def conectar(ruta=None):
    ruta = ruta or RUTA_DB
    os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
    # check_same_thread=False: uvicorn atiende cada petición en un hilo del pool. El módulo
    # sqlite3 va en modo serializado (threadsafety=3), así que compartir la conexión vale.
    db = sqlite3.connect(ruta, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    db.executescript(ESQUEMA)
    db.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0("
        f"chunk_id INTEGER PRIMARY KEY, embedding FLOAT[{DIMS}])"
    )
    return db


def borrar_chunks(db, document_id):
    """Quita los chunks de un documento de las tres tablas (vec, chunks y, por trigger, FTS)."""
    ids = [r[0] for r in db.execute("SELECT id FROM chunks WHERE document_id = ?", (document_id,))]
    if ids:
        marcas = ",".join("?" * len(ids))
        db.execute(f"DELETE FROM vec_chunks WHERE chunk_id IN ({marcas})", ids)
        db.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
    return len(ids)


def borrar_documento(db, document_id):
    """Borrado con propagación: vectores, chunks, FTS y marca el documento como borrado."""
    n = borrar_chunks(db, document_id)
    db.execute("UPDATE documents SET deleted = 1 WHERE id = ?", (document_id,))
    db.commit()
    return n


def anotar_tombstone(db, kind, key, cuando):
    db.execute(
        "INSERT OR IGNORE INTO tombstones(kind, key, created_at) VALUES (?,?,?)",
        (kind, str(key), cuando),
    )


def tombstones(db, kind):
    return {r[0] for r in db.execute("SELECT key FROM tombstones WHERE kind = ?", (kind,))}


def documentos_con_mensaje(db, message_id):
    """Documentos (ventanas de chat) que incluyen ese mensaje de Telegram."""
    filas = db.execute(
        """SELECT DISTINCT c.document_id
             FROM chunks c, json_each(c.msg_ids) j
            WHERE c.msg_ids IS NOT NULL AND j.value = ?""",
        (str(message_id),),
    )
    return [r[0] for r in filas]


def documentos_de_autor(db, author_hash):
    """Documentos en los que participa ese autor (documents.author_hash es una lista JSON)."""
    filas = db.execute(
        """SELECT DISTINCT d.id
             FROM documents d, json_each(d.author_hash) j
            WHERE d.author_hash IS NOT NULL AND json_valid(d.author_hash) AND j.value = ?""",
        (author_hash,),
    )
    return [r[0] for r in filas]


def guardar_documento(db, doc, chunks, vectores):
    """Inserta o reemplaza un documento con sus chunks y vectores. Devuelve el id."""
    cur = db.execute(
        """INSERT INTO documents
             (source_type, source_id, url, title, author_hash, created_at, updated_at,
              expires_at, visibility, signature, deleted)
           VALUES (:source_type, :source_id, :url, :title, :author_hash, :created_at,
                   :updated_at, :expires_at, :visibility, :signature, 0)
           ON CONFLICT(source_type, source_id) DO UPDATE SET
             url=excluded.url, title=excluded.title, created_at=excluded.created_at,
             updated_at=excluded.updated_at, expires_at=excluded.expires_at,
             signature=excluded.signature, deleted=0
           RETURNING id""",
        doc,
    )
    doc_id = cur.fetchone()[0]
    borrar_chunks(db, doc_id)
    for seq, (texto, vec) in enumerate(zip(chunks, vectores)):
        msg_ids = None
        if isinstance(texto, tuple):  # el chat pasa (texto, [message_ids])
            texto, msg_ids = texto[0], json.dumps(texto[1])
        cur = db.execute(
            "INSERT INTO chunks(document_id, seq, text, msg_ids) VALUES (?,?,?,?) RETURNING id",
            (doc_id, seq, texto, msg_ids),
        )
        chunk_id = cur.fetchone()[0]
        db.execute(
            "INSERT INTO vec_chunks(chunk_id, embedding) VALUES (?, ?)",
            (chunk_id, sqlite_vec.serialize_float32(vec)),
        )
    return doc_id


def firma_actual(db, source_type, source_id):
    r = db.execute(
        "SELECT signature FROM documents WHERE source_type = ? AND source_id = ? AND deleted = 0",
        (source_type, source_id),
    ).fetchone()
    return r[0] if r else None
