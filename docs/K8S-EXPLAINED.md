# Kubernetes, in plain words

You don't need to read this to use the template — `make configure` handles
everything. But if you're curious what's actually happening, here it is,
without the jargon.

---

## The big picture

Kubernetes (K8s) is a system for running containers across many computers.

- A **container** is your app, packaged with everything it needs to run.
- Kubernetes decides *which computer* runs your container and makes sure it
  stays running — if it crashes, it restarts it; if the computer dies, it
  starts it somewhere else.

The files in `k8s/` are "descriptions of what should exist" — the template
fills them in with your answers from `make configure`.

---

## The pieces

### Pod — "one running copy of your app"

A pod is the smallest unit. In this template one pod = one copy of your
container. When you say "how many copies should run?" (REPLICAS), you're
saying how many pods to keep running. If one dies, Kubernetes starts a new
one automatically.

### Deployment — "keep this many copies alive"

A deployment is the part that watches your pods and keeps the number of
copies you asked for. It also does rolling updates: when you push new code,
it starts a new copy with the new image, waits until it's healthy, then
switches over — so there's no downtime.

### Service — "a stable address for your app"

Pods come and go; every time one restarts it gets a new IP address. A
service is a permanent name that always points at your running pods. Other
apps in the cluster can reach your app through the service without caring
which pod is behind it at the moment.

- `ClusterIP` — reachable only from inside the cluster (default)
- `NodePort` — also reachable from outside on a port of each machine
- `LoadBalancer` — gets a public IP from the cloud provider

### Ingress — "the front door (domain)"

An ingress maps a domain name like `api.example.com` to your service, and
can route different paths to different apps. The template uses `nginx` as
the ingress controller — the component that actually accepts the HTTP
traffic and forwards it.

### ConfigMap — "environment variables"

A configmap is a list of key-value pairs injected into your container as
environment variables. You can add your own via the wizard ("extra
environment variables") — e.g. `API_URL`, `LOG_LEVEL` — without rebuilding
your image. Anything here is **not secret**: the values are committed to
your repository and readable by anyone with cluster access.

### Secret — "the same, but actually secret"

A secret behaves like a ConfigMap, but its values are stored base64-encoded
and are only visible to pods that ask for it. In this template the wizard
only writes the secret **names** into `k8s/secret.yaml`; the **values** are
never committed. They are injected at deploy time from your GitLab CI/CD
variables (set them as *Masked* + *Protected*), or from `.env` when you
deploy from your laptop. The deployment only wires the secret in when
`secret.yaml` exists — and refuses to deploy if a value is missing, rather
than silently shipping an app with an empty password.

Rule of thumb: log levels and URLs → ConfigMap. Passwords and keys → Secret.

### Namespace — "a separate folder"

A namespace is a separate space inside the cluster, so your app's things
don't collide with other teams' apps. The template creates one per project
named after your app. Deleting a namespace deletes everything in it —
that's what `make undeploy` does.

### Health checks — "is the app actually working?"

A container can be "running" but broken. Kubernetes checks a URL on your
app (the health check path, usually `/health`) every few seconds:

- **readiness** — if it fails, the app is taken out of rotation (no traffic)
- **liveness** — if it fails repeatedly, the app is restarted

Make sure your app answers on that path with a success status.

### Resources — "memory and CPU limits"

Each copy of your app can ask for a slice of the machine's resources:

- `requests` — the minimum guaranteed amount
- `limits` — the maximum it may use (above that, it gets restarted)

Examples: `128Mi` = 128 megabytes, `1Gi` = 1 gigabyte, `500m` = half a CPU
core, `2` = two full cores.

---

## What happens when you push

1. **validate** — your settings are checked; problems are reported clearly
2. **build** — your Dockerfile is turned into an image and pushed to the
   GitLab container registry
3. **deploy** — the k8s files are filled in with your settings and applied:
   namespace → deployment → service → configmap → ingress

"Applied" means: *"make sure this exists"*. The next push simply updates
what's already running — the deployment swaps in the new image with a
rolling update, so your app never goes down.

---

## Questions you may still have

**Do I need to install anything?**
No. GitLab CI does everything. Only if you want to deploy from your own
laptop do you need `kubectl` (see the README).

**How do I see my app's logs?**
`make logs` locally, or check the pipeline job output in GitLab. The
deployment also restarts a crashed app automatically — first thing to do
with a restarting app is read the logs.

**How do I change a setting after deploying?**
Re-run `make configure` and push. The deployment updates itself — no
downtime.

**Can I break something?**
The namespace is isolated; `make undeploy` deletes everything but only in
your own namespace.
