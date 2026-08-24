"""Carga el .env al importar el paquete, para que valga igual en la CLI y en la API.

ponytail: cinco líneas en vez de python-dotenv; el fichero es de cuatro claves.
"""

import os

try:
    with open(".env", encoding="utf-8") as f:
        for _linea in f:
            _linea = _linea.strip()
            if _linea and not _linea.startswith("#") and "=" in _linea:
                _k, _v = _linea.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())
except FileNotFoundError:
    pass
