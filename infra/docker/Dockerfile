# Multi-stage build for the Multi Modal RAG API.
#
# Stage 1 (builder) installs every Python dependency — the torch CPU wheels
# are ~200 MB, so we install them first from the CPU index and let the
# requirements file's plain `torch` pin be satisfied by it.  GPU deployments
# would swap this index for the CUDA one.
#
# Stage 2 (runtime) copies only the installed site-packages and the app
# code, so the final image carries no build tooling and no pip caches.
#
# Build from the repo root (the context must contain app/, scripts/ and the
# standalone ingest scripts, which the service imports directly):
#
#     docker build -f infra/docker/Dockerfile -t multimodal-rag:latest .

FROM python:3.11-slim AS builder

# System libraries required at runtime by torch, PIL and faster-whisper.
# Installing them in the builder lets the runtime stage copy them via the
# same apt step — slim images have no compiler toolchain by default.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgomp1 \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# torch CPU wheel first, then the rest of the requirements.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


FROM python:3.11-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgomp1 \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for the API process.
RUN useradd --create-home --uid 1000 app
WORKDIR /app

# Copy the full installed Python environment from the builder.
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Application code.  The standalone pipeline scripts are copied too because
# app.services.ingestion imports them directly from the working directory.
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY pipelines/ ./pipelines/

# Runtime directories: image files are served by StaticFiles (the directory
# must exist), uploads land here before background ingestion, and ML model
# weights are cached here on first startup (HF_HOME).
ENV IMAGE_DIR=/app/data/out_images \
    UPLOAD_DIR=/app/data/uploads \
    HF_HOME=/app/.cache/huggingface \
    TORCH_HOME=/app/.cache/torch \
    PYTHONUNBUFFERED=1
RUN mkdir -p /app/data/out_images /app/data/uploads /app/.cache \
    && chown -R app:app /app
USER app

EXPOSE 8000

# WORKERS defaults to 1 because the ML models live in process memory and
# multiple workers would each load their own copy.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${WORKERS:-1}"]
