"""Enrutador de intención: lo que se puede responder con datos exactos no pasa por el buscador.

Preguntar "¿qué hay este finde?" a un buscador semántico es pedirle que adivine algo que
sabemos con certeza. Aquí se detecta la intención con expresiones regulares (determinista,
sin IA) y se contesta con la fuente de datos que toca:

    /finde, /hoy…      → eventos.aldeapucela.org/site-data.json filtrado por fechas
    /contratos EMPRESA → API de contratos.aldeapucela.org
    lo demás           → el buscador semántico

Ante la duda, se cae al buscador: es mejor buscar de más que responder de menos.
"""

import re
import time
import urllib.parse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.search import buscar
from ingest.common import descargar_json

MADRID = ZoneInfo("Europe/Madrid")
EVENTOS_JSON = "https://eventos.aldeapucela.org/site-data.json"
EVENTOS_WEB = "https://eventos.aldeapucela.org"
CONTRATOS_API = "https://contratos.aldeapucela.org/api/contracts"
CONTRATOS_WEB = "https://contratos.aldeapucela.org"

CACHE_SEGUNDOS = 1800
_cache_eventos = {"cuando": 0, "datos": None}

# El finde empieza el viernes por la tarde, como en la web de eventos.
VIERNES = 4
INICIO_FINDE = 15


def _sin_tildes(t):
    tabla = str.maketrans("áéíóúüñ", "aeiouun")
    return t.lower().translate(tabla)


# Una intención se reconoce por su comando o por unas pocas frases inequívocas. Nada de
# adivinar: si la frase no encaja claramente, va al buscador.
VENTANAS = {
    "hoy": [r"^/hoy\b", r"\bque (hay|se puede hacer|hacer) hoy\b", r"\bplanes para hoy\b",
            r"\beventos de hoy\b", r"\bque hay hoy\b"],
    "manana": [r"^/manana\b", r"\bque hay manana\b", r"\bplanes para manana\b",
               r"\beventos de manana\b"],
    "finde": [r"^/finde\b", r"^/findesemana\b", r"\beste finde\b", r"\beste fin de semana\b",
              r"\bque hay el finde\b", r"\bplanes para el finde\b", r"\bque hacer el finde\b",
              r"\beventos del finde\b"],
    "semana": [r"^/semana\b", r"\bque hay esta semana\b", r"\bplanes para esta semana\b",
               r"\beventos de esta semana\b"],
}

# La agenda solo sabe de lo que viene. Si la pregunta mira al pasado, es cosa del buscador.
PASADO = re.compile(r"\b(pasad[oa]|anterior|el ano pasado|que paso|que hubo|hubo)\b")

# Anclados al principio del mensaje a propósito: "el contrato de la basura es un escándalo"
# es una opinión, no una consulta de datos, y debe ir al buscador.
CONTRATOS = [
    r"^/contratos?\b\s*(?P<arg>.*)$",
    r"^¿?contratos? (?:de|con|a)\s+(?P<arg>.{2,60})$",
    r"^¿?cuanto (?:se le )?(?:ha|han) (?:adjudicado|pagado) a\s+(?P<arg>.{2,60})$",
    r"^¿?adjudicaciones? (?:de|a)\s+(?P<arg>.{2,60})$",
]


def detectar(texto):
    """(tipo, argumento). tipo ∈ {'eventos', 'contratos', 'busqueda'}."""
    limpio = _sin_tildes((texto or "").strip())
    if not PASADO.search(limpio):
        for ventana, patrones in VENTANAS.items():
            if any(re.search(p, limpio) for p in patrones):
                return "eventos", ventana
    for patron in CONTRATOS:
        m = re.search(patron, limpio)
        if m:
            arg = (m.group("arg") or "").strip(" ?¿.,")
            # "/contratos" a secas no identifica ninguna empresa: que lo diga el usuario.
            return "contratos", arg
    return "busqueda", texto


