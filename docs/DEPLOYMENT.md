# Deployment — how it works

This is a plain-language walkthrough of the deployment setup in `infra/`,
`.github/workflows/deploy.yml`, and the FastAPI app itself. You don't need
any of it to run the service locally — `uvicorn app.main:app` is enough for
development.

> **Looking for the step-by-step?** [CLOUD_DEPLOYMENT_GUIDE.md](CLOUD_DEPLOYMENT_GUIDE.md)
> is the from-zero walkthrough: accounts, every credential and where to
> insert it, first infra build, first deploy, and the CI loop.

## The big picture

```
GitHub ──push──▶ GitHub Actions ──build──▶ ECR (container registry)
                       │                      │
                       └───── terraform ─────▶ EKS (Kubernetes) ──pulls──▶ RDS (Postgres/pgvector)
                                              │
                                              └─▶ Prometheus/Grafana watch the app's /metrics
```

Three layers, each doing one job:

1. **Terraform** (`infra/terraform/`) creates the cloud *infrastructure*:
   - **ECR** — a private Docker registry (where images are stored).
   - **RDS** — a managed PostgreSQL 16 server. The app's ingestion code runs
     `CREATE EXTENSION vector` itself, so pgvector works out of the box.
   - **EKS** — a managed Kubernetes cluster with a small node group.
   Terraform only creates these things; it doesn't know about the app.
2. **Kubernetes** (`infra/k8s/`) describes *how the app runs*:
   - `deployment.yaml` — one pod running the container, with CPU/memory
     limits, liveness (`/health`) and readiness (`/readiness`) probes.
   - `service.yaml` — a stable in-cluster address for the pod.
   - `configmap.yaml` / `secret.yaml` — settings (model names, paths) and
     secrets (database URL, Groq key) injected as environment variables.
   - `hpa.yaml` — scales pods up when CPU exceeds 70%.
3. **GitHub Actions** (`.github/workflows/deploy.yml`) is the *delivery
   pipeline*. There is no CI server to run or patch — on every push GitHub
   spins up a fresh runner VM and executes: lint → test → build the Docker
   image → push it to ECR → (on `main`) `terraform apply` + `kubectl set
   image` → hit `/health` to verify.

## How a code change reaches production

1. You push to GitHub. No webhook configuration — Actions triggers
   automatically.
2. The `lint-test` job installs the Python requirements, runs `ruff` and
   `pytest`. Broken code never builds.
3. The `build-push` job builds the image with `infra/docker/Dockerfile`
   (torch, the app, the pipelines, no test code), logs Docker into ECR with
   a fresh token, and pushes with the commit SHA as the tag. GitHub's
   build cache makes the ~2GB torch layers incremental.
4. On the `main` branch only, the `deploy` job runs `terraform apply`
   (a no-op if nothing in the infra changed) and then points the Kubernetes
   deployment at the new image tag. Kubernetes replaces the pod gradually
   (rolling update), so there's no downtime.
5. The job polls `kubectl rollout status`, then curls `/health` from a
   throwaway pod inside the cluster. If that fails, the workflow run is red.

## What you set up once, by hand

- **Repo secrets** (Settings → Secrets and variables → Actions):
  `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` (or an OIDC role instead —
  see the workflow header), `DB_PASSWORD` for Terraform, and `TF_API_TOKEN`
  for HCP Terraform.
- **HCP Terraform for state.** The `cloud` block in
  `infra/terraform/main.tf` points at your HCP organization + workspace
  (execution mode: Local). State storage and locking are handled by HCP —
  no S3 bucket or DynamoDB table. Create the workspace, fill in the org
  name, and `terraform init` once locally to migrate any local state.
- **`infra/k8s/secret.yaml`** — applied once by hand with the real
  `DATABASE_URL` and `GROQ_API_KEY`. The workflow deliberately skips this
  file so placeholder values can never overwrite real secrets.

## Why the pieces are shaped this way

- **One worker per pod.** The ML models (BGE, SigLIP, cross-encoder) live in
  process memory and take 30-60s to load. Multiple uvicorn workers in one
  pod would each load their own copy, so the image defaults to 1 worker and
  the HPA adds pods instead of threads.
- **Readiness waits for the models.** `/readiness` only returns 200 after the
  DB pool answers and the retrieval service exists, so Kubernetes doesn't
  route traffic to a pod that's still downloading model weights.
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

## Running the stack locally (no cloud)

`infra/docker/docker-compose.yml` starts the same shape on your machine:
the app + a pgvector container + Prometheus + Grafana. It's the fastest way
to see the monitoring dashboard and to smoke-test the Docker image before
pushing.
