# Kubernetes Deployment Template

Deploy any app to Kubernetes **without learning Kubernetes** — answer a few
questions, push to GitLab, done.

```
  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
  │ 1. Add your  │     │ 2. make      │     │ 3. git push  │     │ GitLab CI    │
  │ code +       │ ──▶ │    configure │ ──▶ │              │ ──▶ │ builds image │
  │ Dockerfile   │     │ (asks you    │     │              │     │ + deploys to │
  │ in app/      │     │  questions)  │     │              │     │ Kubernetes   │
  └──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
```

New to Kubernetes? Read [docs/K8S-EXPLAINED.md](docs/K8S-EXPLAINED.md) — it
explains everything you need in plain words.

---

## Quick start

```bash
# 1. Clone this template into a new repo for your project
git clone <this-template> && cd <project>

# 2. Put your application in app/ (your code + Dockerfile)
#    There's a working example in app/ to try it right away.

# 3. Configure the deployment — no Kubernetes knowledge needed
make configure

# 4. Commit and push — GitLab builds and deploys automatically
git add -A && git commit -m "Deploy my app" && git push
```

That's it. `make configure` asks you up to 9 plain-English questions
(app name, port, domain, memory, ...) and:

- writes your answers to `.env`
- turns the right Kubernetes files on/off for your choices
  (e.g. no domain → no ingress)
- validates everything before you commit

