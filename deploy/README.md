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
15 4 * * * cd ~/apps/buscador && docker compose exec -T buscador python -m ingest >> ~/apps/buscador/ingest.log 2>&1
```

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
