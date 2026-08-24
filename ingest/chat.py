"""Ingesta del chat de Telegram desde la tabla "Log grupo" de NocoDB.

Un mensaje suelto no dice casi nada, así que la unidad indexada es una *ventana de
conversación*: mensajes seguidos del mismo topic, cercanos en el tiempo. Cada ventana es un
documento con visibilidad 'group'.
"""

import hashlib
import json
import os
import urllib.parse
from datetime import datetime

from app import pii
from app.pii import hash_autor
from app.db import firma_actual, tombstones
from ingest.common import descargar_json, indexar_lote

GRUPO = "https://t.me/aldeapucela"

# Ventana de conversación. Salen ~10 mensajes por chunk, que es lo que pedía la propuesta.
MAX_MENSAJES = 10
SOLAPE_MENSAJES = 2
MAX_CHARS_VENTANA = 1200
HUECO_MINUTOS = 30       # más de media hora de silencio: conversación distinta
MIN_CHARS_MENSAJE = 15   # "jaja", "👏": ruido
TAM_LOTE = 256           # ventanas que se vectorizan de una tacada


def _config():
    url = os.environ.get("NOCODB_URL")
    token = os.environ.get("NOCODB_TOKEN")
    tabla = os.environ.get("NOCODB_TABLE")
    if not (url and token and tabla):
        raise SystemExit("faltan NOCODB_URL / NOCODB_TOKEN / NOCODB_TABLE (ver .env.example)")
    return url.rstrip("/"), {"xc-token": token}, tabla


def mensajes(pagina=1000):
    """Todas las filas de "Log grupo", ordenadas por Id (que sigue el orden de llegada)."""
    base, cabeceras, tabla = _config()
    offset = 0
    while True:
        q = urllib.parse.urlencode({"limit": pagina, "offset": offset, "sort": "Id"})
        d = descargar_json(f"{base}/api/v2/tables/{tabla}/records?{q}", cabeceras)
        filas = d.get("list") or []
        if not filas:
            return
        yield from filas
        if d.get("pageInfo", {}).get("isLastPage"):
            return
        offset += len(filas)


def enlace(fila):
    """Enlace al mensaje.

    No se usa el campo `url` de NocoDB: el workflow que lo escribe lo genera con doble barra
    cuando el mensaje no tiene message_thread_id (14 % de las filas) y queda inservible.
    """
    thread = fila.get("message_thread_id")
    return f"{GRUPO}/{thread}/{fila['message_id']}" if thread else f"{GRUPO}/{fila['message_id']}"


def _minutos(a, b):
    """Minutos entre dos fechas ISO de NocoDB ('2026-08-24 12:40:46+00:00')."""
    try:
        return abs((datetime.fromisoformat(a) - datetime.fromisoformat(b)).total_seconds()) / 60
    except (TypeError, ValueError):
        return 0


def ventanas(filas):
    """Agrupa mensajes consecutivos del mismo topic en ventanas de conversación."""
    actual = []
    for fila in filas:
        if actual:
            previo = actual[-1]
            corta = (
                fila.get("message_thread_id") != previo.get("message_thread_id")
                or _minutos(fila.get("date"), previo.get("date")) > HUECO_MINUTOS
                or len(actual) >= MAX_MENSAJES
                or sum(len(m["text"]) for m in actual) > MAX_CHARS_VENTANA
            )
            if corta:
                yield actual
                # Solape: la conversación se entiende mejor con un par de mensajes de contexto.
                mismo_hilo = fila.get("message_thread_id") == previo.get("message_thread_id")
                actual = actual[-SOLAPE_MENSAJES:] if mismo_hilo else []
        actual.append(fila)
    if actual:
        yield actual


def texto_ventana(ventana):
    lineas = []
    for m in ventana:
        nombre = m.get("first_name") or m.get("username") or "vecino"
        lineas.append(f"{nombre}: {pii.limpiar(m['text'])}")
    return "\n".join(lineas)