**Before the first push, set `APP_NAME` as a GitLab CI/CD project variable**
(see [CI/CD](#cicd-gitlab) below). That's the only required one — without a
domain, the ingress is skipped automatically.

---

## What you get

| Command | What it does |
|---|---|
| `make configure` | **Ask questions → write `.env` → pick the right k8s files** |
| `make preview` | Show the exact Kubernetes manifests that will be deployed |
| `make validate` | Check your configuration (this also runs in CI) |
| `make deploy` | Deploy to your cluster with your local kubectl |
| `make status` | Show what's running (`kubectl get all`) |
| `make logs` | Tail logs of your app |
| `make undeploy` | Remove everything (deletes the namespace) |
| `make pod` | Quick one-off test pod, no service/ingress |
| `make compose` | Convert `docker-compose.yml` → k8s manifests |
| `make envfile` | Regenerate `.env.example` |
| `make help` | Show all commands |

---

## The workflow, in detail

### 1. Your app lives in `app/`

```
app/
├── Dockerfile   # required — CI builds from this
└── ...          # your code
```

Your Dockerfile must expose the port your app listens on (`EXPOSE 3000`),
and the app must respond on some path with a success status — that's the
health check. The wizard detects the port from your Dockerfile automatically.

### 2. `make configure` asks the right questions

It detects what it can (port from your Dockerfile), offers sensible
defaults for everything else, and validates your answers:

- What is your app called? *(becomes all the Kubernetes resource names)*
- Which port does it listen on? *(detected from your Dockerfile)*
- How many copies should run?
- Do you want a public domain? *(e.g. `api.example.com`)*
- How should it be reachable? *(internal / node port / load balancer)*
- Max memory / CPU per copy?
- Health check path? *(usually `/health`)*
- Extra environment variables? *(goes into the ConfigMap — keep non-secrets here)*
- Secrets? *(you only name them — values are never stored in the repo)*

Re-run `make configure` any time to change things — it updates `.env` and
re-picks the k8s files. You never edit YAML unless you want to.

### Secrets

The wizard asks for your secret variable **names** (e.g. `DB_PASSWORD`,
`API_KEY`) — never their values. It writes them as placeholders into
`k8s/secret.yaml`, which is safe to commit: it contains only names.

The actual values come from your **GitLab CI/CD variables** at deploy time
(and from `.env` / your shell when deploying locally). Nothing secret is ever
in the repo, and the app receives them via a Kubernetes Secret.

```bash
# 1. During make configure, list the names:   DB_PASSWORD,API_KEY
# 2. In GitLab → Settings → CI/CD → Variables, add each one:
#      DB_PASSWORD = <value>   (Masked ✓  Protected ✓)
#      API_KEY     = <value>   (Masked ✓  Protected ✓)
# 3. Push — the deployment picks them up automatically.
```

> Use **ConfigMap / `EXTRA_ENV`** for non-secrets (log levels, URLs) and
> **secrets** for anything sensitive. ConfigMaps are plaintext and readable
> by anyone with repo or cluster access.

### 3. Push → GitLab does the rest

The pipeline (`.gitlab-ci.yml`) is fully self-contained — no scripts to keep
in sync:

1. **validate** — checks required CI/CD variables, the Dockerfile and that
   every `${VAR}` used in your k8s manifests is set; fails fast with clear
   messages
2. **build** — builds the image and publishes it to your **Nexus** Docker
   registry (commit SHA + `latest` tags); works with plain-HTTP registries
   out of the box
3. **deploy** — creates the k8s Secret from your masked `SECRETS` variables,
   renders the k8s manifests with your settings and applies them with
   `kubectl`. It refuses to deploy if any variable is missing, instead of
   shipping a broken app.

---

## CI/CD (GitLab)

Set these in **Settings → CI/CD → Variables** of your project:

| Variable | Required | Notes |
|---|---|---|
| `APP_NAME` | **Yes** | Your app name (lowercase letters, numbers, dashes) |
| `NEXUS_URL` | **Yes** | Nexus Docker registry host:port, no scheme — e.g. `nexus.example.com:8082` |
| `NEXUS_USERNAME` | **Yes** | Nexus user (script/user with access to the repo) |
| `NEXUS_API_KEY` | **Yes** | Nexus API key / password — **Masked ✓** |
| `NEXUS_DOCKER_REPO` | No | Repository on Nexus; default `docker-releases`. Can include a path prefix: `sde4-releases/backend` |
| `INGRESS_HOST` | Only for a public domain | e.g. `api.example.com`; leave unset otherwise |
| `NODE_PORT` | Only for `NodePort` | Fixed node port (30000-32767); empty = auto-assigned |
| `IMAGE_PULL_SECRETS` | Only for private registries | Comma-separated k8s pull-secret names, e.g. `regcred` — created automatically from the Nexus credentials |
| `KUBE_CONFIG_CONTENT` | For external clusters | Base64 of your kubeconfig (see below) |
| `KUBE_CONTEXT` | Optional | Context name if your kubeconfig has several |
| `DB_PASSWORD`, `API_KEY`, ... | If listed as secrets | Each secret name you chose in the wizard — **Masked ✓ Protected ✓** |

Anything else (`REPLICAS`, `APP_PORT`, `MEMORY_LIMIT`, ...) can also be set
as project variables; defaults come from the wizard.

The deployed image is pinned to the commit tag:
`$NEXUS_URL/repository/$NEXUS_DOCKER_REPO/$APP_NAME:$CI_COMMIT_SHORT_SHA`.

**Private registry pull access.** The cluster must be able to pull from
Nexus. When `IMAGE_PULL_SECRETS` is set, the deploy stage creates the
docker-registry secret(s) in your namespace automatically (from
`NEXUS_URL`/`NEXUS_USERNAME`/`NEXUS_API_KEY`) and attaches them to the
deployment. To create one manually instead:

```bash
kubectl create secret docker-registry regcred \
  --docker-server="$NEXUS_URL" \
  --docker-username="$NEXUS_USERNAME" \
  --docker-password="$NEXUS_API_KEY" \
  --namespace="$NAMESPACE"
```

**`KUBE_CONFIG_CONTENT`** is only needed when GitLab cannot reach your
cluster directly (e.g. no GitLab Kubernetes Agent integration). It is the
base64 of your kubeconfig — the file `kubectl` uses to connect:

```bash
# Linux / Git Bash:
cat ~/.kube/config | base64 -w0
# macOS (no -w flag):
cat ~/.kube/config | base64 | tr -d '\n'
```

Paste the output into the `KUBE_CONFIG_CONTENT` variable (Masked). If your
kubeconfig has several contexts, also set `KUBE_CONTEXT` to the right one.

> Note: never define `APP_NAME`/`NEXUS_URL`/etc. in a `variables:` block
> inside `.gitlab-ci.yml` — pipeline variables override project settings.
> Keep that block minimal (it already is).
>
> If your Nexus registry is plain HTTP (no TLS), the pipeline already starts
> the build daemon with `--insecure-registry $NEXUS_URL`, so nothing to do.
> Only delete those lines (at the top of `.gitlab-ci.yml`) if your Nexus has
> a valid TLS certificate.

### Reuse this pipeline from other projects

Instead of copying this file everywhere, other projects can **include** it —
they always get the latest validate/build/deploy logic, and their own
Dockerfile, `k8s/` manifests and CI/CD variables are used automatically:

```yaml
include:
  - project: '<group>/kube-template'
    ref: 'main'          # or pin a version: ref: 'v1.2.0'
    file: '/.gitlab-ci.yml'
```

No scripts are needed in the consuming project — everything lives in this
pipeline file. Update the template once, all consuming projects pick it up.

---

## Local development (optional, needs `kubectl`)

```bash
make preview   # see exactly what will be deployed
make deploy    # apply to the cluster your kubectl points at
make status    # watch it come up
make logs      # tail logs
make undeploy  # tear everything down
```

Want a quick test without a Service or Ingress? `make pod` runs a single
pod of your image and `make logs` shows its output.

---

## Advanced

### Convert an existing docker-compose.yml

```bash
# Requires Python 3 + PyYAML (pip install pyyaml)
make compose
# Generates k8s/deployment-<svc>.yaml, k8s/service-<svc>.yaml, ...
# Review the generated files, delete what you don't need, then:
make deploy
```

### Add more Kubernetes resources

Drop any standard YAML file into `k8s/` — it is applied automatically:
- `hpa.yaml` — autoscaling
- `pvc.yaml` — persistent storage
- `job.yaml` / `cronjob.yaml` — batch tasks
- `secret.yaml` — already provided by the wizard (names only, values from CI variables)

Remove an existing one by renaming it (e.g. `mv k8s/ingress.yaml k8s/ingress.yaml.disabled`)
or deleting it.

### Edit values without the wizard

`.env` (generated by the wizard, not committed) contains every setting.
Change it and run `make deploy`, or just push — CI reads the same defaults.

### Files in `k8s/`

| File | What it creates | You need it if... |
|---|---|---|
| `deployment.yaml` | Your app (copies, health checks, resources) | always |
| `service.yaml` | Internal networking for your app | another service must reach it, or you use an ingress |
| `ingress.yaml` | Public domain + routing | you have a domain |
| `configmap.yaml` | Environment variables (non-secrets) | your app reads env vars |
| `secret.yaml` | Secret environment variables (values from CI vars) | you have secrets (see above) |
| `namespace.yaml` | Isolated space for your app | always (created automatically) |
| `pod.yaml` | Single quick-test pod | `make pod` only |

---

## Troubleshooting

| Problem | Fix |
|---|---|
| CI fails at **validate** with `APP_NAME is not set` | Set `APP_NAME` in GitLab CI/CD variables |
| CI fails at **validate** with `Secret 'X' has no value set` | Add `X` as a GitLab CI/CD variable (Masked + Protected) |
| CI fails at **build** with `unable to prepare context` | Make sure `app/Dockerfile` exists |
| CI fails at **deploy** with `refusing to render incomplete manifests` | A variable named in `k8s/*.yaml` isn't set — the message lists which |
| Warning: `skipping ingress.yaml — INGRESS_HOST is not set` | Fine — the app stays internal. Set `INGRESS_HOST` to expose it on a domain |
| App restarts in a loop | Check `make logs` — the health check path may be wrong (`make configure`) |
| Want to test before pushing | Run `make configure` + `make preview` locally |
