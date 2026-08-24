"""Filtro de datos personales para el texto del chat, antes de indexarlo.

No pretende ser exhaustivo (eso es imposible con expresiones regulares): quita lo que
aparece de verdad en un chat vecinal y es identificable de forma fiable. La red de
seguridad de verdad es /forget y que el chat solo se muestre dentro del grupo.

Deliberadamente NO se intentan detectar direcciones postales: en un chat de barrio medio
mensaje menciona una calle ("han cortado Gamazo", "quedamos en Portugalete") y un filtro de
calles se llevaría por delante justo el contenido que da valor al buscador.
"""

import hashlib
import hmac
import os
import re

# Móviles y fijos españoles: 9 dígitos que empiezan por 6/7/8/9, con separadores opcionales.
# El (?<![\d-]) evita comerse trozos de números más largos (expedientes, importes, años).
TELEFONO = re.compile(r"(?<![\d\-/.])(?:\+34[ .-]?)?[6789]\d{2}[ .-]?\d{3}[ .-]?\d{3}(?![\d\-/])")
# Matrícula moderna: 4 dígitos + 3 consonantes (sin vocales ni Ñ/Q).
MATRICULA = re.compile(r"\b\d{4}[ -]?[BCDFGHJKLMNPRSTVWXYZ]{3}\b")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")
# IBAN español.
IBAN = re.compile(r"\bES\d{2}[ ]?(?:\d{4}[ ]?){5}\b", re.IGNORECASE)
# DNI/NIE.
DNI = re.compile(r"\b(?:[XYZ]?\d{7,8})[ -]?[A-HJ-NP-TV-Z]\b")

SUSTITUCIONES = [
    (EMAIL, "[email]"),
    (IBAN, "[iban]"),
    (TELEFONO, "[teléfono]"),
    (MATRICULA, "[matrícula]"),
    (DNI, "[dni]"),
]


def limpiar(texto):
    """Devuelve el texto con los datos personales reconocibles sustituidos."""
    if not texto:
        return texto
    for patron, marca in SUSTITUCIONES:
        texto = patron.sub(marca, texto)
    return texto


def sal():
    valor = os.environ.get("AUTHOR_HASH_SECRET")
    if not valor:
        raise SystemExit("falta AUTHOR_HASH_SECRET en .env (genera uno y no lo cambies)")
    return valor.encode()


def hash_autor(user_id, secreto=None):
    """Identificador estable y no reversible del autor: nunca guardamos el user_id en claro."""
    return hmac.new(secreto or sal(), str(user_id).encode(), hashlib.sha256).hexdigest()[:16]


def _autocomprobacion():
    # Tiene que limpiar esto
    casos = [
        ("llámame al 655 123 456", "[teléfono]"),
        ("mi tel es 655123456 gracias", "[teléfono]"),
        ("escribe a hola@aldeapucela.org", "[email]"),
        ("el coche 1234 BCD lleva ahí semanas", "[matrícula]"),
        ("el coche 1234BCD lleva ahí semanas", "[matrícula]"),
        ("mi dni 12345678Z", "[dni]"),
        ("ES91 2100 0418 4502 0005 1332", "[iban]"),
    ]
    for entrada, esperado in casos:
        assert esperado in limpiar(entrada), f"no limpió: {entrada!r} → {limpiar(entrada)!r}"

    # Y NO debe tocar esto (falsos positivos que romperían el buscador)
    intactos = [
        "el pleno aprobó 153.670 euros para el circuito",
        "quedamos en la calle Gamazo a las 20:00",
        "el expediente 2024/1234 sigue parado",
        "han cortado el tráfico en Arco de Ladrillo",
        "somos 8000 vecinos en el grupo",
        "el año 2026 será el del soterramiento",
    ]
    for t in intactos:
        assert limpiar(t) == t, f"falso positivo: {t!r} → {limpiar(t)!r}"
    print("pii.py OK")


if __name__ == "__main__":
    _autocomprobacion()
