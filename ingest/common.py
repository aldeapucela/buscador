"""Piezas compartidas por los ingestores: descarga, HTML→texto, troceado y guardado."""

import html
import json
import re
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser

from app import db as dbmod
from app import models

UA = "AldeaPucelaBuscador/1.0 (https://aldeapucela.org)"

# Troceado: ~350 tokens ≈ 1.400 caracteres de castellano, con solape de un párrafo corto.
MAX_CHARS = 1400
SOLAPE_CHARS = 200


def descargar(url, headers=None, timeout=30, reintentos=4):
    """GET con reintentos y espera creciente.

    Hace falta de verdad: una ingesta del chat son ~100 peticiones seguidas a NocoDB y de vez
    en cuando una se corta ("connection reset by peer"). Sin esto, el reindexado nocturno se
    caería a mitad cada pocas noches.
    """
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    for intento in range(reintentos):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or intento == reintentos - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if intento == reintentos - 1:
                raise
        time.sleep(2 ** intento)
    return None


def descargar_json(url, headers=None, timeout=60):
    return json.loads(descargar(url, headers, timeout))


class _ExtractorTexto(HTMLParser):
    """HTML → texto plano conservando los saltos de párrafo.

    ponytail: 30 líneas de html.parser en vez de arrastrar selectolax/bs4 solo para esto.
    """

    BLOQUES = {
        "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
        "blockquote", "pre", "section", "article", "aside", "figcaption",
    }
    IGNORAR = {"script", "style", "noscript", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.partes = []
        self._saltar = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.IGNORAR:
            self._saltar += 1
        elif tag in self.BLOQUES:
            self.partes.append("\n")

    def handle_endtag(self, tag):
        if tag in self.IGNORAR and self._saltar:
            self._saltar -= 1
        elif tag in self.BLOQUES:
            self.partes.append("\n")

    def handle_data(self, data):
        if not self._saltar:
            self.partes.append(data)

    def texto(self):
        t = "".join(self.partes)
        t = re.sub(r"[ \t\r\f\v]+", " ", t)
        t = re.sub(r" ?\n ?", "\n", t)
        return re.sub(r"\n{3,}", "\n\n", t).strip()


def html_a_texto(fragmento):
    if not fragmento:
        return ""
    p = _ExtractorTexto()
    p.feed(html.unescape(fragmento) if "&amp;" in fragmento else fragmento)
    p.close()
    return p.texto()


def _cola(texto, solape):
    """Últimos ~solape caracteres de texto, empezando en frontera de palabra."""
    cola = texto[-solape:]
    corte = cola.find(" ")
    return cola[corte + 1:] if corte != -1 else cola


def _piezas(texto, max_chars):
    """Parte el texto en piezas que quepan: por párrafos, luego frases, luego a lo bruto."""
    for parrafo in re.split(r"\n{2,}", texto):
        parrafo = parrafo.strip()
        if not parrafo:
            continue
        if len(parrafo) <= max_chars:
            yield parrafo
            continue
        actual = ""
        for frase in re.split(r"(?<=[.!?])\s+", parrafo):
            while len(frase) > max_chars:  # frase kilométrica sin puntuación
                yield frase[:max_chars]
                frase = frase[max_chars:]
            if actual and len(actual) + 1 + len(frase) > max_chars:
                yield actual
                actual = frase
            else:
                actual = f"{actual} {frase}".strip()
        if actual:
            yield actual


def trocear(texto, max_chars=MAX_CHARS, solape=SOLAPE_CHARS):
    """Trocea respetando párrafos, con solape entre chunks consecutivos.

    Cota real de un chunk: max_chars + solape + 2 (el solape se añade por delante y el
    separador son dos saltos de línea). El modelo trunca a 512 tokens de todas formas.
    """
    texto = (texto or "").strip()
    if not texto:
        return []
    if len(texto) <= max_chars:
        return [texto]

    chunks, actual = [], ""
    for pieza in _piezas(texto, max_chars):
        if actual and len(actual) + 2 + len(pieza) > max_chars:
            chunks.append(actual)
            actual = _cola(actual, solape)
        actual = f"{actual}\n\n{pieza}".strip() if actual else pieza
    if actual.strip():
        chunks.append(actual.strip())
    return chunks


def indexar(db, doc, textos, verbose=True):
    """Guarda un documento si su firma cambió. Devuelve nº de chunks indexados (0 si no cambió)."""
    if dbmod.firma_actual(db, doc["source_type"], doc["source_id"]) == doc["signature"]:
        return 0
    chunks = []
    for t in textos:
        chunks.extend(trocear(t) if isinstance(t, str) else [t])
    if not chunks:
        return 0
    planos = [c[0] if isinstance(c, tuple) else c for c in chunks]
    vectores = models.vectorizar_pasajes(planos)
    dbmod.guardar_documento(db, doc, chunks, vectores)
    db.commit()
    if verbose:
        print(f"  + {doc['source_type']}:{doc['source_id']} → {len(chunks)} chunks | {(doc['title'] or '')[:60]}")
    return len(chunks)


def indexar_lote(db, pendientes, verbose=False):
    """Indexa varios documentos de un chunk vectorizando todos sus textos de una vez.

    Medido con el chat: de uno en uno salían ~9 ventanas/s (45 min para el corpus entero);
    por lotes, 67/s (25.128 ventanas en 376 s). Reindexar sale barato, que es lo que permite
    iterar el troceado sin pensárselo.
    `pendientes` es una lista de (doc, texto, msg_ids).
    """
    if not pendientes:
        return 0
    vectores = models.vectorizar_pasajes([texto for _, texto, _ in pendientes])
    for (doc, texto, msg_ids), vector in zip(pendientes, vectores):
        dbmod.guardar_documento(db, doc, [(texto, msg_ids)], [vector])
        if verbose:
            print(f"  + {doc['source_id']}")
    db.commit()
    return len(pendientes)


def _autocomprobacion():
    # ponytail: un self-check en vez de una suite; falla si trocear/html_a_texto se rompen.
    assert trocear("") == []
    assert trocear("corto") == ["corto"]

    tope = MAX_CHARS + SOLAPE_CHARS + 2  # ver docstring de trocear()
    parrafos = "\n\n".join(f"Parrafo {i} " + "palabra " * 40 for i in range(12))
    trozos = trocear(parrafos)
    assert len(trozos) > 1, "un texto largo debe partirse"
    assert all(len(t) <= tope for t in trozos), [len(t) for t in trozos]
    assert "Parrafo 0" in trozos[0] and "Parrafo 11" in trozos[-1], "no puede perder contenido"

    seguido = "x" * (MAX_CHARS * 2 + 50)  # sin espacios ni puntuación
    assert all(len(t) <= tope for t in trocear(seguido)), [len(t) for t in trocear(seguido)]

    h = "<div><script>no</script><p>Hola <b>mundo</b></p><p>Segundo &amp; final</p></div>"
    assert html_a_texto(h) == "Hola mundo\n\nSegundo & final", repr(html_a_texto(h))
    print("common.py OK")


if __name__ == "__main__":
    _autocomprobacion()
