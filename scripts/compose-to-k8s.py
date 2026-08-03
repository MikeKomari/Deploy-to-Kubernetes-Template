#!/usr/bin/env python3
"""Generate Kubernetes manifests from docker-compose.yml.

Cross-platform replacement for compose-to-k8s.sh. Requires PyYAML
instead of yq:

    pip install pyyaml

Usage:
    python3 scripts/compose-to-k8s.py [docker-compose.yml] [output-dir]

If no arguments given, reads docker-compose.yml from the current dir,
writes to k8s/.
"""

import argparse
import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit(
        "ERROR: 'pyyaml' is required to parse docker-compose.yml.\n"
        "  Install it: pip install pyyaml"
    )

DEFAULT_COMPOSE = "docker-compose.yml"
DEFAULT_OUT_DIR = "k8s"


_VAR_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}$")


def expand_port(text):
    """Resolve a ${VAR:-default} port string, or None if not resolvable."""
    text = text.strip()
    match = _VAR_PATTERN.match(text)
    if not match:
        return None
    value = os.environ.get(match.group(1))
    if value:
        return value
    return match.group(2)


def parse_ports(service):
    """Return list of (service_port, container_port) tuples."""
    ports = []
    for raw in service.get("ports") or []:
        if isinstance(raw, dict):
            target = raw.get("target")
            published = raw.get("published") or target
            if target is None:
                continue
            ports.append((int(published), int(target)))
            continue
        port = str(raw)
        if ":" in port:
            service_port, container_port = port.rsplit(":", 1)
        else:
            container_port = port
            service_port = None
        container_port = container_port.split("/")[0]
        if not service_port:
            service_port = container_port
        try:
            ports.append((int(service_port), int(container_port)))
        except ValueError:
            resolved = expand_port(service_port)
            if resolved is None:
                continue
            ports.append((int(resolved), int(container_port)))
    return ports


def parse_env(service):
    """Return ordered list of (key, value) pairs from environment."""
    env = service.get("environment")
    if isinstance(env, dict):
        return [(str(k), str(v) if v is not None else "") for k, v in env.items()]
    pairs = []
    for item in env or []:
        item = str(item)
        if "=" in item:
            key, _, value = item.partition("=")
        else:
            key, value = item, ""
        pairs.append((key, value))
    return pairs


def parse_healthcheck(service):
    """Return True if the service has an HTTP-style healthcheck."""
    hc = service.get("healthcheck")
    test = hc.get("test") if hc else None
    if not test:
        return False
    if isinstance(test, list):
        text = " ".join(str(t) for t in test)
    else:
        text = str(test)
    return bool(re.search(r"curl|wget|http", text, re.IGNORECASE))


def parse_volumes(service):
    """Return list of (name, mount_path) tuples (emptyDir per mount)."""
    mounts = []
    for idx, raw in enumerate(service.get("volumes") or [], start=1):
        if isinstance(raw, dict):
            source = raw.get("source", "")
            target = raw.get("target", "")
        else:
            parts = str(raw).split(":")
            source = parts[0] if len(parts) > 0 else ""
            target = parts[1] if len(parts) > 1 else ""
        mounts.append((f"vol-{idx}", target or source or f"vol-{idx}"))
    return mounts


def parse_command(service):
    cmd = service.get("command")
    if cmd is None:
        return None
    if isinstance(cmd, str):
        return [cmd]
    return [str(c) for c in cmd]


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Kubernetes manifests from docker-compose.yml")
    parser.add_argument("compose_file", nargs="?", default=DEFAULT_COMPOSE)
    parser.add_argument("out_dir", nargs="?", default=DEFAULT_OUT_DIR)
    args = parser.parse_args()

    compose_file = Path(args.compose_file)
    out_dir = Path(args.out_dir)

    if not compose_file.is_file():
        print(f"ERROR: {compose_file} not found.")
        return 1

    try:
        data = yaml.safe_load(compose_file.read_text()) or {}
    except yaml.YAMLError as exc:
        print(f"ERROR: failed to parse {compose_file}: {exc}")
        return 1

    services = data.get("services")
    if not services:
        print(f"ERROR: No services found in {compose_file}")
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "namespace.yaml").write_text(
        "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: ${NAMESPACE}\n"
    )
    print(f"  Created {out_dir / 'namespace.yaml'}")

    app_name = os.environ.get("APP_NAME")

    for name, service in services.items():
        print(f"==> Converting service: {name}")
        svc = service or {}

        image = svc.get("image") or app_name or name
        ports = parse_ports(svc)
        env_pairs = parse_env(svc)
        healthcheck = parse_healthcheck(svc)
        volumes = parse_volumes(svc)
        command = parse_command(svc)

        config_name = f"{app_name or name}-config"

        if env_pairs:
            lines = ["apiVersion: v1", "kind: ConfigMap", "metadata:",
                     f"  name: {config_name}",
                     "  namespace: ${NAMESPACE}",
                     "data:"]
            lines += [f"  {key}: \"{value}\"" for key, value in env_pairs]
            (out_dir / "configmap.yaml").write_text("\n".join(lines) + "\n")
            print(f"  Created {out_dir / 'configmap.yaml'}")

        deploy = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": name,
                "namespace": "${NAMESPACE}",
                "labels": {"app": name, "env": "${ENVIRONMENT}"},
            },
            "spec": {
                "replicas": int(os.environ.get("REPLICAS") or 1),
                "selector": {"matchLabels": {"app": name}},
                "template": {
                    "metadata": {"labels": {"app": name, "env": "${ENVIRONMENT}"}},
                    "spec": {
                        "containers": [
                            {
                                "name": name,
                                "image": image,
                                "imagePullPolicy": "IfNotPresent",
                            }
                        ]
                    },
                },
            },
        }
        container = deploy["spec"]["template"]["spec"]["containers"][0]
        if ports:
            container["ports"] = [{"containerPort": cp} for _, cp in ports]
        if env_pairs:
            container["env"] = [
                {"name": key, "value": value} for key, value in env_pairs
            ]
            container["envFrom"] = [{"configMapRef": {"name": config_name}}]
        if command:
            container["command"] = command
        if healthcheck:
            container["livenessProbe"] = {
                "httpGet": {"path": "/api/health", "port": 3000},
                "initialDelaySeconds": 60,
            }
        if volumes:
            container["volumeMounts"] = [
                {"name": vname, "mountPath": mpath} for vname, mpath in volumes
            ]
            deploy["spec"]["template"]["spec"]["volumes"] = [
                {"name": vname, "emptyDir": {}} for vname, _ in volumes
            ]

        deploy_file = out_dir / f"deployment-{name}.yaml"
        deploy_file.write_text(yaml.dump(deploy, sort_keys=False))
        print(f"  Created {deploy_file}")

        if ports:
            svc_manifest = {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {
                    "name": name,
                    "namespace": "${NAMESPACE}",
                    "labels": {"app": name, "env": "${ENVIRONMENT}"},
                },
                "spec": {
                    "type": "ClusterIP",
                    "selector": {"app": name},
                    "ports": [
                        {"protocol": "TCP", "port": sp, "targetPort": cp}
                        for sp, cp in ports
                    ],
                },
            }
            svc_file = out_dir / f"service-{name}.yaml"
            svc_file.write_text(yaml.dump(svc_manifest, sort_keys=False))
            print(f"  Created {svc_file}")

    print("")
    print("Done! Manifests written to " + str(out_dir) + "/")
    print("Review them, then run: make deploy")
    print(f"Tip: Delete any file you don't need (e.g., rm {out_dir / 'configmap.yaml'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
