"""python -m ingest [fuente...]   (por defecto: todas las disponibles)"""

import sys
import time

from app.db import conectar

FUENTES = {}


def _cargar(nombre):
    if nombre not in FUENTES:
        FUENTES[nombre] = __import__(f"ingest.{nombre}", fromlist=["ingestar"])
    return FUENTES[nombre]


DISPONIBLES = ["otrapucela", "forum", "chat"]


def main(argv):
    pedidas = [a for a in argv[1:] if not a.startswith("-")] or DISPONIBLES
    limite = None
    for a in argv[1:]:
        if a.startswith("--limite="):
            limite = int(a.split("=", 1)[1])

    db = conectar()
    for nombre in pedidas:
        if nombre not in DISPONIBLES:
            print(f"fuente desconocida: {nombre} (hay: {', '.join(DISPONIBLES)})")
            return 2
        t0 = time.time()
        docs, chunks = _cargar(nombre).ingestar(db, limite=limite)
        print(f"[{nombre}] {docs} documentos, {chunks} chunks en {time.time() - t0:.0f}s")

    fila = db.execute(
        "SELECT (SELECT count(*) FROM documents WHERE deleted=0), (SELECT count(*) FROM chunks)"
    ).fetchone()
    print(f"\nÍndice: {fila[0]} documentos, {fila[1]} chunks")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
