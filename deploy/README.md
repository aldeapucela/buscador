# Despliegue en oracle-server

El servidor es un cliente de git: se despliega **por GitHub**, nunca copiando ficheros.

## Primera vez

```bash
ssh oracle-server 'mkdir -p ~/apps && cd ~/apps && git clone https://github.com/aldeapucela/buscador.git'
```

Crear `~/apps/buscador/.env` en el servidor (no está en git) con `NOCODB_URL`, `NOCODB_TOKEN`,
`NOCODB_TABLE`, y un `AUTHOR_HASH_SECRET` y un `API_KEY` propios del servidor:

```bash
ssh oracle-server 'cd ~/apps/buscador && umask 077 && printf "AUTHOR_HASH_SECRET=%s\nAPI_KEY=%s\n" "$(openssl rand -hex 32)" "$(openssl rand -hex 24)" >> .env'
```

```bash
ssh oracle-server 'cd ~/apps/buscador && docker compose up -d --build'
```

Construir el índice la primera vez (unos 15 minutos; el contenedor ya responde mientras tanto,
con el índice vacío):

```bash
ssh oracle-server 'cd ~/apps/buscador && docker compose exec -T buscador python -m ingest'
```

## Actualizar

```bash
ssh oracle-server 'cd ~/apps/buscador && git pull && docker compose up -d --build && docker compose ps'
```

## Reindexado nocturno

`crontab -e` en el servidor:

```
15 4 * * * cd ~/apps/buscador && /usr/bin/flock -n /tmp/buscador-ingest.lock /usr/bin/docker compose exec -T buscador python -m ingest >> ~/apps/buscador/ingest.log 2>&1
```

El `flock` evita que dos reindexados se solapen: SQLite no lleva bien dos escritores y una
ingesta larga que se cruzara con la de la noche siguiente daría errores de bloqueo.

La ingesta es incremental: en una noche normal solo toca lo que ha cambiado (las fuentes web
en segundos, y el chat según los mensajes nuevos).

## Notas de este servidor

- `docker compose` es **v2**; nunca uses `sudo docker compose` en esta máquina (rompe el bind
  mount de `~/.n8n`).
- El contenedor escucha solo en `127.0.0.1:8100`. Al público se llega por el nginx del host.
- Está también en la red `n8n-docker_default`, así que n8n lo llama como `http://buscador:8100`
  sin pasar por internet ni por el nginx.
- El índice vive en el volumen `buscador-data`, así que sobrevive a los rebuilds. Para
  empezar de cero: `docker compose down && docker volume rm buscador_buscador-data`.

## Exponerlo en HTTPS (aparcado)

**Decisión de agosto de 2026: por ahora el buscador solo se usa desde el bot de Telegram**, así
que no hace falta ni el subdominio ni el certificado. El contenedor escucha solo en
`127.0.0.1:8100` y n8n lo llama por la red de docker; nada del buscador está expuesto a
internet.

Si algún día se quiere el buscador en las webs, está todo preparado en
`nginx-buscador.conf`: haría falta un registro A de `buscador.aldeapucela.org` a la IP del
servidor y luego certbot.

## El bot en n8n

Dos workflows:

- **[Aldea Pucela] Buscador del grupo** (`vNnEVp5BiR7LyasK`) — hace todo el trabajo. Recibe el
  mensaje de Telegram tal cual, detecta el comando, llama a `/ask` o a `/forget` y responde en
  el mismo topic.
- **[Aldea Pucela] Comandos AldeaPucela_bot** (`JMdOQ9eBLbRfD1Xn`) — el de siempre. Se le
  añadieron dos nodos colgando del Telegram Trigger, en paralelo a `/permitirfotos`,
  `/publicar` y `/webpeluditos`: un *If* (`Es del buscador?`) y un *Execute Sub-workflow*
  (`Llamar al buscador`). Ninguna rama existente se tocó.

El buscador no tiene su propio Telegram Trigger **a propósito**: Telegram admite un solo
webhook por bot, así que un segundo trigger sobre AldeaPucela_bot dejaría sordo al bot de
moderación.

Comandos: `/buscar algo`, `/finde`, `/hoy`, `/manana`, `/semana`, `/contratos EMPRESA` y
`/olvidar` (respondiendo a un mensaje propio). Funcionan también con `@AldeaPucela_bot`
pegado al comando, que es como los manda Telegram en los grupos.

`/olvidar` solo lo acepta **el autor del mensaje respondido**: se comprueba comparando
`message.from.id` con `reply_to_message.from.id`. Los admins, de momento, no tienen atajo.

### Lo único que falta

**Crear la credencial `Buscador API Key`** (tipo *Header Auth*): nombre `X-API-Key`, valor el
`API_KEY` de `~/apps/buscador/.env` del servidor. La usan los dos nodos HTTP del subflujo.
Hasta que exista, los comandos del buscador no responden (fallan dentro de n8n, sin publicar
nada en el grupo); el resto del bot sigue funcionando igual.

La credencial de Telegram ya está puesta: *Telegram account 2* (AldeaPucela_bot).

## Poner el buscador en las webs

Cuando exista el DNS y el certificado, en cualquier web del ecosistema:

```html
<script src="https://buscador.aldeapucela.org/buscador.js" defer></script>
```

Se abre con la tecla `/` o con cualquier elemento que lleve `data-buscador`. Para probarlo en
local, `web/demo.html` apunta a `http://localhost:8100`.

Ojo: hoy el fichero `buscador.js` no lo sirve nadie. O se copia a cada web (son estáticas), o
se añade una `location /buscador.js` en el vhost de nginx que lo sirva desde el repo clonado.
