#!/usr/bin/env python3
"""Interactive Kubernetes deployer.

Asks for each setting (namespace, environment, image, replicas, ...) and
uses a default value when you press Enter with no input.

Non-interactive mode (used automatically in CI, or with --yes) skips all
prompts and uses environment variables or the built-in defaults.

Usage:
    python3 scripts/deploy.py             # interactive prompts
    python3 scripts/deploy.py --preview   # show rendered manifests, don't apply
    python3 scripts/deploy.py --yes       # no prompts, use defaults/env vars
    python3 scripts/deploy.py --pod       # apply just the quick-test pod

Values are taken from the environment, then from .env if it exists, then
from sensible defaults. Run 'python3 scripts/configure.py' to generate .env.
"""

import os
import re
import shutil
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

ENV_FILE = REPO_ROOT / ".env"


def load_dotenv() -> None:
    """Load .env into os.environ, without overriding real environment vars."""
    if not ENV_FILE.is_file():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")

def _is_positive_int(value: str) -> bool:
    """a positive integer"""
    return value.isdigit() and int(value) > 0


def _is_port(value: str) -> bool:
    """a port number (1-65535)"""
    return value.isdigit() and 1 <= int(value) <= 65535


def _is_service_type(value: str) -> bool:
    """ClusterIP, NodePort or LoadBalancer"""
    return value in {"ClusterIP", "NodePort", "LoadBalancer"}


PROMPTS = [
    ("APP_NAME", "App name"),
    ("NAMESPACE", "Namespace", "app name"),
    ("ENVIRONMENT", "Environment"),
    ("IMAGE", "Image", "app name:latest"),
    ("REPLICAS", "Replicas", None, _is_positive_int),
    ("APP_PORT", "App port (container)", None, _is_port),
    ("SERVICE_PORT", "Service port", None, _is_port),
    ("SERVICE_TYPE", "Service type", None, _is_service_type),
    ("INGRESS_HOST", "Ingress host", "app name.example.com"),
    ("INGRESS_CLASS", "Ingress class"),
    ("INGRESS_PATH", "Ingress path"),
    ("HEALTHCHECK_PATH", "Healthcheck path"),
    ("MEMORY_REQUEST", "Memory request"),
    ("CPU_REQUEST", "CPU request"),
    ("MEMORY_LIMIT", "Memory limit"),
    ("CPU_LIMIT", "CPU limit"),
]


def envsubst(text: str) -> str:
    """Expand ${VAR}, $VAR and ${VAR:-default} using os.environ."""

    def repl(match: re.Match) -> str:
        name, default = match.group(1), match.group(3)
        if default is None:
            return os.environ.get(name, "")
        return os.environ.get(name, default)

    pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\}|\$([A-Za-z_][A-Za-z0-9_]*)")
    return pattern.sub(repl, text)


def default_for(key: str, app_name: str) -> str:
    """Prefer an env var (even empty = explicitly disabled), then a default."""
    if key in os.environ:
        return os.environ[key]
    if key == "APP_NAME":
        return DEFAULTS["APP_NAME"]
    if key == "NAMESPACE":
        return app_name
    if key == "INGRESS_HOST":
        return ""
    if key == "IMAGE":
        ci_image = os.environ.get("CI_REGISTRY_IMAGE", "")
        ci_sha = os.environ.get("CI_COMMIT_SHORT_SHA", "")
        if ci_image and ci_sha:
            return f"{ci_image}:{ci_sha}"
        return f"{app_name}:latest"
    return DEFAULTS[key]


def ask(label: str, default: str, validate=None) -> str:
    while True:
        raw = input(f"  {label} [{default}]: ").strip()
        value = raw or default
        if validate is None or validate(value):
            return value
        print(f"    Invalid: expected {validate.__doc__}.")


def gather_settings(interactive: bool) -> dict:
    app_name = default_for("APP_NAME", "")
    print("==> Kubernetes deployment settings (Enter = default)")
    if not interactive:
        print("    Non-interactive mode: using defaults / environment variables")
    settings = {}
    for item in PROMPTS:
        key, label = item[0], item[1]
        validate = item[3] if len(item) > 3 else None
        default = default_for(key, app_name)
        if interactive:
            settings[key] = ask(label, default, validate)
        else:
            settings[key] = default
        if key == "APP_NAME":
            app_name = settings[key]
    return settings


_VAR_TOKEN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\}")


def check_missing_vars(files: list[Path]) -> None:
    """Fail if any ${VAR} placeholder (without a :-default) is unresolved."""
    missing: set[str] = set()
    for path in files:
        for match in _VAR_TOKEN.finditer(path.read_text()):
            name, default = match.group(1), match.group(3)
            if default is None and name not in os.environ:
                missing.add(name)
    if not missing:
        return
    print("ERROR: refusing to render incomplete manifests — these variables "
          "are not set:", file=sys.stderr)
    for var in sorted(missing):
        print(f"  - {var}", file=sys.stderr)
    print("If these are secrets, set them as GitLab CI/CD variables "
          "(Masked + Protected), or in .env / your shell locally.",
          file=sys.stderr)
    sys.exit(1)


