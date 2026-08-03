#!/usr/bin/env python3
"""One-command setup wizard for your Kubernetes deployment.

You do NOT need to know Kubernetes. Answer a few plain-English questions
and this script writes your `.env` and switches the right k8s manifests on.

Usage:
    python3 scripts/configure.py             # interactive wizard
    python3 scripts/configure.py --yes       # no prompts (.env / env vars)
    python3 scripts/configure.py --check     # validate config (used in CI)

Then just commit and push — GitLab CI does the rest.
"""

import argparse
import os
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
K8S_DIR = REPO_ROOT / "k8s"
ENV_FILE = REPO_ROOT / ".env"

NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
ENVKEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
HOST_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)+$", re.IGNORECASE)
RESOURCE_RE = re.compile(r"^[0-9]+([mMkKgGiT]i?|m)?$")

DEFAULTS = {
    "APP_NAME": "myapp",
    "ENVIRONMENT": "production",
    "REPLICAS": "2",
    "APP_PORT": "3000",
    "SERVICE_PORT": "80",
    "SERVICE_TYPE": "ClusterIP",
    "INGRESS_HOST": "",
    "INGRESS_CLASS": "nginx",
    "INGRESS_PATH": "/",
    "HEALTHCHECK_PATH": "/health",
    "MEMORY_REQUEST": "128Mi",
    "CPU_REQUEST": "100m",
    "MEMORY_LIMIT": "256Mi",
    "CPU_LIMIT": "500m",
}

# Everything written to .env, in this order (EXTRA_ENV, SECRETS last).
KEYS = [
    "APP_NAME", "NAMESPACE", "ENVIRONMENT", "IMAGE", "REPLICAS",
    "APP_PORT", "SERVICE_PORT", "SERVICE_TYPE",
    "INGRESS_HOST", "INGRESS_CLASS", "INGRESS_PATH",
    "HEALTHCHECK_PATH",
    "MEMORY_REQUEST", "CPU_REQUEST", "MEMORY_LIMIT", "CPU_LIMIT",
    "EXTRA_ENV", "SECRETS",
]


def _valid_app_name(v): return bool(NAME_RE.match(v))
def _valid_port(v): return v.isdigit() and 1 <= int(v) <= 65535
def _valid_replicas(v): return v.isdigit() and int(v) > 0
def _valid_service_type(v): return v in {"ClusterIP", "NodePort", "LoadBalancer"}
def _valid_resource(v): return bool(RESOURCE_RE.match(v))
def _valid_host(v): return bool(HOST_RE.match(v))


def env(name: str) -> str:
    return os.environ.get(name, "")


def value_for(key: str) -> str:
    """Env var, else .env, else default. For prompts & --yes mode."""
    val = env(key)
    if val:
        return val
    val = dotenv_value(key)
    if val:
        return val
    if key == "NAMESPACE":
        return value_for("APP_NAME")
    if key in ("EXTRA_ENV", "SECRETS"):
        return ""
    if key == "IMAGE":
        ci_image, ci_sha = env("CI_REGISTRY_IMAGE"), env("CI_COMMIT_SHORT_SHA")
        if ci_image and ci_sha:
            return f"{ci_image}:{ci_sha}"
        return f"{value_for('APP_NAME')}:latest"
    return DEFAULTS[key]


def has_value(key: str) -> bool:
    """True if the key is explicitly set (env var or .env), not a default."""
    return bool(env(key)) or bool(dotenv_value(key))


def load_dotenv_values() -> dict:
    """Parse .env without letting it override real environment variables."""
    values = {}
    if not ENV_FILE.is_file():
        return values
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key and key not in values:
            values[key] = val.strip().strip('"').strip("'")
    return values


_dotenv_cache = None


def dotenv_value(key: str) -> str:
    global _dotenv_cache
    if _dotenv_cache is None:
        _dotenv_cache = load_dotenv_values()
    return _dotenv_cache.get(key, "")


# ---------- plain-English prompts ----------

def ask(label: str, default: str, validate=None, hint: str = "") -> str:
    suffix = f" ({hint})" if hint else ""
    while True:
        raw = input(f"  ? {label} [{default}]{suffix}: ").strip()
        value = raw or default
        if validate is None or validate(value):
            return value
        print(f"    That doesn't look right — try again.")


