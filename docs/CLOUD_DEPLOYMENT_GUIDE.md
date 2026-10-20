# Cloud Deployment Guide — from zero to running

A complete, ordered walkthrough of deploying the Multi Modal RAG service to
AWS, from nothing to a working CI/CD loop. Follow the phases in order; every
"insert credential here" spot is marked with ⚡.

**The end state:**

```
git push ──▶ GitHub Actions ──▶ lint/test ──▶ Docker build ──▶ ECR
                                          ──(on main)──▶ terraform apply (HCP state)
                                                      ─▶ kubectl set image ──▶ EKS pods ──▶ RDS/pgvector
```

---

## Credentials cheat sheet (read this first, you'll come back to it)

| # | Credential | Where you get it | Where it gets inserted | Used by |
|---|---|---|---|---|
| 1 | `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | AWS IAM → create user → Security credentials | **a)** `aws configure` on your laptop **b)** GitHub repo secrets | `configure-aws-credentials` in the workflow; everything AWS |
| 2 | `TF_API_TOKEN` | HCP Terraform → Settings → Teams → create token | **a)** `terraform login` on your laptop **b)** GitHub repo secret | `terraform init/apply` (state + locking in HCP) |
| 3 | `DB_PASSWORD` | You invent it (RDS master password) | **a)** `TF_VAR_db_pass` when running terraform by hand **b)** GitHub repo secret **c)** inside `DATABASE_URL` for k8s secret | Terraform creates RDS with it; the app connects with it |
| 4 | `GROQ_API_KEY` | console.groq.com → API Keys | **a)** `.env` for local dev **b)** `infra/k8s/secret.yaml` (base64) | LLM synthesis in `/query` |
| 5 | `DATABASE_URL` | Constructed from RDS output: `postgresql://rag:<DB_PASSWORD>@<db_endpoint>:5432/rag` | **a)** `.env` for local dev **b)** `infra/k8s/secret.yaml` (base64) | The app's database pool |
| 6 | Org + workspace names | HCP Terraform (you create them) | `infra/terraform/main.tf` `cloud` block | Terraform state routing |

⚠️ **Never commit**: `.env`, real `secret.yaml` values, AWS keys, tokens. The
`.gitignore` at the repo root already excludes them.

---

## Phase 0 — Accounts & tools (once, ~1 hour)

1. **AWS account** — sign up at aws.amazon.com. Root account only for billing; everything else uses an IAM user (next phase).
2. **GitHub** — have an account, create a private repo for this code later (Phase 3).
3. **HCP Terraform** — sign up at app.terraform.io (free tier is enough).
4. **Groq** — sign up at console.groq.com (you need this for `/query`).
5. **Install on your laptop**: AWS CLI, Terraform (≥1.5), Docker, kubectl.

---

## Phase 1 — AWS: create the credentials (#1)

1. AWS console → **IAM → Users → Create user** (name it `mmrag-deployer`).
2. Attach policies: `AdministratorAccess` (quick-and-dirty; a tighter policy can come later).
3. Security credentials → **Create access key** → copy both values. ⚡
4. On your laptop:

```bash
aws configure          # paste AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY, region us-east-1
aws sts get-caller-identity   # sanity check: prints your account
```

*(Optional upgrade, not required now: GitHub OIDC instead of static keys —
create an OIDC provider + role in IAM, store the role ARN as a GitHub
secret, and swap the `configure-aws-credentials` inputs as shown in the
workflow header comment.)*

---

## Phase 2 — HCP Terraform: state home (#2, #6)

HCP stores your `.tfstate` and does locking — no S3, no DynamoDB.

1. app.terraform.io → **Create organization** (any name, e.g. `yourname-org`). ⚡
2. Inside it → **New workspace** → choose **CLI-driven workflow** → name it
   `multimodal-rag` → **Execution mode: Local** (this is important — the
   GitHub runner executes terraform, HCP only stores state). ⚡
3. HCP → your org → **Settings → Teams** → create a **team API token**. ⚡
4. On your laptop:

```bash
terraform login       # paste the team token; stores it for local use
```

---

## Phase 3 — GitHub: push the code + insert secrets (#1, #2, #3)

1. Create the private repo, push this code:

```bash
git init && git add . && git commit -m "initial" && git branch -M main
git remote add origin git@github.com:<you>/<repo>.git && git push -u origin main
```

2. Repo → **Settings → Secrets and variables → Actions → New repository secret**. Insert: ⚡

| Name | Value |
|---|---|
| `AWS_ACCESS_KEY_ID` | from Phase 1 |
| `AWS_SECRET_ACCESS_KEY` | from Phase 1 |
| `TF_API_TOKEN` | from Phase 2 |
| `DB_PASSWORD` | invent one, e.g. 20 random chars |

