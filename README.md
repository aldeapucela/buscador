# Buscador semántico Aldea Pucela

Buscador híbrido (vectorial + léxico + reranker, todo local en CPU) sobre el contenido del
ecosistema Aldea Pucela: foro Discourse, La Otra Pucela, eventos y chat de Telegram.
Se expone en el bot de Telegram y en las webs. Coste por búsqueda: 0 €.

Estado: **en marcha.** Desplegado en oracle-server (contenedor `buscador`) y conectado al bot
de Telegram, con los dos workflows de n8n publicados. Por ahora **solo se usa desde el bot**:
el buscador de las webs queda aparcado, así que el contenedor no está expuesto a internet.

- Despliegue y reindexado nocturno: [deploy/README.md](deploy/README.md)
- Widget para las webs: [web/buscador.js](web/buscador.js) (`web/demo.html` para probarlo)

## Cómo se usa

```bash
uv venv --python 3.12 && uv pip install -r requirements.txt
```

```bash
.venv/bin/python -m ingest              # indexa todas las fuentes (incremental)
```

```bash
.venv/bin/python -m app.pii && .venv/bin/python -m app.router && .venv/bin/python -m ingest.common && .venv/bin/python -m ingest.chat
```

Eso último son las autocomprobaciones: filtro de PII, enrutador de intención, troceado y
ventanas de conversación.

## Endpoints

| | |
|---|---|
| `GET /ask?q=…&scope=public\|group&limit=8` | **lo que debe llamar el bot**: enrutador + buscador |
| `GET /search?q=…&scope=…&sources=…&limit=8` | solo el buscador híbrido, sin enrutador |
| `POST /forget` | saca contenido del índice (ver Privacidad) |
| `GET /healthz` | estado y tamaño del índice |

`scope=group` y `/forget` exigen la cabecera `X-API-Key`.

```bash
.venv/bin/python -m eval.run_eval       # mide recall@8, MRR y latencia
```

```bash
API_KEY=loquesea .venv/bin/python -m uvicorn app.main:app --port 8100
```

`python3 scripts/check_sources.py` comprueba que las fuentes siguen accesibles, sin
dependencias. Con `--chat` mira además la tabla del chat en NocoDB (necesita `.env`).

## Resultados

Índice completo: **30.228 documentos y 31.092 chunks** en un fichero de **80 MB** — 58
artículos de La Otra Pucela, 370 topics del foro y **29.800 ventanas de conversación** del
chat (de 79.907 mensajes con texto aprovechable, el 83 % de los 95.712 del log).

Contra las 65 consultas de `eval/golden.jsonl` (50 de web + 15 de chat):

| 65 consultas | recall@8 | MRR | p95 (portátil) |
|---|---|---|---|
| con reranker | 0,94 | 0,859 | 587 ms |
| **sin reranker** (lo que corre en producción) | **0,95** | 0,833 | **45 ms** |

**El reranker se quedó fuera, y no solo por velocidad.** Con el corpus completo mejora el
orden muy poco (+0,026 de MRR) y *empeora* el recall (0,94 frente a 0,95; en las consultas de
chat, 0,73 frente a 0,80). En la Fase 1, con solo el piloto indexado, parecía que aportaba
más. Además el servidor tiene **un solo núcleo**, donde cuesta ~400 ms por candidato: 12 s por
consulta. Se enciende con `RERANKER=1` si algún día la máquina da para ello.

Que las 50 consultas web sigan en 1,00 con el chat dentro del índice es la señal importante:
añadir 29.796 ventanas no ha degradado nada lo anterior.

Los 4 fallos son consultas de chat en las que el buscador devuelve **otra** ventana del mismo
tema en vez de la etiquetada. El chat es repetitivo (la misma conversación ocurre veinte
veces), así que etiquetar una sola ventana como "la correcta" subestima la calidad real: la
cifra de 0,73 es un suelo, no un techo. Se deja así a propósito: ampliar las etiquetas con lo
que ya devuelve el sistema sería ajustar el examen al alumno.

Dos ajustes del reranker salieron de medir en la Fase 1 (siguen valiendo si se enciende), y los dos mejoran calidad **y** velocidad a la vez, porque el
reranker se despista con pasajes largos y con la cola de candidatos:

