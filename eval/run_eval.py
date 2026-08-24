"""Evalúa la recuperación contra eval/golden.jsonl.

    .venv/bin/python -m eval.run_eval [--sin-reranker] [--limite=8]

Criterio de la Fase 1: recall@8 >= 0,8 y p95 < 2 s.
"""

import json
import sys
import time

from app.db import conectar
from app.search import buscar

GOLDEN = "eval/golden.jsonl"


def casos():
    with open(GOLDEN, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def acierta(resultado_url, esperadas):
    return any(e in resultado_url for e in esperadas)


def main(argv):
    usar_reranker = "--sin-reranker" not in argv
    limite = 8
    for a in argv:
        if a.startswith("--limite="):
            limite = int(a.split("=", 1)[1])

    db = conectar()
    buscar(db, "calentando modelos", limite=1, usar_reranker=usar_reranker)  # precarga

    aciertos, rr, tiempos, fallos = 0, [], [], []
    fugas = []
    pruebas = casos()
    for caso in pruebas:
        t0 = time.time()
        # Las consultas sobre el chat solo tienen sentido con el scope del grupo.
        r = buscar(
            db, caso["q"], scope=caso.get("scope", "public"),
            limite=limite, usar_reranker=usar_reranker,
        )
        tiempos.append((time.time() - t0) * 1000)
        # Prueba de privacidad: el scope público no puede devolver nunca chat.
        if caso.get("scope", "public") != "group":
            fugas += [x["url"] for x in r["results"] if x["source_type"] == "chat"]
        posicion = next(
            (i for i, x in enumerate(r["results"]) if acierta(x["url"], caso["urls"])), None
        )
        if posicion is None:
            fallos.append((caso["q"], [x["title"][:50] for x in r["results"][:3]]))
        else:
            aciertos += 1
            rr.append(1 / (posicion + 1))

    n = len(pruebas)
    for etiqueta, subconjunto in (
        ("web (foro + otrapucela)", [c for c in pruebas if c.get("scope", "public") != "group"]),
        ("chat (scope grupo)", [c for c in pruebas if c.get("scope") == "group"]),
    ):
        if subconjunto:
            ac = sum(1 for c in subconjunto if c["q"] not in [f[0] for f in fallos])
            print(f"  {etiqueta:<24} recall@{limite} {ac / len(subconjunto):.2f} ({ac}/{len(subconjunto)})")

    tiempos.sort()
    p95 = tiempos[min(int(n * 0.95), n - 1)]
    print(f"consultas:   {n}")
    print(f"recall@{limite}:    {aciertos / n:.2f}  ({aciertos}/{n})")
    print(f"MRR:         {sum(rr) / n:.3f}")
    print(f"latencia:    mediana {tiempos[n // 2]:.0f} ms | p95 {p95:.0f} ms | max {tiempos[-1]:.0f} ms")
    print(f"reranker:    {'sí' if usar_reranker else 'no'}")
    if fallos:
        print(f"\nfallos ({len(fallos)}):")
        for q, top in fallos:
            print(f"  · {q}")
            print(f"      top: {top}")

    print(f"\nfuga de chat al scope público: {'NINGUNA' if not fugas else f'{len(fugas)} ¡FALLO!'}")
    if fugas:
        for u in fugas[:5]:
            print(f"  · {u}")

    ok = aciertos / n >= 0.8 and p95 < 2000 and not fugas
    print(f"Criterio (recall@8 >= 0,80, p95 < 2000 ms, sin fugas): {'CUMPLE' if ok else 'NO CUMPLE'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
