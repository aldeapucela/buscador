#!/usr/bin/env python3
"""Fase 0: comprueba que las cuatro fuentes del buscador son accesibles y mide el corpus.

Uso:  python3 scripts/check_sources.py [--chat]
      --chat requiere NOCODB_URL / NOCODB_TOKEN / NOCODB_TABLE en el entorno o .env

Sin dependencias externas a propósito: esto tiene que poder correrse en cualquier sitio.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

UA = "AldeaPucelaBuscador/1.0 (https://aldeapucela.org)"
FORO = "https://foro.aldeapucela.org"
OTRAPUCELA = "https://otrapucela.org"
EVENTOS_JSON = "https://eventos.aldeapucela.org/site-data.json"


def cargar_env(ruta=".env"):
    # ponytail: parser de .env de tres líneas, en vez de depender de python-dotenv
    try:
        with open(ruta, encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if linea and not linea.startswith("#") and "=" in linea:
                    k, v = linea.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass


def fetch(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def fetch_json(url, headers=None):
    return json.loads(fetch(url, headers))


def foro_categorias():
    """Árbol completo de categorías (site.json incluye subcategorías; categories.json no)."""
    return fetch_json(f"{FORO}/site.json")["categories"]


def foro_es_noindex(cat):
    """El plugin categories-noindex inyecta <meta robots noindex> en la página de la
    categoría, y lo hereda en las subcategorías: se detecta desde fuera, sin mantener
    ninguna lista en espejo dentro de este repo."""
    return "data-categories-noindex" in fetch(f"{FORO}/c/{cat['slug']}/{cat['id']}")


def check_foro():
    print("\n== FORO (Discourse) ==")
    cats = foro_categorias()
    total = 0
    indexables = []
    for c in cats:
        noindex = foro_es_noindex(c)
        topics = c.get("topic_count", 0)
        total += topics
        padre = f" (sub de {c['parent_category_id']})" if c.get("parent_category_id") else ""
        flag = "NOINDEX (excluida)" if noindex else "indexable"
        print(f"  {c['id']:>3} {(c['name'][:30] + padre)[:38]:<40} topics={topics:>5}  {flag}")
        if not noindex:
            indexables.append((c["id"], c["slug"], topics))
    idx_topics = sum(t for _, _, t in indexables)
    print(f"  → {len(indexables)}/{len(cats)} categorías indexables, ~{idx_topics} topics de {total}")
    # Prueba de lectura real de un topic
    first = fetch_json(f"{FORO}/c/{indexables[0][1]}/{indexables[0][0]}.json")
    t = first["topic_list"]["topics"][0]
    det = fetch_json(f"{FORO}/t/{t['slug']}/{t['id']}.json")
    posts = det["post_stream"]["posts"]
    print(f"  lectura OK: topic {t['id']} con {len(posts)} post(s), {len(posts[0]['cooked'])} chars en el primero")
    return idx_topics


def check_otrapucela():
    print("\n== LA OTRA PUCELA ==")
    sitemap = fetch(f"{OTRAPUCELA}/sitemap.xml")
    urls = re.findall(r"<loc>([^<]+)</loc>", sitemap)
    arts = [u for u in urls if "/p/" in u]
    print(f"  sitemap: {len(urls)} URLs, {len(arts)} artículos")
    feed = fetch(f"{OTRAPUCELA}/feed.xml")
    items = feed.count("<item>")
    full_text = "content:encoded" in feed
    print(f"  feed.xml: {items} items, texto completo en content:encoded: {'sí' if full_text else 'NO'}")
    print(f"  → {len(arts) - items} artículos fuera del feed: hay que leerlos del HTML (data-article-id)")
    return len(arts)


def check_eventos():
    print("\n== EVENTOS ==")
    d = fetch_json(EVENTOS_JSON)
    evs = d["events"]
    con_desc = sum(1 for e in evs if e.get("descriptionHtml") or e.get("summary"))
    fechas = [e["startsAt"] for e in evs if e.get("startsAt")]
    print(f"  {len(evs)} eventos, {con_desc} con descripción")
    print(f"  rango: {min(fechas)[:10]} → {max(fechas)[:10]}")
    print(f"  trae 'signature' por evento: {'sí' if 'signature' in evs[0] else 'no'} (reutilizable para incrementalidad)")
    return len(evs)


def check_chat():
    print("\n== CHAT (NocoDB 'Log grupo') ==")
    url, token, table = (
        os.environ.get("NOCODB_URL"),
        os.environ.get("NOCODB_TOKEN"),
        os.environ.get("NOCODB_TABLE"),
    )
    if not (url and token and table):
        print("  SALTADO: faltan NOCODB_URL / NOCODB_TOKEN / NOCODB_TABLE (ver .env.example)")
        return None
    base = url.rstrip("/")
    headers = {"xc-token": token}
    q = urllib.parse.urlencode({"limit": 1, "sort": "-Id"})
    d = fetch_json(f"{base}/api/v2/tables/{table}/records?{q}", headers)
    rows = d.get("list", [])
    total = d.get("pageInfo", {}).get("totalRows")
    print(f"  filas: {total}")
    if rows:
        print(f"  campos: {', '.join(sorted(rows[0]))}")
        print(f"  fila más reciente: {json.dumps(rows[0], ensure_ascii=False)[:300]}")
    q = urllib.parse.urlencode({"limit": 1, "sort": "Id"})
    old = fetch_json(f"{base}/api/v2/tables/{table}/records?{q}", headers).get("list", [])
    if old:
        print(f"  fila más antigua: {json.dumps(old[0], ensure_ascii=False)[:300]}")
    return total


def main():
    cargar_env()
    checks = [check_foro, check_otrapucela, check_eventos]
    if "--chat" in sys.argv:
        checks.append(check_chat)
    fallos = 0
    for c in checks:
        try:
            c()
        except Exception as e:  # noqa: BLE001 - queremos ver todas las fuentes, no parar en la primera
            fallos += 1
            print(f"  ERROR en {c.__name__}: {e}")
    print("\nFuentes con error:", fallos)
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
