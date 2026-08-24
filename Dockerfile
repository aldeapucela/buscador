FROM python:3.12-slim

# tzdata hace falta de verdad: el enrutador calcula las ventanas del finde en Europe/Madrid
# y zoneinfo sin tzdata revienta con ZoneInfoNotFoundError.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Los modelos se bajan en el build, no en el arranque: así el contenedor levanta listo y no
# depende de que HuggingFace esté disponible cada vez que se reinicia.
ENV HF_HOME=/opt/hf
RUN python -c "\
from sentence_transformers import SentenceTransformer, CrossEncoder; \
SentenceTransformer('intfloat/multilingual-e5-small', device='cpu'); \
CrossEncoder('cross-encoder/mmarco-mMiniLMv2-L12-H384-v1', device='cpu')"

COPY app ./app
COPY ingest ./ingest
COPY eval ./eval
COPY scripts ./scripts

# Usuario sin privilegios, dueño de /data (el volumen con el índice).
RUN useradd --create-home --uid 10001 buscador \
    && mkdir -p /data && chown -R buscador:buscador /data /opt/hf
USER buscador

ENV BUSCADOR_DB=/data/index.db
EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -fsS http://localhost:8100/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
