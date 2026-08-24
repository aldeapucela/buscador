"""Ingesta del foro Discourse (foro.aldeapucela.org).

Las categorías excluidas no se listan aquí: el plugin categories-noindex del foro marca sus
páginas con <meta robots noindex>, y eso se detecta en caliente. Así la exclusión la manda
siempre el foro y no una lista en espejo que se quedaría vieja.
"""

import time

from app.db import firma_actual
from ingest.common import descargar, descargar_json, html_a_texto, indexar

FORO = "https://foro.aldeapucela.org"
PAUSA = 0.3  # cortesía con un foro de la propia comunidad


def categorias_indexables():
    """Categorías del foro que no están marcadas como noindex (site.json trae subcategorías)."""
    for cat in descargar_json(f"{FORO}/site.json")["categories"]:
        pagina = descargar(f"{FORO}/c/{cat['slug']}/{cat['id']}")
        if "data-categories-noindex" not in pagina:
            yield cat
        else:
            print(f"  (excluida por noindex: {cat['name']})")


def topics_de_categoria(cat):
    url = f"{FORO}/c/{cat['slug']}/{cat['id']}.json"
    while url:
        lista = descargar_json(url)["topic_list"]
        topics = lista.get("topics") or []
        yield from topics
        siguiente = lista.get("more_topics_url")
        # more_topics_url viene como '/c/general/4?page=1' y la API pide el .json antes del ?
        url = f"{FORO}{siguiente}".replace("?", ".json?", 1) if (siguiente and topics) else None
        time.sleep(PAUSA)


def texto_del_topic(topic):
    """Devuelve (texto, fecha_ultimo_post). Indexa el primer post y las respuestas con chicha."""
    d = descargar_json(f"{FORO}/t/{topic['slug']}/{topic['id']}.json")
    posts = d["post_stream"]["posts"]
    partes = []
    for i, p in enumerate(posts):
        cuerpo = html_a_texto(p.get("cooked"))
        if not cuerpo:
            continue
        # El primer post siempre; del resto, lo que aporte algo (con likes, aceptado o largo).
        util = i == 0 or p.get("accepted_answer") or (p.get("like_count") or 0) > 0 or len(cuerpo) > 200
        if util:
            partes.append(f"{p.get('username', '')}: {cuerpo}" if i else cuerpo)
    return "\n\n".join(partes)


def ingestar(db, limite=None):
    total_docs = total_chunks = 0
    for cat in categorias_indexables():
        print(f"[foro] categoría {cat['id']} {cat['name']}")
        for topic in topics_de_categoria(cat):
            if limite and total_docs >= limite:
                return total_docs, total_chunks
            firma = f"{topic.get('last_posted_at')}|{topic.get('posts_count')}"
            doc = {
                "source_type": "forum",
                "source_id": str(topic["id"]),
                "url": f"{FORO}/t/{topic['slug']}/{topic['id']}",
                "title": topic.get("title"),
                "author_hash": None,
                "created_at": topic.get("created_at"),
                "updated_at": topic.get("last_posted_at"),
                "expires_at": None,
                "visibility": "public",
                "signature": firma,
            }
            if firma_actual(db, "forum", doc["source_id"]) == firma:
                continue
            texto = texto_del_topic(topic)
            if not texto:
                continue
            n = indexar(db, doc, [f"{topic['title']}\n\n{texto}"])
            total_docs += 1
            total_chunks += n
            time.sleep(PAUSA)
    return total_docs, total_chunks