- 30 candidatos a rerankear en vez de 50: MRR 0,963 → 0,980 y 2,4x más rápido.
- Truncar el pasaje a 256 tokens: MRR 0,963 → 0,967 y 1,5x más rápido.

La ingesta es incremental de verdad: una segunda pasada de las fuentes web tarda 5 s, no
reindexa nada y ni siquiera carga los modelos.

**Vectorizar por lotes** (`indexar_lote`) fue necesario para el chat: de uno en uno salían
~9 ventanas/s (45 minutos el corpus entero), por lotes de 256 van a 67/s (25.128 ventanas en
376 s). Reindexar entero cuesta unos 8 minutos, que es lo que permite iterar el troceado sin
pensárselo, como pedía la propuesta.

### Troceado del chat

Un mensaje suelto no dice nada, así que la unidad indexada es una **ventana de conversación**:
mensajes seguidos del mismo topic con menos de 30 minutos de silencio entre ellos, hasta 10
mensajes o 1.200 caracteres, con 2 mensajes de solape con la ventana anterior. Cada línea va
como `Nombre: texto`, que es lo que da contexto de quién responde a quién (el log de NocoDB no
guarda `reply_to`). Se descartan los mensajes de menos de 15 caracteres y las ventanas de
menos de 80.

## Hallazgos de la Fase 0 (24-08-2026)

### Foro (`foro.aldeapucela.org`)

API pública sin autenticación. 20 categorías, 1.581 topics en total.

**Las categorías noindex se detectan solas**: el plugin propio `categories-noindex` inyecta
`<meta name="robots" content="noindex" data-categories-noindex="1">` en la página de la
categoría, y lo hereda en las subcategorías. La ingesta lo comprueba en caliente, así que
**no hay que mantener ninguna lista en espejo** dentro de este repo (esto elimina el riesgo
de indexar por error una categoría que el foro marca como no indexable).

Hoy están excluidas por noindex:

| Cat | Nombre | Topics | Por qué está bien excluirla |
|-----|--------|--------|------------------------------|
| 6 | Eventos culturales | 1.169 | Ya se indexan desde `eventos`, con estructura mucho mejor (fechas, venue, categoría) |
| 9 + 11 | La Otra Pucela + La viñeta | 58 | Duplican los artículos, que se indexan desde `otrapucela.org` |

→ **354 topics indexables** (General 186, Movilidad 60, Barrios y sus 11 subcategorías de
barrio 59, Integración Ferroviaria 38, Comunidad 11). Corpus pequeño: la ingesta completa
del foro es cuestión de minutos.

Detalles de API útiles: usar `site.json` para el árbol de categorías (`categories.json` no
trae las subcategorías, y el `topic_count` del padre no incluye las de los hijos: Barrios
aparece con 0 y en realidad tiene 59 repartidos en subcategorías).

### La Otra Pucela (`otrapucela.org`)

Sitio estático en GitHub Pages. 58 artículos en `sitemap.xml` (URLs `/p/{id}/{slug}/`),
desde mayo de 2025. Corpus pequeño.

- `feed.xml` trae **41 artículos con el texto completo en `content:encoded`** → para esos no
  hace falta scrapear nada.
- Los ~17 restantes (los más antiguos) hay que leerlos del HTML: el contenido cuelga del
  `<article data-article-id="...">` de la página.
- No existe ningún JSON global tipo `search-index.json` que reutilizar.

### Eventos (`eventos.aldeapucela.org/site-data.json`)

1.141 eventos (2,9 MB), 1.131 con descripción, de abril 2024 a octubre 2027. Campos ricos:
`title`, `descriptionHtml`, `startsAt`/`endsAt`, `venue`, `categoryLabel`, `isFree`,
`urlPath`, `sourceUrl` (apunta al topic del foro del que salió) y **`signature`**, que se
reutiliza tal cual para la incrementalidad.

### Chat de Telegram (NocoDB, tabla "Log grupo")

**El log está completo**: 95.712 mensajes desde el 31-10-2025 hasta hoy, a un ritmo actual de
2.400-3.000 mensajes por semana. No hace falta el plan B con Telethon para este periodo
(antes del 31-10-2025 no hay nada; si algún día se quiere ese histórico, ahí sí haría falta).

