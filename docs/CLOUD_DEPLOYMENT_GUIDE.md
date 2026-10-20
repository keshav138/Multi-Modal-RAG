# Cloud Deployment Guide — single EC2 + Jenkins

A complete, ordered walkthrough of deploying the Multi Modal RAG service to
AWS with the simplified architecture: **one EC2 instance** (m7i-flex.large,
8GB) runs the whole docker-compose stack (app + pgvector + Prometheus +
Grafana). **Terraform** provisions the instance (state in HCP Terraform).
**Jenkins on your laptop** builds, pushes to Docker Hub, and deploys over SSH.

Every "insert credential here" spot is marked with ⚡.

**The end state:**

```
GitHub ──poll(1min)──▶ Jenkins (your laptop)
                         ├─ lint ─ test ─ docker build ─ push ─▶ Docker Hub
                         └─ (main) SSH ─▶ EC2 box: compose pull && up -d ─▶ curl /health
```

---

## Credentials cheat sheet

| # | Credential | Where you get it | Where it gets inserted | Used by |
|---|---|---|---|---|
| 1 | `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | AWS IAM → create user → Security credentials | **a)** `aws configure` on your laptop | Terraform (creates the EC2 box) |
| 2 | `TF_API_TOKEN` | HCP Terraform → Settings → Teams → create token | **a)** `terraform login` on your laptop | Terraform state + locking in HCP |
| 3 | SSH keypair (private + public) | You generate it: `ssh-keygen` | **a)** public key → Terraform `ssh_public_key` var **b)** private key → Jenkins credential `ec2-ssh-key` | Terraform registers the key on AWS; Jenkins/you log into the box |
| 4 | Docker Hub username + access token | hub.docker.com → Account Settings → Security → Access Tokens | **a)** Jenkins credential `dockerhub` | Jenkins pushes the image; the box pulls it |
| 5 | `GROQ_API_KEY` | console.groq.com → API Keys | **a)** `.env` for local dev **b)** Jenkins credential `groq-api-key` (deploy writes it to the box) | LLM synthesis in `/query` |
| 6 | EC2 elastic IP | `terraform output public_ip` | **a)** Jenkins credential `ec2-host` | SSH deploy target |

⚠️ **Never commit**: `.env`, private keys, AWS keys, tokens. The `.gitignore`
at the repo root already excludes them.

---

## Phase 0 — Accounts & tools (once)

1. **AWS account** (m7i-flex.large free tier already confirmed).
2. **HCP Terraform** — app.terraform.io (free tier).
3. **Docker Hub** — hub.docker.com.
4. **Groq** — console.groq.com.
5. **GitHub** — this code will live in a repo (public or private, your call).
6. **Laptop tools**: AWS CLI, Terraform (≥1.5), Docker Desktop, Git for
   Windows (Git Bash), Python 3.11 (already installed for this project).

---

## Phase 1 — AWS credentials (#1)

1. AWS console → **IAM → Users → Create user** (e.g. `mmrag-deployer`).
2. Attach `AmazonEC2FullAccess` (enough for this stack).
3. Security credentials → **Create access key** → copy both values. ⚡

```bash
aws configure          # paste keys, region ap-southeast-2
aws sts get-caller-identity   # sanity check
```

---

## Phase 2 — HCP Terraform workspace (#2)

1. app.terraform.io → **Create organization** (already done: `keshav-org`).
2. Create workspace **`multimodal-rag`** → CLI-driven workflow →
   **Execution mode: Local** (you run terraform; HCP only stores state).
3. Settings → Teams → create a **team API token**. ⚡

```bash
terraform login       # paste the team token (stores it locally)
```

The `cloud` block in `infra/terraform/main.tf` already points at
`keshav-org` / `multimodal-rag` — no edits needed.

---

## Phase 3 — SSH keypair (#3)

```bash
ssh-keygen -t ed25519 -f ~/.ssh/mmrag-deploy -N ""
# creates: ~/.ssh/mmrag-deploy          (private — Jenkins credential)
#          ~/.ssh/mmrag-deploy.pub      (public — Terraform variable)
```

---

## Phase 4 — Provision the EC2 box with Terraform (#3)

```bash
cd infra/terraform
terraform init

terraform apply \
  -var "ssh_public_key=$(cat ~/.ssh/mmrag-deploy.pub)"
