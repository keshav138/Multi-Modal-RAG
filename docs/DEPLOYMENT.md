# Deployment — how it works

This is a plain-language walkthrough of the deployment setup in `infra/`,
`Jenkinsfile`, and the FastAPI app itself. You don't need any of it to run
the service locally — `uvicorn app.main:app` is enough for development.

> **Looking for the step-by-step?** [CLOUD_DEPLOYMENT_GUIDE.md](CLOUD_DEPLOYMENT_GUIDE.md)
> is the from-zero walkthrough: accounts, every credential and where to
> insert it, provisioning the box, installing Jenkins, and the CI loop.

## The big picture

```
GitHub ──poll(1min)──▶ Jenkins (your laptop)
                         ├─ lint ─ test ─ docker build ─ push ─▶ Docker Hub
                         └─ (main) SSH ─▶ EC2 box: compose pull && up -d
                                                │
                              one m7i-flex.large (8GB) runs:
                              app + pgvector + Prometheus + Grafana
```

Three layers, each doing one job:

1. **Terraform** (`infra/terraform/`) provisions the *machine* — one EC2
   instance (m7i-flex.large, 30GB disk), its security group (open for the
   demo: 22/8000/3000/9090, SSH key-only — re-lock via `ssh_cidr` after),
   the SSH key pair, an elastic IP, and a cloud-init script that installs
   Docker + compose. State and locking live in HCP Terraform. Terraform
   never deploys code — it's a day-0 tool you run from your laptop, and
   only again when the box itself needs changing.
2. **Docker compose** (`infra/docker/docker-compose.yml`) describes *what
   runs on the box*: the app, a pgvector container, Prometheus and Grafana.
   The same file works on your laptop for local development.
3. **Jenkins** (`Jenkinsfile`, running on your laptop) is the *delivery
   pipeline*. It polls GitHub every minute (webhooks can't reach a laptop
   behind NAT), runs lint → test → `docker build` → push to Docker Hub, and
   on `main` SSHes into the box, drops the compose file + `.env`, runs
   `docker compose pull && up -d`, and curls `/health`.

## How a code change reaches the box

1. You push to GitHub. Within a minute, Jenkins' poll sees the new commit.
2. Jenkins runs `ruff` and `pytest` — broken code never builds.
3. Jenkins builds the image with `infra/docker/Dockerfile` (torch, the app,
   the pipelines, no test code) and pushes it to Docker Hub under the
   commit SHA and `latest` tags.
4. On `main` only, Jenkins SSHs into the box, writes `/opt/mmrag/.env`
   (database URL, Groq key, Docker Hub user, image tag), scp's the compose
   file, and runs `docker compose pull && up -d`. Compose replaces the app
   container with the new image while the database container keeps running.
5. Jenkins curls `/health` and `/readiness` on the box. If either fails,
   the build is red.

## What you set up once, by hand

- **AWS IAM user keys** for Terraform (EC2 permissions) — `aws configure`.
- **HCP Terraform workspace** (`keshav-org` / `multimodal-rag`, execution
  mode Local) + `terraform login`. State storage and locking are handled by
  HCP — no S3 bucket or DynamoDB table.
- **An SSH keypair** — public half goes to Terraform, private half to
  Jenkins.
- **Docker Hub repo** `multimodal-rag` (public) + access token.
- **Jenkins credentials**: `dockerhub`, `ec2-ssh-key`, `ec2-host`,
  `groq-api-key`, plus the `DOCKERHUB_USER` global env var. The deploy stage
  writes the box's `.env` from these — no secrets are ever committed.

## Why the pieces are shaped this way

- **One box, no orchestrator.** A single m7i-flex.large (8GB) comfortably
  runs the app (ML models are ~4GB in memory) plus Postgres and the
  monitoring stack. EKS/RDS/ECS were dropped — the EKS control plane alone
  costs ~$73/month, and one service doesn't need orchestration.
- **One worker per container.** The ML models (BGE, SigLIP, cross-encoder)
  live in process memory and take 30-60s to load. Multiple uvicorn workers
  in one container would each load their own copy, so the image defaults to
  1 worker.
- **`/readiness` waits for the models.** It only returns 200 after the DB
  pool answers and the retrieval service exists — that's what Jenkins'
  Verify stage checks after deploy.
- **BackgroundTasks, not a queue.** Ingestion uploads are processed in the
  same process after the HTTP response. That's fine for this volume; if
  ingestion ever needs retries or many concurrent jobs, swap the in-memory
  job tracker for Redis + Celery — the endpoint contract (job_id + status
  polling) stays the same.
- **Prometheus + Grafana.** The app exposes `/metrics` (generic HTTP metrics
  plus `rag_retrieval_seconds`, `rag_rerank_seconds`, `rag_llm_seconds`,
  `rag_ingestion_chunks_total`). Prometheus scrapes it every 15s; Grafana
  renders the pre-provisioned RAG dashboard (latency p50/p95, request rate,
  ingestion throughput).
- **Terraform = machine, Jenkins = app.** Terraform runs rarely (create the
  box, change ports/size); Jenkins runs every commit (build, push, deploy).
  The seam between them is one number: the elastic IP.

## Running the stack locally (no cloud)

`infra/docker/docker-compose.yml` starts the same shape on your machine:
the app + a pgvector container + Prometheus + Grafana. It's the fastest way
to see the monitoring dashboard and to smoke-test the Docker image before
pushing.