def render_manifests() -> list[tuple[str, str]]:
    manifest_dir = REPO_ROOT / "k8s"
    if not manifest_dir.is_dir():
        print(f"ERROR: {manifest_dir} not found.", file=sys.stderr)
        sys.exit(1)

    files = [m for m in sorted(manifest_dir.glob("*.yaml"))
             if m.name not in ("namespace.yaml", "kustomization.yaml")]

    if (manifest_dir / "ingress.yaml").is_file() and not os.environ.get("INGRESS_HOST"):
        files = [m for m in files if m.name != "ingress.yaml"]
        print("NOTE: skipping ingress.yaml — INGRESS_HOST is not set. "
              "Set it (or remove the file) to expose your app publicly.")

    check_missing_vars(files)
    return [(m.name, envsubst(m.read_text())) for m in files]


def install_secret_manifest() -> None:
    """If k8s/secret.yaml is active, wire it into the deployment's envFrom.

    Injects ${SECRET_LINES} so the deployment references the secret only when
    one is configured (a missing secret would otherwise block pod startup).
    """
    app_name = os.environ.get("APP_NAME")
    if app_name and (REPO_ROOT / "k8s" / "secret.yaml").is_file():
        os.environ["SECRET_LINES"] = (
            f"            - secretRef:\n                name: {app_name}-secret")
    else:
        os.environ["SECRET_LINES"] = ""


def apply_manifests(settings: dict, manifests: list[tuple[str, str]]) -> None:
    namespace = settings["NAMESPACE"]
    if run(["kubectl", "get", "namespace", namespace]).returncode != 0:
        run(["kubectl", "create", "namespace", namespace])
    for name, rendered in manifests:
        print(f"  Applying {name}")
        result = subprocess.run(["kubectl", "apply", "-f", "-"], input=rendered,
                                capture_output=True, text=True)
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        if result.returncode != 0:
            sys.exit(result.returncode)


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Deploy k8s/ manifests to Kubernetes")
    parser.add_argument("--preview", action="store_true",
                        help="print rendered manifests without applying")
    parser.add_argument("--yes", action="store_true",
                        help="skip prompts, use defaults/environment")
    parser.add_argument("--pod", action="store_true",
                        help="apply only the quick-test pod (k8s/pod.yaml)")
    args = parser.parse_args()

    load_dotenv()
    interactive = not (args.yes or os.environ.get("CI") or not sys.stdin.isatty())

    settings = gather_settings(interactive)

    if not settings["APP_NAME"]:
        print("ERROR: APP_NAME is not set. Run 'python3 scripts/configure.py' "
              "or export APP_NAME.", file=sys.stderr)
        return 1

    if args.pod:
        pod_file = REPO_ROOT / "k8s" / "pod.yaml"
        if not pod_file.exists():
            disabled = REPO_ROOT / "k8s" / "pod.yaml.disabled"
            if disabled.exists():
                disabled.rename(pod_file)

    os.environ.update(settings)

    if args.pod:
        if shutil.which("kubectl") is None:
            print("ERROR: 'kubectl' not found on PATH.", file=sys.stderr)
            return 1
        pod_file = REPO_ROOT / "k8s" / "pod.yaml"
        if not pod_file.exists():
            disabled = REPO_ROOT / "k8s" / "pod.yaml.disabled"
            if disabled.exists():
                disabled.rename(pod_file)
        if not pod_file.exists():
            print("ERROR: k8s/pod.yaml not found.", file=sys.stderr)
            return 1
        unresolved = sorted({m.group(1) for m in _VAR_TOKEN.finditer(pod_file.read_text())
                             if m.group(3) is None and m.group(1) not in os.environ})
        if unresolved:
            print("ERROR: refusing to create the pod — these variables are "
                  f"not set: {', '.join(unresolved)}", file=sys.stderr)
            return 1
        rendered = envsubst(pod_file.read_text())
        if not os.path.exists(REPO_ROOT / "k8s" / "namespace.yaml"):
            if run(["kubectl", "get", "namespace", settings["NAMESPACE"]]).returncode != 0:
                run(["kubectl", "create", "namespace", settings["NAMESPACE"]])
        print(f"==> Creating quick-test pod {settings['APP_NAME']} "
              f"(namespace {settings['NAMESPACE']})")
        result = subprocess.run(["kubectl", "apply", "-f", "-"], input=rendered,
                                capture_output=True, text=True)
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        if result.returncode != 0:
            return result.returncode
        print(f"==> Done! Run: make logs")
        return 0

    install_secret_manifest()
    manifests = render_manifests()

    print("==> Deployment summary")
    for key, label in ((p[0], p[1]) for p in PROMPTS):
        print(f"    {label}: {settings[key]}")
    print(f"    Manifests: {len(manifests)} file(s)")

    if args.preview:
        for name, rendered in manifests:
            print(f"--- {name} ---")
            print(rendered, end="")
        print("==> Preview only, nothing applied.")
        return 0

    if interactive:
        confirm = input("Apply to cluster? [Y/n]: ").strip().lower()
        if confirm not in ("", "y", "yes"):
            print("Aborted.")
            return 1

    if shutil.which("kubectl") is None:
        print("ERROR: 'kubectl' not found on PATH.", file=sys.stderr)
        return 1

    print(f"==> Deploying to namespace {settings['NAMESPACE']} ({settings['ENVIRONMENT']})")
    apply_manifests(settings, manifests)
    print(f"==> Done! Run: kubectl get all -n {settings['NAMESPACE']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