```

Creates: the instance (m7i-flex.large, 30GB disk), security group (everything
open for the demo: 22/8000/3000/9090 — SSH is key-only, no passwords), key
pair, elastic IP, and runs the cloud-init script that installs Docker +
compose (~3-5 min after boot).

> **Demo mode**: the box is reachable by anyone until you tear it down.
> Run `terraform destroy` when you're done, or set `ssh_cidr` to your own IP
> (`terraform apply -var "ssh_cidr=203.0.113.10/32"`) to re-lock SSH.

```bash
terraform output public_ip    # ⚡ save this — it's Jenkins credential #6
```

Sanity check (SSH from your laptop):

```bash
ssh -i ~/.ssh/mmrag-deploy ubuntu@<public_ip> "docker --version"
```

---

## Phase 5 — Docker Hub repo + token (#4)

1. hub.docker.com → **Create repository** → name `multimodal-rag` →
   visibility **Public** (private also works, but then the box needs its own
   `docker login` — see the Jenkinsfile's Deploy stage comment).
2. Account Settings → Security → **New Access Token**. ⚡

---

## Phase 6 — Jenkins on your laptop

1. **Install**: Java 17 (e.g. Temurin) → Jenkins LTS (`jenkins.io/download`)
   → start it → `http://localhost:8080` → unlock + suggested plugins.
2. **Plugins** (Dashboard → Manage Jenkins → Plugins): Git, Pipeline,
   SSH Agent, Credentials Binding — these come with the suggested set.
3. **Global env var**: Manage Jenkins → System → Global properties →
   Environment variables → `DOCKERHUB_USER` = your Docker Hub username.
4. **Credentials** (Manage Jenkins → Credentials → System → Global): ⚡

| Credential ID | Kind | Value |
|---|---|---|
| `dockerhub` | Username with password | Docker Hub username + access token (#4) |
| `ec2-ssh-key` | SSH private key | contents of `~/.ssh/mmrag-deploy` (#3) — username `ubuntu` |
| `ec2-host` | Secret text | the elastic IP (#6) |
| `groq-api-key` | Secret text | Groq key (#5) |

5. **Create the job**: New Item → **Pipeline** → Definition: *Pipeline
   script from SCM* → Git → repo URL → Script Path: `Jenkinsfile` → Save.
6. **Trigger**: SCM polling is already in the Jenkinsfile
   (`pollSCM('* * * * *')`) — Jenkins checks GitHub every minute. No webhook
   needed (a laptop behind NAT can't receive them anyway).

Laptop prerequisites so the stages run: Docker Desktop running, Git Bash on
PATH, `ruff`/`pytest` installed (`pip install -r requirements.txt`).

---

## Phase 7 — First deploy

1. **Build Now** in Jenkins — or just push a commit to `main`.
2. Watch the stages: Checkout → Lint → Test → Build → Push → Deploy → Verify.
   The Deploy stage ssh's to the box, drops the compose file + `.env`, runs
   `docker compose pull && up -d` (first pull downloads the ~2GB torch image;
   model weights download inside the container on first start — give it a few
   minutes), then curls `/health` and `/readiness`.
3. Open the stack from anywhere:

```
http://<public_ip>:8000/docs      # API (upload PDFs, run /query)
http://<public_ip>:3000           # Grafana (admin / admin)
http://<public_ip>:9090           # Prometheus
```

4. Smoke test through the public API: `POST /ingest/pdf` with a manual →
   poll `GET /ingest/status/{job_id}` → `POST /query`.

From now on: **`git push` to `main` = deploy** (within ~1 minute of the poll).
Terraform is done — you only touch it again to change instance size, ports,
or the deploy directory.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `terraform apply` fails to reach HCP | `terraform login` not run, or token expired (#2) |
| `terraform apply` SSH key error | `ssh_public_key` mangled — pass via `-var` exactly as in Phase 4 (#3) |
| Jenkins: `docker` not found in Build stage | Docker Desktop not running, or Jenkins can't see `docker.exe` (restart Jenkins after installing Docker) |
| Push stage: `denied: requested access to the resource is denied` | `dockerhub` token expired or repo name mismatch (#4) |
| Deploy: `Permission denied (publickey)` | `ec2-ssh-key` holds the wrong private key, or `ec2-host` IP wrong (#6) |
| Deploy: box `compose pull` denied | Docker Hub repo is private — make it public or add a login step on the box |
| Verify: connection refused on /health | app still starting (models download 2-5 min first run); watch with `ssh ubuntu@<ip> "cd /opt/mmrag && docker compose logs -f app"` |
| Box runs out of disk | root volume is 30GB gp3; `docker system prune -a` frees old images |
| Laptop sleeps → no builds | Jenkins lives on your laptop; polling pauses when it sleeps — keep it awake, or accept delayed builds |

---

## Local run (before any of this)

```bash
cp .env.example .env        # fill DATABASE_URL (local Postgres) + GROQ_API_KEY
uvicorn app.main:app --reload
# open http://localhost:8000/docs — upload → ingest → query
```