Campos: `Id`, `message_id`, `user_id`, `first_name`, `username`, `message_thread_id`, `text`,
`date`, `url`, `CreatedAt`. **No hay `reply_to`** (se pierde el hilo de respuestas dentro de
un topic) ni marca de adjuntos.

Forma del corpus (muestra de 6.000 mensajes repartida por todo el histórico):

- 83 % tienen texto aprovechable (≥ 15 caracteres); media de 122 caracteres, mediana 80, p90 256.
- 10 % no tienen texto (fotos, stickers, audios) y un 7 % son mensajes de menos de 15
  caracteres ("jaja", "👏"): se descartan en la ingesta.
- 71 topics distintos y 454 usuarios distintos en la muestra.

**Ojo con el campo `url`**: en los 13.540 mensajes sin `message_thread_id` (14 % del total,
los del topic General) la URL guardada sale mal formada, con doble barra
(`https://t.me/aldeapucela//92987`), y no funciona. La ingesta debe reconstruir el enlace a
partir de `message_thread_id` y `message_id` en lugar de fiarse del campo.

Con estos números el índice sale mucho más pequeño de lo previsto en la propuesta: unos
**12.000-13.000 chunks en total** entre las cuatro fuentes, que con vectores de 384
dimensiones son ~20 MB (la estimación de 200-250 MB de la propuesta asumía un modelo de 1.024
dimensiones).

## Enrutador de intención (Fase 3)

Lo que se puede contestar con datos exactos no pasa por el buscador semántico. `/ask` mira la
frase con expresiones regulares (determinista, sin IA) y decide:

| Entrada | Va a | Respuesta |
|---|---|---|
| `/finde`, `/hoy`, `/manana`, `/semana`, "¿qué hay este finde?" | agenda de eventos | eventos de esa ventana, en hora de Madrid |
| `/contratos TELECYL`, "contratos de X", "cuánto se le ha adjudicado a X" | API de contratos | nº de contratos, importe total y los mayores |
| todo lo demás | buscador híbrido | lo de siempre |

La respuesta trae un campo `tipo` (`eventos` \| `contratos` \| `busqueda`) para que el bot
sepa cómo formatearla. **Ante la duda se cae al buscador**: es mejor buscar de más que
contestar de menos.

Tres detalles que salieron de probarlo con frases reales, y que están en las
autocomprobaciones de `app/router.py` para que no se pierdan:

- **"el contrato de la basura es un escándalo" no es una consulta de datos.** Los patrones en
  lenguaje natural van anclados al principio del mensaje: una opinión que menciona un contrato
  va al buscador, no a la API.
- **"qué pasó el fin de semana pasado" mira al pasado** y la agenda solo sabe de lo que viene.
  Cualquier frase con marcas de pasado (`pasado`, `anterior`, `qué pasó`, `hubo`) queda vetada
  para la ruta de eventos.
- **Primero lo que empieza en la ventana, y luego lo que ya estaba en marcha.** Valladolid
  tiene muchas exposiciones que duran meses; ordenando solo por fecha de inicio, entierran los
  conciertos del sábado, que es justo lo que la gente pregunta. Lo que viene de antes se
  muestra como "en curso, hasta el 30/08" en vez de con su fecha original, que confunde.

Dónde vive el enrutador: **en la API, no en un nodo Code de n8n** como decía el plan. Es
lógica determinista con casos límite reales (husos horarios, findes que empiezan el viernes
por la tarde, falsos positivos), y aquí se puede probar en un segundo con
`python -m app.router`. n8n se queda de transporte: recibe el mensaje de Telegram, llama a
`/ask` y formatea la respuesta, que es lo que mejor hace.

## Privacidad (Fase 2)

- **El chat se indexa entero pero no se muestra en abierto.** Cada ventana de conversación se
  guarda con `visibility='group'`, y el filtro va **en el SQL** de la búsqueda: `scope=public`
  no puede devolver chat aunque el frontend se equivoque. `eval/run_eval.py` lo comprueba en
  cada ejecución y falla si se cuela una sola ventana.
- `scope=group` exige `X-API-Key`; la usa el bot desde n8n.
- **Los user_id nunca se guardan en claro**: se guarda un HMAC-SHA256 con `AUTHOR_HASH_SECRET`.
  Si cambias esa sal, los borrados por usuario dejan de encontrar lo antiguo.
