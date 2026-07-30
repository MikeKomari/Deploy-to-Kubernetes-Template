#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export APP_NAME="${APP_NAME:-myapp}"
export NAMESPACE="${NAMESPACE:-$APP_NAME}"
export ENVIRONMENT="${ENVIRONMENT:-production}"
export IMAGE="${IMAGE:-$CI_REGISTRY_IMAGE:$CI_COMMIT_SHORT_SHA}"
export REPLICAS="${REPLICAS:-2}"
export APP_PORT="${APP_PORT:-3000}"
export SERVICE_PORT="${SERVICE_PORT:-80}"
export SERVICE_TYPE="${SERVICE_TYPE:-ClusterIP}"
export INGRESS_HOST="${INGRESS_HOST:-$APP_NAME.example.com}"
export INGRESS_CLASS="${INGRESS_CLASS:-nginx}"
export INGRESS_PATH="${INGRESS_PATH:-/}"
export HEALTHCHECK_PATH="${HEALTHCHECK_PATH:-/health}"
export MEMORY_REQUEST="${MEMORY_REQUEST:-128Mi}"
export CPU_REQUEST="${CPU_REQUEST:-100m}"
export MEMORY_LIMIT="${MEMORY_LIMIT:-256Mi}"
export CPU_LIMIT="${CPU_LIMIT:-500m}"

echo "==> Deploying $APP_NAME to namespace $NAMESPACE ($ENVIRONMENT)"
echo "    Image: $IMAGE"

kubectl get namespace "$NAMESPACE" &>/dev/null || kubectl create namespace "$NAMESPACE"

# Apply namespace first, then everything else
for manifest in "$REPO_ROOT/k8s/namespace.yaml" "$REPO_ROOT/k8s/"*.yaml; do
    [ -f "$manifest" ] || continue
    name="$(basename "$manifest")"
    [ "$name" = "namespace.yaml" ] && continue  # already done
    # Skip kustomize and other non-K8s files
    case "$name" in
        kustomization.yaml) continue ;;
    esac
    echo "  Applying $name"
    envsubst < "$manifest" | kubectl apply -f -
done

echo "==> Done! Run: kubectl get all -n $NAMESPACE"