def ask_yes_no(label: str, default: bool = True) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        raw = input(f"  ? {label} [{suffix}]: ").strip().lower()
        if raw in ("", "y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("    Please answer yes or no.")


# ---------- Dockerfile / compose detection ----------

def detect_port() -> str:
    """Find the port the app listens on: Dockerfile EXPOSE, then compose."""
    for dockerfile in (REPO_ROOT / "app" / "Dockerfile", REPO_ROOT / "Dockerfile"):
        if dockerfile.is_file():
            for line in dockerfile.read_text(errors="ignore").splitlines():
                m = re.search(r"\bEXPOSE\s+(\d+)", line, re.IGNORECASE)
                if m:
                    return m.group(1)
    compose = REPO_ROOT / "docker-compose.yml"
    if compose.is_file():
        try:
            import yaml
            data = yaml.safe_load(compose.read_text()) or {}
        except Exception:
            return ""
        for svc in (data.get("services") or {}).values():
            for raw in (svc or {}).get("ports") or []:
                if isinstance(raw, dict):
                    return str(raw.get("target", ""))
                text = str(raw)
                if ":" in text:
                    return text.rsplit(":", 1)[1].split("/")[0]
                return text.split("/")[0]
    return ""


def default_app_name() -> str:
    if env("APP_NAME"):
        return env("APP_NAME")
    name = REPO_ROOT.name.lower()
    name = re.sub(r"[^a-z0-9-]+", "-", name).strip("-")
    if name and NAME_RE.match(name):
        return name
    return DEFAULTS["APP_NAME"]


# ---------- manifest toggling ----------

def toggle(stem: str, enabled: bool):
    """Rename k8s/<stem>.yaml <-> k8s/<stem>.yaml.disabled."""
    active = K8S_DIR / f"{stem}.yaml"
    disabled = K8S_DIR / f"{stem}.yaml.disabled"
    if enabled and disabled.is_file():
        disabled.rename(active)
        print(f"  enabled  {active.name}")
    elif not enabled and active.is_file():
        active.rename(disabled)
        print(f"  disabled {active.name}")


def apply_file_selection(ingress_host: str, use_pod: bool):
    print("\n==> Choosing which Kubernetes files to use")
    toggle("deployment", True)
    toggle("service", True)
    toggle("configmap", True)
    toggle("ingress", bool(ingress_host))
    toggle("pod", use_pod)


# ---------- .env writing ----------

def parse_extra_env(raw: str) -> list[tuple[str, str]]:
    pairs = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            print(f"    Skipping '{item}' — expected KEY=VALUE")
            continue
        key, _, val = item.partition("=")
        if not ENVKEY_RE.match(key.strip()):
            print(f"    Skipping '{item}' — invalid key name")
            continue
        pairs.append((key.strip(), val.strip()))
    return pairs


def update_configmap(extra: list[tuple[str, str]]):
    """Add extra env vars to k8s/configmap.yaml (first 3 stay templated)."""
    if not K8S_DIR.is_dir():
        return
    lines = [
        "apiVersion: v1",
        "kind: ConfigMap",
        "metadata:",
        "  name: ${APP_NAME}-config",
        "  namespace: ${NAMESPACE}",
        "  labels:",
        "    app: ${APP_NAME}",
        "    env: ${ENVIRONMENT}",
        "data:",
        '  APP_NAME: "${APP_NAME}"',
        '  APP_ENV: "${ENVIRONMENT}"',
        '  APP_PORT: "${APP_PORT}"',
    ]
    for key, val in extra:
        lines.append(f"  {key}: \"{val}\"")
    (K8S_DIR / "configmap.yaml").write_text("\n".join(lines) + "\n")
    if extra:
        print("  configmap.yaml  updated with extra environment variables")


# ---------- secrets ----------

def parse_secret_names(raw: str) -> list[str]:
    names = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if not ENVKEY_RE.match(item):
            print(f"    Skipping '{item}' — invalid name (use A-Z, digits, _)")
            continue
        if item not in names:
            names.append(item)
    return names


def secret_value(name: str) -> str:
    """A secret's value: real env var, else .env."""
    return env(name) or dotenv_value(name)


def update_secret(names: list[str]):
    """Write k8s/secret.yaml with the secret NAMES only (never their values).

    The values come from environment / GitLab CI/CD variables at deploy time;
    nothing secret is ever written to disk or committed.
    """
    if not K8S_DIR.is_dir():
        return
    if not names:
        toggle("secret", False)
        return
    toggle("secret", True)
    lines = [
        "apiVersion: v1",
        "kind: Secret",
        "metadata:",
        "  name: ${APP_NAME}-secret",
        "  namespace: ${NAMESPACE}",
        "type: Opaque",
        "stringData:",
    ]
    for name in names:
        lines.append(f'  {name}: "${{{name}}}"')
    (K8S_DIR / "secret.yaml").write_text("\n".join(lines) + "\n")
    print("  secret.yaml  updated — values are read from your variables at deploy time")


def write_env(settings: dict):
    serialized = ",".join(f"{k}={v}" for k, v in settings["EXTRA"])
    secrets = ",".join(settings.get("SECRET_NAMES", []))

    def r(key, default=""):
        return settings.get(key, default)

    lines = [
        "# Kubernetes deployment configuration (generated by configure.py)",
        "# Re-run 'make configure' to change these.",
        f"APP_NAME={r('APP_NAME')}",
        f"NAMESPACE={r('NAMESPACE', r('APP_NAME'))}",
        f"ENVIRONMENT={r('ENVIRONMENT')}",
        f"IMAGE={r('IMAGE')}",
        f"REPLICAS={r('REPLICAS')}",
        f"APP_PORT={r('APP_PORT')}",
        f"SERVICE_PORT={r('SERVICE_PORT')}",
        f"SERVICE_TYPE={r('SERVICE_TYPE')}",
        f"INGRESS_HOST={r('INGRESS_HOST')}",
        f"INGRESS_CLASS={r('INGRESS_CLASS')}",
        f"INGRESS_PATH={r('INGRESS_PATH')}",
        f"HEALTHCHECK_PATH={r('HEALTHCHECK_PATH')}",
        f"MEMORY_REQUEST={r('MEMORY_REQUEST')}",
        f"CPU_REQUEST={r('CPU_REQUEST')}",
        f"MEMORY_LIMIT={r('MEMORY_LIMIT')}",
        f"CPU_LIMIT={r('CPU_LIMIT')}",
        f"EXTRA_ENV={serialized}",
        f"SECRETS={secrets}",
    ]
    ENV_FILE.write_text("\n".join(lines) + "\n")
    print(f"\n==> Saved your configuration to {ENV_FILE.name}")


# ---------- interactive wizard ----------

def run_wizard() -> dict:
    port_default = detect_port() or value_for("APP_PORT")
    app_name_default = default_app_name()

    print("==> Deployment setup wizard")
    print("    Answer the questions below. Press Enter to accept the default.")
    print("    (You can change anything later by re-running this wizard.)\n")

    app_name = ask("What is your app called?",
                   app_name_default, _valid_app_name,
                   hint="lowercase letters, numbers, dashes")
    settings = {"APP_NAME": app_name}

    settings["APP_PORT"] = ask(
        f"Which port does {app_name} listen on inside the container?",
        port_default, _valid_port, hint="from your Dockerfile if found")

    settings["REPLICAS"] = ask(
        "How many copies of your app should run? (2 for reliability)",
        value_for("REPLICAS"), _valid_replicas)

    if ask_yes_no("Do you want a public domain for your app? (e.g. api.example.com)", default=False):
        settings["INGRESS_HOST"] = ask("Domain name", value_for("INGRESS_HOST"),
                                       _valid_host, hint="must be a real domain")
    else:
        settings["INGRESS_HOST"] = ""
        print("    OK — the app will only be reachable inside the cluster.")

    settings["SERVICE_TYPE"] = ask(
        "How should the app be reachable? (ClusterIP = internal only)",
        value_for("SERVICE_TYPE"), _valid_service_type,
        hint="ClusterIP, NodePort or LoadBalancer")

    settings["MEMORY_LIMIT"] = ask(
        "Max memory per copy (e.g. 256Mi, 1Gi)?", value_for("MEMORY_LIMIT"), _valid_resource)
    settings["CPU_LIMIT"] = ask(
        "Max CPU per copy (e.g. 500m = half a core, 2 = two cores)?",
        value_for("CPU_LIMIT"), _valid_resource)

    settings["HEALTHCHECK_PATH"] = ask(
        "Path that returns success when the app is healthy (usually /health)?",
        value_for("HEALTHCHECK_PATH"))

    print("\n  Optional: extra environment variables (e.g. API_URL=https://x, LOG_LEVEL=debug)")
    raw_extra = input("  ? Extra environment variables, comma-separated (empty = none): ").strip()
    settings["EXTRA"] = parse_extra_env(raw_extra)

    print("\n  Optional: secrets (e.g. DB_PASSWORD, API_KEY).")
    print("  You only name them here — their VALUES are never stored or committed.")
    print("  They will come from your GitLab CI/CD variables at deploy time.")
    print("  (For local testing, put them in .env like DB_PASSWORD=mypass)")
    raw_secrets = input("  ? Secret variable names, comma-separated (empty = none): ").strip()
    settings["SECRET_NAMES"] = parse_secret_names(raw_secrets)

    settings["ENVIRONMENT"] = value_for("ENVIRONMENT")
    settings["NAMESPACE"] = app_name if not has_value("NAMESPACE") else value_for("NAMESPACE")
    if has_value("IMAGE") or env("CI_REGISTRY_IMAGE"):
        settings["IMAGE"] = value_for("IMAGE")
    else:
        settings["IMAGE"] = f"{app_name}:latest"
    settings["SERVICE_PORT"] = value_for("SERVICE_PORT")
    settings["INGRESS_CLASS"] = value_for("INGRESS_CLASS")
    settings["INGRESS_PATH"] = value_for("INGRESS_PATH")
    settings["MEMORY_REQUEST"] = value_for("MEMORY_REQUEST")
    settings["CPU_REQUEST"] = value_for("CPU_REQUEST")
    return settings


# ---------- non-interactive ----------

def resolve_yes() -> dict:
    settings = {key: value_for(key) for key in KEYS}
    settings["EXTRA"] = parse_extra_env(settings.pop("EXTRA_ENV"))
    settings["SECRET_NAMES"] = parse_secret_names(settings.pop("SECRETS"))
    settings["NAMESPACE"] = settings["NAMESPACE"] or settings["APP_NAME"]
    return settings


# ---------- validation ----------

def validate(settings: dict, check_only: bool, explicit: set | None = None,
             wizard_mode: bool = False) -> list[str]:
    errors = []
    warnings = []
    explicit = explicit or set()

    app_name = settings.get("APP_NAME", "")
    if "APP_NAME" not in explicit and not has_value("APP_NAME"):
        errors.append("APP_NAME is not set. Run 'make configure' or set APP_NAME "
                      "in your GitLab CI/CD variables.")
    elif not _valid_app_name(app_name):
        errors.append(f"APP_NAME '{app_name}' is invalid. Use lowercase letters, "
                      "numbers and dashes (e.g. my-api).")

    for key in ("APP_PORT", "SERVICE_PORT"):
        val = settings.get(key, "")
        if val and not _valid_port(val):
            errors.append(f"{key} '{val}' is not a valid port (1-65535).")

    replicas = settings.get("REPLICAS", "")
    if replicas and not _valid_replicas(replicas):
        errors.append(f"REPLICAS '{replicas}' must be a positive number.")

    for key in ("MEMORY_REQUEST", "CPU_REQUEST", "MEMORY_LIMIT", "CPU_LIMIT"):
        val = settings.get(key, "")
        if val and not _valid_resource(val):
            errors.append(f"{key} '{val}' is not valid (examples: 128Mi, 1Gi, 500m, 2).")

    service_type = settings.get("SERVICE_TYPE", "")
    if service_type and not _valid_service_type(service_type):
        errors.append(f"SERVICE_TYPE '{service_type}' must be ClusterIP, NodePort or LoadBalancer.")

    ingress = settings.get("INGRESS_HOST", "")
    if ingress and not _valid_host(ingress):
        errors.append(f"INGRESS_HOST '{ingress}' is not a valid domain name.")

    ingress_active = (K8S_DIR / "ingress.yaml").is_file()
    if ingress_active and not ingress and not wizard_mode:
        warnings.append("k8s/ingress.yaml is enabled but INGRESS_HOST is empty — "
                        "it will be skipped at deploy time. Run 'make configure' "
                        "and choose a domain to expose your app publicly.")

    for name in settings.get("SECRET_NAMES", []):
        if secret_value(name):
            continue
        message = (f"Secret '{name}' has no value set. Add it as a Masked + "
                   "Protected GitLab CI/CD variable, or put DB-like values in "
                   ".env for local runs.")
        if wizard_mode:
            warnings.append(message)
        else:
            errors.append(message)

    dockerfile = REPO_ROOT / "app" / "Dockerfile"
    if not dockerfile.is_file() and not (REPO_ROOT / "Dockerfile").is_file():
        warnings.append("No Dockerfile found in app/ (or repo root). "
                        "The pipeline cannot build your image without one.")

    if check_only:
        settings_list = {k: settings.get(k, "") for k in KEYS}
        if settings.get("EXTRA"):
            settings_list["EXTRA_ENV"] = ",".join(f"{k}={v}" for k, v in settings["EXTRA"])
        if settings.get("SECRET_NAMES"):
            settings_list["SECRETS"] = ",".join(settings["SECRET_NAMES"])
        print("==> Current configuration")
        for key in KEYS:
            print(f"    {key}={settings_list.get(key, '')}")

    return errors, warnings


def settings_from_env() -> dict:
    settings = {key: value_for(key) for key in KEYS}
    settings["EXTRA"] = parse_extra_env(settings.pop("EXTRA_ENV"))
    settings["SECRET_NAMES"] = parse_secret_names(settings.pop("SECRETS"))
    settings["NAMESPACE"] = settings["NAMESPACE"] or settings["APP_NAME"]
    return settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure your Kubernetes deployment")
    parser.add_argument("--yes", action="store_true",
                        help="no prompts — use .env / environment values")
    parser.add_argument("--check", action="store_true",
                        help="validate configuration, make no changes (used in CI)")
    args = parser.parse_args()

    if args.check:
        settings = settings_from_env()
        explicit = {k for k in KEYS if has_value(k)}
        errors, warnings = validate(settings, check_only=True, explicit=explicit)
        for w in warnings:
            print(f"WARNING: {w}")
        if errors:
            print("\nConfiguration INVALID:")
            for e in errors:
                print(f"  - {e}")
            return 1
        print("\nConfiguration OK — ready to deploy.")
        return 0

    if args.yes:
        settings = resolve_yes()
        explicit = {k for k in KEYS if has_value(k)}
        errors, warnings = validate(settings, check_only=False, explicit=explicit)
        for w in warnings:
            print(f"WARNING: {w}")
        if errors:
            print("\nConfiguration INVALID:")
            for e in errors:
                print(f"  - {e}")
            print("Fix the values above (set them in .env or as environment "
                  "variables), then re-run.")
            return 1
    else:
        settings = run_wizard()
        errors, warnings = validate(settings, check_only=False,
                                    explicit=set(settings.keys()), wizard_mode=True)
        if errors:
            print("\nConfiguration INVALID:")
            for e in errors:
                print(f"  - {e}")
            return 1

    write_env(settings)
    update_configmap(settings.get("EXTRA", []))
    update_secret(settings.get("SECRET_NAMES", []))
    apply_file_selection(settings["INGRESS_HOST"], use_pod=False)

    print("\n==> Summary")
    for key in KEYS:
        if key in ("EXTRA_ENV", "SECRETS"):
            continue
        print(f"    {key}={settings.get(key, '')}")
    if settings.get("EXTRA"):
        print(f"    EXTRA_ENV={','.join(f'{k}={v}' for k, v in settings['EXTRA'])}")
    if settings.get("SECRET_NAMES"):
        print(f"    SECRETS={','.join(settings['SECRET_NAMES'])}")
        print("    (values come from your GitLab CI/CD variables — set them "
              "Masked + Protected)")

    print("""
==> All done! What happens next:
    1. (optional) See the exact Kubernetes manifests:      make preview
    2. Commit and push — GitLab CI builds and deploys:
         git add -A && git commit -m "Deploy my app" && git push
    3. (optional) Deploy locally instead (needs kubectl): make deploy
""")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