def ingestar(db, limite=None):
    sal = pii.sal()
    msgs_olvidados = tombstones(db, "chat_msg")
    autores_olvidados = tombstones(db, "chat_user")

    utiles = []
    for fila in mensajes():
        texto = (fila.get("text") or "").strip()
        if len(texto) < MIN_CHARS_MENSAJE:
            continue
        if str(fila["message_id"]) in msgs_olvidados:
            continue
        if hash_autor(fila["user_id"], sal) in autores_olvidados:
            continue
        fila["text"] = texto
        utiles.append(fila)
    print(f"[chat] {len(utiles)} mensajes con texto aprovechable")

    docs = chunks = 0
    lote = []
    for ventana in ventanas(utiles):
        if limite and docs >= limite:
            break
        primero = ventana[0]
        texto = texto_ventana(ventana)
        if len(texto) < 80:  # una ventana de dos líneas sueltas no aporta nada
            continue
        ids = [str(m["message_id"]) for m in ventana]
        autores = sorted({hash_autor(m["user_id"], sal) for m in ventana})
        source_id = f"chat:{primero.get('message_thread_id') or 'general'}:{primero['message_id']}"
        firma = hashlib.sha256(texto.encode()).hexdigest()[:16]
        if firma_actual(db, "chat", source_id) == firma:
            continue
        fecha = (primero.get("date") or "")[:10]
        doc = {
            "source_type": "chat",
            "source_id": source_id,
            "url": enlace(primero),
            "title": f"Conversación en el chat · {fecha}",
            # Lista de autores de la ventana: permite /forget por usuario sin guardar user_ids.
            "author_hash": json.dumps(autores),
            "created_at": primero.get("date") or "",
            "updated_at": ventana[-1].get("date") or "",
            "expires_at": None,
            "visibility": "group",
            "signature": firma,
        }
        lote.append((doc, texto, ids))
        if len(lote) >= TAM_LOTE:
            docs += indexar_lote(db, lote)
            chunks += len(lote)
            lote = []
            print(f"  … {docs} ventanas indexadas")

    docs += indexar_lote(db, lote)
    chunks += len(lote)
    return docs, chunks


def _autocomprobacion():
    # ponytail: comprueba el troceado conversacional, que es donde está la chicha.
    def m(i, thread, minuto, texto="mensaje de prueba con longitud suficiente"):
        return {
            "message_id": i, "user_id": 100 + (i % 3), "first_name": f"V{i % 3}",
            "message_thread_id": thread, "text": texto,
            "date": f"2026-08-24 {10 + minuto // 60:02d}:{minuto % 60:02d}:00+00:00",
        }

    # Un hueco largo parte la conversación
    v = list(ventanas([m(1, "5", 0), m(2, "5", 1), m(3, "5", 90)]))
    assert len(v) == 2, [len(x) for x in v]

    # Cambiar de topic también, y sin arrastrar solape de otro hilo
    v = list(ventanas([m(1, "5", 0), m(2, "5", 1), m(3, "9", 2)]))
    assert len(v) == 2 and len(v[1]) == 1, [[x["message_id"] for x in w] for w in v]

    # Corte por número de mensajes, con solape dentro del mismo hilo
    v = list(ventanas([m(i, "5", i) for i in range(1, 15)]))
    assert all(len(w) <= MAX_MENSAJES + 1 for w in v), [len(w) for w in v]
    comunes = set(x["message_id"] for x in v[0]) & set(x["message_id"] for x in v[1])
    assert len(comunes) == SOLAPE_MENSAJES, comunes

    # Ningún mensaje se pierde por el camino
    entrada = [m(i, "5", i * 40) for i in range(1, 8)]
    vistos = {x["message_id"] for w in ventanas(entrada) for x in w}
    assert vistos == {x["message_id"] for x in entrada}, vistos

    # El texto de la ventana lleva el nombre delante y pasa por el filtro de PII
    texto = texto_ventana([m(1, "5", 0, "mi teléfono es 655 123 456 por si acaso")])
    assert texto.startswith("V1: ") and "[teléfono]" in texto, texto

    # El enlace se reconstruye (y no hereda la doble barra del campo url de NocoDB)
    assert enlace({"message_thread_id": "244", "message_id": "207389"}).endswith("/244/207389")
    assert "//" not in enlace({"message_thread_id": None, "message_id": "92987"}).replace("https://", "")
    print("chat.py OK")


if __name__ == "__main__":
    _autocomprobacion()
