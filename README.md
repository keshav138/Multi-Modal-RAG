# Multi Modal RAG

Retrieval-Augmented Generation over **text, images, and audio** for HVAC
technical-support manuals. Queries fan out to three pgvector indexes in
parallel, candidates are fused and reranked with a cross-encoder, and a
Groq-served LLM synthesizes a cited answer.

## Folder structure

```
├── app/                  # FastAPI service (routers, schemas, services, config)
├── pipelines/            # CLI pipeline scripts — also imported by the service
│   ├── ingest_pgvector.py    # text/audio chunks → BGE embeddings → pgvector
│   ├── ingest_images.py      # figure records → SigLIP embeddings → pgvector
│   ├── transcribe_audio.py   # audio → Whisper transcripts
│   └── retrieve.py           # CLI fusion search (3-way search + rerank)
├── scripts/              # extraction & dataset-creation scripts
│   ├── text-extraction/      # PDF → text chunks
│   ├── image-extraction/     # PDF → cropped figures + captions
│   └── audio-creation/       # synthetic calls (Piper TTS)
├── tests/                # pytest suite (no real ML models or DB needed)
├── infra/                # docker/, k8s/, terraform/, monitoring/
├── docs/                 # Plan.md, fastapi-plan.md, DEPLOYMENT.md
├── data/                 # all runtime data (gitignored-style, local only)
│   ├── manuals/              # source PDFs
│   ├── audio_data/           # synthetic call recordings
│   ├── out_audio/            # TTS output
│   ├── out_images/           # extracted figures (served via /static/images)
│   └── uploads/              # multipart upload staging
├── .github/workflows/    # CI/CD pipeline (lint → test → build → deploy)
└── README.md
```

## Quickstart (local)

```bash
pip install -r requirements.txt
cp .env.example .env        # fill in DATABASE_URL and GROQ_API_KEY
uvicorn app.main:app --reload
```

The API accepts **multipart uploads of raw files** (no auth — internal-service
design). All extraction happens server-side:

| Endpoint | Upload | What happens |
|---|---|---|
| `POST /ingest/pdf` | one or more `.pdf` | text chunked (scripts/text-extraction) → BGE → `text_chunks`; figures cropped (scripts/image-extraction) → SigLIP → `image_chunks` |
| `POST /ingest/audio` | one or more audio files | Whisper → transcript chunks → `text_chunks` (modality='audio') |
| `POST /search` | JSON `{question, k, top_n, modalities}` | retrieval only |
| `POST /query` | JSON `{question, k, top_n, model}` | retrieval + Groq synthesis + citations |
| `GET /health`, `/readiness` | — | probes |
| `GET /metrics` | — | Prometheus |

Ingestion runs as a FastAPI **BackgroundTask** and returns `202` with a
`job_id`; poll `GET /ingest/status/{job_id}`.

## Tests & lint

```bash
pytest tests/ -v
ruff check app tests pipelines scripts
```

## Deployment

Step-by-step from zero (accounts, credentials, first deploy, CI loop):
[docs/CLOUD_DEPLOYMENT_GUIDE.md](docs/CLOUD_DEPLOYMENT_GUIDE.md).
Plain-language overview of how the pieces fit:
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
Quickstart with Docker:

```bash
export GROQ_API_KEY=...
docker compose -f infra/docker/docker-compose.yml up --build
```

This starts the API on :8000, pgvector on :5432, Prometheus on :9090 and
Grafana (admin/admin) on :3000 with the RAG dashboard pre-provisioned.