def ventana_fechas(clave, ahora=None):
    """Inicio y fin (aware, Europe/Madrid) de la ventana temporal pedida."""
    ahora = ahora or datetime.now(MADRID)
    hoy = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    fin_del_dia = {"hour": 23, "minute": 59, "second": 59}

    if clave == "hoy":
        return hoy, hoy.replace(**fin_del_dia)
    if clave == "manana":
        m = hoy + timedelta(days=1)
        return m, m.replace(**fin_del_dia)
    if clave == "semana":
        return ahora, (hoy + timedelta(days=7)).replace(**fin_del_dia)

    # finde: si ya estamos en él (viernes por la tarde en adelante), el que corre; si no, el
    # siguiente. Ojo: un viernes por la mañana el finde es el de esa misma tarde.
    dias_al_viernes = (VIERNES - hoy.weekday()) % 7
    viernes = hoy + timedelta(days=dias_al_viernes)
    if hoy.weekday() > VIERNES:  # sábado o domingo: el finde es el que ya empezó
        viernes = hoy - timedelta(days=hoy.weekday() - VIERNES)
    # Si el finde ya ha empezado, la ventana arranca ahora: un sábado a mediodía no interesa
    # lo que se acabó el viernes por la noche.
    inicio = max(viernes.replace(hour=INICIO_FINDE), ahora)
    domingo = viernes + timedelta(days=2)
    return inicio, domingo.replace(**fin_del_dia)


def _eventos():
    if time.time() - _cache_eventos["cuando"] > CACHE_SEGUNDOS or not _cache_eventos["datos"]:
        _cache_eventos["datos"] = descargar_json(EVENTOS_JSON)["events"]
        _cache_eventos["cuando"] = time.time()
    return _cache_eventos["datos"]


def _fecha(valor):
    try:
        return datetime.fromisoformat(valor).astimezone(MADRID)
    except (TypeError, ValueError):
        return None


def eventos_en(clave, ahora=None, limite=12, eventos=None):
    inicio, fin = ventana_fechas(clave, ahora)
    encontrados = []
    for e in (eventos if eventos is not None else _eventos()):
        empieza = _fecha(e.get("startsAt"))
        if not empieza:
            continue
        acaba = _fecha(e.get("endsAt")) or empieza
        # Cuenta también lo que ya está en marcha y sigue abierto durante la ventana.
        if empieza <= fin and acaba >= inicio:
            encontrados.append((empieza, acaba, e))
    # Primero lo que empieza dentro de la ventana, por orden; después lo que ya venía de
    # antes y sigue abierto. Si no, las exposiciones de fondo (que son muchas y duran meses)
    # entierran los conciertos del sábado, que es justo lo que la gente pregunta.
    encontrados.sort(key=lambda x: (x[0] < inicio, max(x[0], inicio)))
    return {
        "tipo": "eventos",
        "ventana": clave,
        "desde": inicio.isoformat(),
        "hasta": fin.isoformat(),
        "total": len(encontrados),
        "url": f"{EVENTOS_WEB}/",
        "results": [
            {
                "title": e.get("title"),
                "url": f"{EVENTOS_WEB}{e.get('urlPath', '')}",
                # Lo que ya venía de antes no lleva su fecha original (confunde), sino hasta cuándo sigue.
                "date": (
                    e.get("startsAtLabel") or empieza.strftime("%d/%m %H:%M")
                    if empieza >= inicio
                    else f"en curso, hasta el {acaba.strftime('%d/%m')}"
                ),
                "ongoing": empieza < inicio,
                "venue": e.get("venueLabel") or e.get("venue"),
                "category": e.get("categoryLabel"),
                "free": e.get("isFree"),
            }
            for empieza, acaba, e in encontrados[:limite]
        ],
    }


def contratos_de(consulta, limite=8):
    if not consulta:
        return {
            "tipo": "contratos",
            "error": "di de qué empresa: /contratos TELECYL",
            "url": f"{CONTRATOS_WEB}/",
            "results": [],
        }
    params = urllib.parse.urlencode(
        {"q": consulta, "limit": limite, "sort_by": "amount", "sort_direction": "desc"}
    )
    d = descargar_json(f"{CONTRATOS_API}?{params}")
    return {
        "tipo": "contratos",
        "consulta": consulta,
        "total": d.get("total", 0),
        "importe_total": d.get("total_amount", 0),
        "url": f"{CONTRATOS_WEB}/?q={urllib.parse.quote(consulta)}",
        "results": [
            {
                "title": r.get("title"),
                "supplier": r.get("supplier_name"),
                "amount": r.get("amount_value"),
                "date": r.get("contract_date") or r.get("period_start"),
                "area": r.get("area_or_body"),
                "url": f"{CONTRATOS_WEB}{r.get('public_contract_url') or '/'}",
            }
            for r in (d.get("rows") or [])[:limite]
        ],
    }