- **Filtro de PII antes de indexar** (`app/pii.py`): teléfonos, emails, matrículas, DNI/NIE e
  IBAN se sustituyen por una marca. A propósito no se detectan direcciones postales: en un
  chat de barrio media conversación menciona una calle y el filtro se llevaría por delante
  justo lo que da valor al buscador.
- **`POST /forget`** acepta `message_id`, `user_id` o `source_id`: borra los documentos
  afectados (vectores, chunks y FTS) y anota una *lápida* en `tombstones`, que la ingesta
  consulta antes de insertar. Por eso un reindexado completo no resucita lo olvidado.
  Al olvidar un mensaje se borra la ventana entera que lo contiene; al reindexar, la ventana
  se reconstruye sin él.
- Conviene saber que el corpus del chat **contiene opiniones políticas** y temas delicados
  (art. 9 del RGPD). Es otro argumento para no exponerlo en la web pública.

## Decisiones tomadas al implementar

- **Sin dependencias de scraping**: el HTML se pasa a texto con `html.parser` de la stdlib
  (~30 líneas en `ingest/common.py`) en vez de meter selectolax o BeautifulSoup.
- **Sin cliente HTTP externo**: `urllib` basta para lo que hacemos.
- Los feeds de La Otra Pucela traen los **58 artículos** completos entre los tres
  (`feed.xml`, `vineta`, `podcast`), así que el camino de scraping del HTML existe pero casi
  nunca se usa.
- Del foro se indexa el primer post entero y, de las respuestas, las que aportan algo (con
  "me gusta", aceptadas como solución, o de más de 200 caracteres).

## Temas pendientes (fuera de este repo, decidir aparte)

- `../telegram/descargar.py` tiene `api_id`/`api_hash` de Telegram hardcodeados y un
  `sesion.session` versionado: conviene sacarlos a `.env`, gitignorarlos y rotar las
  credenciales. Es otro repo; aquí solo queda anotado.
- El workflow de logging de n8n no guarda `reply_to_message_id`: añadirlo mejoraría el
  troceado por conversación (hoy hay que inferir el hilo por proximidad temporal), y lo que
  no se captura hoy no se recupera mañana.
- Ese mismo workflow construye mal el campo `url` cuando el mensaje no tiene
  `message_thread_id`: quedan enlaces con doble barra que no funcionan. Aquí lo sorteamos
  reconstruyendo el enlace, pero conviene arreglarlo en origen.
- Conectar el flujo de moderación de n8n a `POST /forget` (Fase 2) para que un borrado por
  spam también salga del índice.

## Rendimiento real en producción

Medido en oracle-server (**un solo núcleo** arm64) con el índice completo y la máquina en
reposo:

| | tiempo |
|---|---|
| `/search` (10 consultas distintas) | 77-320 ms, mediana ~130 ms |
| `/ask` con `/finde` (agenda ya cacheada) | ~5 ms |
| `/ask` con `/finde` (primera vez, baja los 2,9 MB de la agenda) | ~450 ms |
| `/ask` con `/contratos EMPRESA`, empresa nueva | ~3,5 s |
| `/ask` con `/contratos EMPRESA`, empresa ya consultada | ~40 ms |

Los 3,5 s de `/contratos` **no son nuestros**: la API de contratos.aldeapucela.org tarda eso
en resolver una consulta nueva y luego la cachea ella (comprobado llamándola directamente,
sin pasar por el buscador). A partir de la segunda vez va a 50 ms.

## El bot de Telegram

El comando vive en el subflujo de n8n **[Aldea Pucela] Buscador del grupo**, que recibe
`comando`, `consulta`, `chat_id`, `thread_id` y `mensaje_a_olvidar`, llama a `/ask` (o a
`/forget`) y responde en el grupo con enlaces, fecha y fuente.

Va en un workflow aparte, no dentro del de comandos, por una razón concreta: **Telegram
admite un solo webhook por bot**, así que un segundo Telegram Trigger sobre el mismo bot se
pelearía con el que ya existe. El workflow de comandos solo necesita una rama nueva que llame
a este subflujo con "Execute Sub-workflow".

n8n habla con el buscador por la red interna de Docker (`http://buscador:8100`), sin salir a
internet ni pasar por nginx.
