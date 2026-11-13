# base.Dockerfile: builds base_image. The only file allowed to start FROM a public image.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FASTEMBED_CACHE_PATH=/models
COPY requirements-base.txt /tmp/requirements-base.txt
RUN pip install -r /tmp/requirements-base.txt && pip check
# Bake the embedding model in: no download at container start, no network dependency at runtime.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"
ENV HF_HUB_OFFLINE=1
