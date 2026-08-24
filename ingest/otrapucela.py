"""Ingesta de La Otra Pucela (otrapucela.org, sitio estático).

Los feeds traen el artículo entero en content:encoded, así que la mayoría no hay que
scrapearla. Los que no están en ningún feed (los más antiguos) se leen del HTML.
"""

import hashlib
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from app.db import firma_actual
from ingest.common import descargar, html_a_texto, indexar

SITIO = "https://otrapucela.org"
FEEDS = ["/feed.xml", "/vineta/feed.xml", "/podcast.xml"]
NS = {"content": "http://purl.org/rss/1.0/modules/content/"}


def fecha_iso(valor):
    """Los feeds dan la fecha en RFC 822 ('Tue, 07 Apr 2026 07:05:59 GMT'). Se guarda en ISO
    porque el resto del sistema compara y recorta fechas por los 10 primeros caracteres."""
    valor = (valor or "").strip()
    if not valor:
        return ""
    try:
        return parsedate_to_datetime(valor).isoformat()
    except (TypeError, ValueError):
        return valor  # las páginas HTML ya traen <time datetime> en ISO


def _texto(nodo, etiqueta, ns=None):
    hijo = nodo.find(etiqueta, ns) if ns else nodo.find(etiqueta)
    return (hijo.text or "") if hijo is not None else ""


def articulos_del_feed():
    """{url: {title, fecha, texto}} con lo que viene completo en los feeds."""
    encontrados = {}
    for ruta in FEEDS:
        try:
            raiz = ET.fromstring(descargar(f"{SITIO}{ruta}"))
        except ET.ParseError as e:
            print(f"  (feed {ruta} ilegible: {e})")
            continue
        for item in raiz.iter("item"):
            url = _texto(item, "link").strip()
            cuerpo = _texto(item, "content:encoded", NS) or _texto(item, "description")
            if url:
                encontrados[url] = {
                    "title": _texto(item, "title").strip(),
                    "fecha": fecha_iso(_texto(item, "pubDate")),
                    "texto": html_a_texto(cuerpo),
                }
    return encontrados


def urls_del_sitemap():
    xml = descargar(f"{SITIO}/sitemap.xml")
    urls = re.findall(r"<loc>([^<]+)</loc>", xml)
    return [u for u in urls if "/p/" in u]


def articulo_del_html(url):
    """Lee el artículo de la página: el contenido cuelga de <article data-article-id>."""
    pagina = descargar(url)
    m = re.search(r'<article[^>]*data-article-id="\d+"[^>]*>(.*?)</article>', pagina, re.S)
    cuerpo = m.group(1) if m else pagina
    titulo = re.search(r"<title>(.*?)</title>", pagina, re.S)
    fecha = re.search(r'<time[^>]*datetime="([^"]+)"', pagina)
    return {
        "title": html_a_texto(titulo.group(1)) if titulo else url,
        "fecha": fecha_iso(fecha.group(1)) if fecha else "",
        "texto": html_a_texto(cuerpo),
    }


def ingestar(db, limite=None):
    del_feed = articulos_del_feed()
    urls = urls_del_sitemap()
    # Alguna entrada del feed puede no estar en el sitemap (la viñeta, el podcast).
    todas = list(dict.fromkeys(urls + list(del_feed)))
    print(f"[otrapucela] {len(todas)} artículos ({len(del_feed)} completos en feeds)")

    docs = chunks = 0
    for url in todas:
        if limite and docs >= limite:
            break
        art = del_feed.get(url)
        origen = "feed"
        if not art or len(art["texto"]) < 200:
            art = articulo_del_html(url)
            origen = "html"
        if not art["texto"]:
            print(f"  (sin texto: {url})")
            continue
        # La firma cubre también título y fecha: si se arregla cómo se extrae un metadato,
        # el reindexado lo propaga solo en vez de saltarse el documento por "no ha cambiado".
        firma = hashlib.sha256(
            f"{art['title']}|{art['fecha']}|{art['texto']}".encode()
        ).hexdigest()[:16]
        doc = {
            "source_type": "otrapucela",
            "source_id": url,
            "url": url,
            "title": art["title"],
            "author_hash": None,
            "created_at": art["fecha"],
            "updated_at": art["fecha"],
            "expires_at": None,
            "visibility": "public",
            "signature": firma,
        }
        if firma_actual(db, "otrapucela", url) == firma:
            continue
        n = indexar(db, doc, [f"{art['title']}\n\n{art['texto']}"])
        if n:
            docs += 1
            chunks += n
            if origen == "html":
                print("    (leído del HTML)")
    return docs, chunks
