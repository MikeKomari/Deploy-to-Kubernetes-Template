#!/usr/bin/env python3
"""Deploy Kubernetes manifests from the k8s/ directory.

Cross-platform replacement for deploy.sh. Uses kubectl via subprocess.
Environment variables (APP_NAME, NAMESPACE, ...) control the rendered
manifests; `${VAR}` / `${VAR:-default}` placeholders are substituted.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

DEFAULTS = {
    "APP_NAME": "myapp",
    "ENVIRONMENT": "production",
    "REPLICAS": "2",
    "APP_PORT": "3000",
    "SERVICE_PORT": "80",
    "SERVICE_TYPE": "ClusterIP",
    "INGRESS_CLASS": "nginx",
    "INGRESS_PATH": "/",
    "HEALTHCHECK_PATH": "/health",
    "MEMORY_REQUEST": "128Mi",
    "CPU_REQUEST": "100m",
    "MEMORY_LIMIT": "256Mi",
    "CPU_LIMIT": "500m",
}


def envsubst(text: str) -> str:
    """Expand ${VAR}, $VAR and ${VAR:-default} using os.environ."""

    def repl(match: re.Match) -> str:
        name, default = match.group(1), match.group(3)
        if default is None:
            return os.environ.get(name, "")
        return os.environ.get(name, default)

    pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\}|\$([A-Za-z_][A-Za-z0-9_]*)")
    return pattern.sub(repl, text)


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def main() -> int:
    for key, value in DEFAULTS.items():
        os.environ.setdefault(key, value)

    app_name = os.environ.get("APP_NAME", "myapp")
    os.environ.setdefault("NAMESPACE", app_name)

    ci_image = os.environ.get("CI_REGISTRY_IMAGE", "")
    ci_sha = os.environ.get("CI_COMMIT_SHORT_SHA", "")
    os.environ.setdefault("IMAGE", f"{ci_image}:{ci_sha}")

    ingress_host = os.environ.get("INGRESS_HOST") or f"{app_name}.example.com"
    os.environ["INGRESS_HOST"] = ingress_host

    namespace = os.environ["NAMESPACE"]
    environment = os.environ["ENVIRONMENT"]
    image = os.environ["IMAGE"]

    print(f"==> Deploying {app_name} to namespace {namespace} ({environment})")
    print(f"    Image: {image}")

    result = run(["kubectl", "get", "namespace", namespace])
    if result.returncode != 0:
        print(run(["kubectl", "create", "namespace", namespace]).stdout, end="")

    manifest_dir = REPO_ROOT / "k8s"
    if not manifest_dir.is_dir():
        print(f"ERROR: {manifest_dir} not found.", file=sys.stderr)
        return 1

    for manifest in sorted(manifest_dir.glob("*.yaml")):
        if manifest.name == "namespace.yaml" or manifest.name == "kustomization.yaml":
            continue
        print(f"  Applying {manifest.name}")
        rendered = envsubst(manifest.read_text())
        result = subprocess.run(["kubectl", "apply", "-f", "-"], input=rendered,
                                capture_output=True, text=True)
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        if result.returncode != 0:
            return result.returncode

    print(f"==> Done! Run: kubectl get all -n {namespace}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