3. Edit `infra/terraform/main.tf` → `cloud` block → replace
   `CHANGE_ME_ORGANIZATION` with your HCP org name (workspace is already
   `multimodal-rag`). Commit.

---

## Phase 4 — First infrastructure build (from your laptop)

You run this once by hand, so you see what happens and get the outputs.

```bash
cd infra/terraform
export TF_VAR_db_pass="<your DB_PASSWORD>"        # ⚡ from Phase 3
terraform init        # connects to HCP; if you had local state it migrates it
terraform apply       # review the plan, type yes (~15 min)
```

Creates: **ECR repo** (empty), **RDS Postgres 16** (empty, private), **EKS
cluster** with 2 workers.

Grab the outputs:

```bash
terraform output       # note db_endpoint and ecr_repository_url
```

---

## Phase 5 — First deploy of the app (by hand, proves the app stack)

1. Fill in `infra/k8s/secret.yaml` with real values. ⚡
   Both fields are **base64**-encoded — encode like this:

```bash
echo -n "postgresql://rag:<DB_PASSWORD>@<db_endpoint>:5432/rag" | base64   # -> DATABASE_URL
echo -n "gsk_..."                                                          | base64   # -> GROQ_API_KEY
```

2. Point kubectl at the cluster:

```bash
aws eks update-kubeconfig --region us-east-1 --name multimodal-rag
kubectl get nodes        # 2 nodes
```

3. Build + push the image manually (proves the Dockerfile):

```bash
ECR=$(terraform output -raw ecr_repository_url)
docker build -f infra/docker/Dockerfile -t $ECR:manual-1 .
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin $ECR
docker push $ECR:manual-1
```

4. Edit `infra/k8s/deployment.yaml` → `image:` → `$ECR:manual-1`. Deploy:

```bash
kubectl apply -f infra/k8s/        # applies all manifests, secret included
kubectl get pods -w                # wait for Running (models load on start)
```

5. Smoke test:

```bash
kubectl port-forward svc/multimodal-rag 8000:8000
curl localhost:8000/health
# then POST a PDF to /ingest/pdf and a question to /query via the /docs UI
```

If this works, the app stack is proven; the pipeline is the only thing left.

---

## Phase 6 — The CI/CD loop (from now on, `git push` deploys)

Everything is already wired: `.github/workflows/deploy.yml` + your repo
secrets. No webhooks, no servers.

1. Push a small commit to `main` → GitHub → **Actions** tab → watch the run.
2. The three jobs, in order:
   - **lint-test** — ruff + pytest
   - **build-push** — Docker build (cached torch layers) → ECR, tag = commit SHA
   - **deploy** (main only) — `terraform apply` (state in HCP) → `kubectl apply`
     (secret.yaml deliberately skipped) → `kubectl set image` → rollout wait →
     curl `/health` from an in-cluster pod
3. Red run? Click the failing job — the step logs tell you exactly where.

Remember: `infra/k8s/secret.yaml` is applied **only by hand** (Phase 5). The
workflow never touches it, so placeholders can't overwrite real credentials.

---

## Phase 7 — Monitoring (optional but cheap)

- Local: `docker compose -f infra/docker/docker-compose.yml up` gives you
  Prometheus + Grafana against the app on your machine.
- On EKS: install Prometheus/Grafana with Helm (`kube-prometheus-stack`), or
  `kubectl port-forward` any pod that can reach `/metrics` and scrape it.
  Import `infra/monitoring/grafana/dashboards/rag-dashboard.json`.

---

## Phase 8 — Production hardening (only when real users exist)

- Ingress + TLS in front of the ClusterIP service (nothing external can
  reach the app right now — intentional).
- RDS: enable automatic snapshots/backups in the module.
- ECR lifecycle policy to expire old tags.
- Tighten the IAM policy from `AdministratorAccess` to the minimum needed
  (ECR push, EKS access, RDS management).
- Switch GitHub → AWS auth from static keys to OIDC (no long-lived keys).

---

## Troubleshooting quick hits

| Symptom | Likely cause |
|---|---|
| `terraform init` fails with "organization not found" | `cloud` block org name doesn't match HCP (Phase 3.3) |
| `terraform apply` 401/403 | `TF_API_TOKEN` missing/expired in repo secrets (Phase 3.2) |
| Build job: ECR push denied | IAM user lacks ECR permissions (Phase 1.2) |
| Deploy job: `kubectl` "no configuration" | `update-kubeconfig` ran in wrong region, or cluster name mismatch (Phase 5.2) |
| Pod CrashLoopBackOff | `kubectl logs <pod>` — usually missing `DATABASE_URL`/`GROQ_API_KEY` (secret.yaml not applied, Phase 5.1) |
| Pod never Ready | Models still downloading; `readiness` waits ~90s+ (deployment.yaml probe delay) |
| `/query` returns 500 | GROQ_API_KEY invalid or no credits |