def responder(db, texto, scope="public", limite=8):
    tipo, arg = detectar(texto)
    if tipo == "eventos":
        return eventos_en(arg, limite=limite)
    if tipo == "contratos":
        return contratos_de(arg, limite=limite)
    return {"tipo": "busqueda", **buscar(db, texto, scope=scope, limite=limite)}


def _autocomprobacion():
    # Intención: comandos y frases claras
    assert detectar("/finde") == ("eventos", "finde")
    assert detectar("¿qué hay este finde?") == ("eventos", "finde")
    assert detectar("planes para hoy") == ("eventos", "hoy")
    assert detectar("/hoy") == ("eventos", "hoy")
    assert detectar("qué hay esta semana") == ("eventos", "semana")
    assert detectar("/contratos TELECYL") == ("contratos", "telecyl")
    assert detectar("contratos de Telecyl") == ("contratos", "telecyl")
    assert detectar("cuánto se le ha adjudicado a Gotion") == ("contratos", "gotion")
    assert detectar("/contratos")[0] == "contratos" and detectar("/contratos")[1] == ""

    # Lo ambiguo se va al buscador, que es el comportamiento seguro
    for t in ["por qué no se soterra el tren", "el contrato de la basura es un escándalo",
              "qué pasó el fin de semana pasado en Delicias", "dónde comer un kebab",
              "qué hubo este finde en la plaza", "el finde pasado hubo mercadillo"]:
        assert detectar(t)[0] == "busqueda", t

    # Ventanas temporales (con un "ahora" fijo para que no dependa del día que se ejecute)
    lunes = datetime(2026, 8, 24, 10, 0, tzinfo=MADRID)          # lunes
    ini, fin = ventana_fechas("finde", lunes)
    assert (ini.day, ini.hour) == (28, 15) and fin.day == 30, (ini, fin)   # vie 28 → dom 30

    sabado = datetime(2026, 8, 29, 12, 0, tzinfo=MADRID)
    ini, fin = ventana_fechas("finde", sabado)
    # El finde en curso (no el siguiente), y arrancando "ahora": lo del viernes ya pasó.
    assert ini == sabado and fin.day == 30, (ini, fin)

    viernes_manana = datetime(2026, 8, 28, 9, 0, tzinfo=MADRID)
    ini, fin = ventana_fechas("finde", viernes_manana)
    assert (ini.day, ini.hour) == (28, 15), ini                  # el de esa misma tarde

    ini, fin = ventana_fechas("hoy", lunes)
    assert ini.day == fin.day == 24 and ini.hour == 0 and fin.hour == 23

    ini, fin = ventana_fechas("manana", lunes)
    assert ini.day == fin.day == 25

    # Filtrado y orden de eventos, con datos de mentira para no depender de la red
    falsos = [
        {"title": "Expo que viene de largo", "startsAt": "2025-10-20T10:00:00+02:00",
         "endsAt": "2026-12-31T20:00:00+01:00", "urlPath": "/e/1/"},
        {"title": "Concierto del sábado", "startsAt": "2026-08-29T21:00:00+02:00",
         "endsAt": "2026-08-29T23:00:00+02:00", "urlPath": "/e/2/"},
        {"title": "Teatro del viernes", "startsAt": "2026-08-28T20:00:00+02:00",
         "endsAt": "2026-08-28T22:00:00+02:00", "urlPath": "/e/3/"},
        {"title": "Nada que ver, es en octubre", "startsAt": "2026-10-05T20:00:00+02:00",
         "endsAt": "2026-10-05T22:00:00+02:00", "urlPath": "/e/4/"},
    ]
    r = eventos_en("finde", lunes, eventos=falsos)
    titulos = [x["title"] for x in r["results"]]
    assert "Nada que ver, es en octubre" not in titulos, titulos      # fuera de la ventana
    assert len(titulos) == 3, titulos                                 # los otros tres sí
    # Primero lo que empieza en la ventana (por orden), y lo de fondo al final
    assert titulos == ["Teatro del viernes", "Concierto del sábado", "Expo que viene de largo"], titulos
    assert not r["results"][0]["ongoing"]
    assert r["results"][-1]["ongoing"] and "en curso" in r["results"][-1]["date"]
    print("router.py OK")


if __name__ == "__main__":
    _autocomprobacion()
