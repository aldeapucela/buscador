# ToDo del buscador

Cosas a depurar e ideas recogidas tras las primeras pruebas reales del bot (25-08-2026). Nada
de esto está implementado todavía; se prioriza y se aborda en otra sesión.

## Bugs

### 1. `/contratos`: los enlaces están rotos (falta el slug)

**Confirmado** revisando la ejecución real (n8n, workflow `vNnEVp5BiR7LyasK`, ejecución 12550,
`/contratos Eventival`). Enlazamos:

```
https://contratos.aldeapucela.org/contratos/6048467299674538/
```

y la ruta real es:

```
https://contratos.aldeapucela.org/contratos/6048467299674538/contratacion-de-herramienta-de-gestion-de-festivales-eventival-para-la/
```

**Causa**: el campo `public_contract_url` que devuelve la API de contratos solo trae
`/contratos/{id}/`, sin el slug. El slug lo construye el sitio a partir del título:
minúsculas, sin tildes, no-alfanumérico → guión, truncado a 70 caracteres. Verificado
reproduciendo el algoritmo contra varios títulos reales (incluido el de Eventival) y coincide
carácter a carácter.

**Arreglo**: en `app/router.py::contratos_de`, generar el slug del `title` con esa misma
regla (`unicodedata.normalize('NFKD', ...).encode('ascii','ignore')` + `lower` + regex +
`[:70]`) y añadirlo a la URL: `/contratos/{id}/{slug}/`. Antes de darlo por bueno, confirmar la
regla de truncado con 5-10 títulos más (por si el corte real respeta palabra completa y el de
70 caracteres a veces no).

### 2. `/buscar`: a veces enlaza a mensajes del chat que ya no existen

Ocurre porque el índice **no detecta cuándo un mensaje desaparece de Telegram**. Solo se borra
del índice cuando alguien llama explícitamente a `/olvidar` (o `POST /forget`); si un mensaje
se borra por otra vía —el propio autor lo borra, un admin modera, Telegram lo retira— el índice
no se entera y la ventana de conversación sigue apareciendo en resultados con un enlace muerto.

**Mitigaciones a valorar** (ninguna trivial, requieren decidir antes de implementar):
- Comprobar periódicamente contra la tabla "Log grupo" de NocoDB si el mensaje sigue existiendo
  (si allí también desapareciera al borrarse) y purgar lo que ya no está.
- Verificación bajo demanda: cuando el bot devuelve un resultado de chat, intentar reenviarlo o
  consultarlo vía Bot API (`forwardMessage` a un chat propio, o similar) y si falla, no
  mostrarlo — coste en latencia y en llamadas a la API de Telegram, a medir.
- Aceptar el ruido y mitigar en la presentación: nada evita el enlace roto, pero se podría
  avisar en el propio mensaje ("puede que este enlace ya no esté disponible").
- Reducir la ventana: los mensajes de chat caducan solos pasado un tiempo si esto resulta
  demasiado ruidoso (compensa menos cobertura por menos frustración).

Pendiente decidir cuál (o combinación) antes de tocar código.

## Nuevas funcionalidades

### 3. `/comandos` (o `/ayuda`): listado de comandos disponibles

El bot debe responder a `/comandos@AldeaPucela_bot` (y variantes sin @) con la lista de
comandos y cómo se usan. Encaja como un caso más en el switch de `Leer el comando` del
subflujo `[Aldea Pucela] Buscador del grupo`, con una rama de texto fijo (no necesita llamar al
buscador). Mantener la lista sincronizada a mano con los comandos reales, o generarla desde una
única fuente de verdad si acaban siendo muchos.

### 4. Renombrar `/finde` a `/eventos` con parámetros

Sustituir el comando único `/finde` por `/eventos <parámetro> <valor>`, no sensible a
mayúsculas/minúsculas ni a tildes (como ya hace `app/router.py::_sin_tildes`). Parámetros
propuestos por el usuario:

- `/eventos fecha finde` — el fin de semana (lo que hoy hace `/finde`)
- `/eventos fecha semana` — el resto de la semana en curso, desde hoy hasta el domingo
- `/eventos fecha octubre` — un mes concreto, por nombre
- `/eventos tipo exposiciones` — eventos de una categoría
- `/eventos lugar Museo Patio Herreriano` — eventos de un espacio concreto

**Lo que ya existe y se reutiliza**: `app/router.py::eventos_en` + `ventana_fechas` cubren
`hoy`/`manana`/`finde`/`semana`. Falta:
- Parsear meses por nombre (`octubre` → rango 1-31 de octubre del año en curso o el próximo si
  ya pasó) y aparcar el resto de wording (`fecha finde`, `fecha semana` son alias de lo que ya
  hay, solo cambia la sintaxis del comando).
- Filtrar por `categoryLabel` (tipo) y por `venue`/`venueLabel` (lugar) sobre el mismo
  `site-data.json`, en vez de por fecha. Los nombres de categoría vienen en `d['filters']` del
  JSON (lista plana: "Charlas", "Cine", "Comedia"...); los de espacio en `d['spaces']`
  (`{slug, name, canonicalVenue}`). Conviene aceptar coincidencia parcial/insensible a tildes
  (igual que ya se hace para las categorías del foro) porque el usuario no va a escribir el
  nombre exacto siempre.
- Decidir qué pasa si se combinan parámetros (¿`/eventos tipo exposiciones lugar Museo...`?) —
  de momento no lo ha pedido el usuario, no diseñar de más (YAGNI) hasta que haga falta.

### 5. El enlace "Ver la agenda completa" debe ser específico, no siempre la portada

Ahora mismo `eventos_en()` devuelve siempre `url: f"{EVENTOS_WEB}/"` (la portada). Debería
apuntar a la página que corresponde a lo que se ha preguntado. **Confirmado que existen** estas
rutas en `eventos.aldeapucela.org`:

| Consulta | Página |
|---|---|
| `fecha finde` | `/fin-de-semana/` |
| `fecha semana` | `/esta-semana/` |
| `fecha hoy` / `manana` | `/hoy/` (no hay `/manana/`: comprobar si existe o usar la portada para ese caso) |
| `tipo <categoría>` | `/t/<slug-categoria>/` (confirmado con `/t/exposiciones/` → 200) |
| `lugar <espacio>` | `/espacios/<slug-del-espacio>/` (confirmado con `/espacios/museo-patio-herreriano/` → 200; el slug viene en `d['spaces']`) |
| `fecha <mes>` | sin confirmar; probablemente no existe página propia por mes — revisar `dist/` del repo `eventos` o usar la portada con filtro por query string si lo soporta |

Pendiente de comprobar: cómo se deriva el slug de una categoría a partir de su nombre visible
(¿es solo `slugify(nombre)`, como en contratos? `exposiciones` → `Exposiciones` cuadra con eso,
pero conviene verificarlo con alguna categoría con espacios o tildes antes de generalizar).

## Notas

- Ninguno de estos puntos está implementado; son la lista de partida para la siguiente sesión.
- Los bugs (1 y 2) son más urgentes que las funcionalidades nuevas: afectan a lo que ya está en
  producción y generan desconfianza en el bot si se acumulan enlaces rotos.
- Recordar publicar en n8n tras cualquier cambio a los workflows — ver
  [deploy/README.md](deploy/README.md#️-este-n8n-usa-versiones-publicadas).
