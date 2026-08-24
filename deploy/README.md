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

## Exponerlo en HTTPS

Ver `nginx-buscador.conf`. **Hace falta primero un registro A** para
`buscador.aldeapucela.org` apuntando a la IP del servidor: sin DNS, certbot no puede emitir el
certificado. Mientras no exista, el bot funciona igual (habla con el contenedor por la red de
docker); lo que espera es el buscador de las webs.

## Enchufar el bot (paso pendiente, en producción)

El subflujo **[Aldea Pucela] Buscador del grupo** ya está creado en n8n, **sin publicar**.
Faltan tres cosas, todas en la interfaz de n8n:

1. **Crear la credencial `Buscador API Key`** (tipo *Header Auth*): nombre `X-API-Key`, valor
   el `API_KEY` de `~/apps/buscador/.env` del servidor. La usan los dos nodos HTTP.
2. **Revisar la credencial de Telegram** de los nodos "Responder en el grupo" y "Confirmar el
   olvido": n8n asignó una automáticamente y hay dos en la instancia. Tiene que ser la del
   bot del grupo, la misma que usa *[Aldea Pucela] Comandos AldeaPucela_bot*.
3. **Añadir la rama en el workflow de comandos** (`JMdOQ9eBLbRfD1Xn`), junto a las de
   `/permitirfotos` y `/publicar`: un nodo *If* que compruebe que el texto empieza por
   `/buscar` (o `/olvidar`), seguido de un nodo *Execute Sub-workflow* apuntando a este
   subflujo, con estas entradas:

   | Entrada | Valor |
   |---|---|
   | `comando` | `buscar` o `olvidar` |
   | `consulta` | el texto del mensaje sin el comando |
   | `chat_id` | `{{ $json.message.chat.id }}` |
   | `thread_id` | `{{ $json.message.message_thread_id }}` |
   | `mensaje_a_olvidar` | `{{ $json.message.reply_to_message.message_id }}` (solo para `/olvidar`) |

Se hace así, y no con un segundo Telegram Trigger, porque **Telegram solo admite un webhook
por bot**: dos triggers sobre el mismo bot se pisan el uno al otro.

Para `/olvidar` conviene además que el bot compruebe que quien lo pide es el autor del mensaje
respondido (o un admin); si no, cualquiera podría borrar mensajes ajenos del índice.

## Poner el buscador en las webs

Cuando exista el DNS y el certificado, en cualquier web del ecosistema:

```html
<script src="https://buscador.aldeapucela.org/buscador.js" defer></script>
```

Se abre con la tecla `/` o con cualquier elemento que lleve `data-buscador`. Para probarlo en
local, `web/demo.html` apunta a `http://localhost:8100`.

Ojo: hoy el fichero `buscador.js` no lo sirve nadie. O se copia a cada web (son estáticas), o
se añade una `location /buscador.js` en el vhost de nginx que lo sirva desde el repo clonado.
