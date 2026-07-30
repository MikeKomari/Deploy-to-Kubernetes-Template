#!/usr/bin/env bash
# compose-to-k8s.sh — Generate Kubernetes manifests from docker-compose.yml
#
# Usage:
#   bash scripts/compose-to-k8s.sh [docker-compose.yml] [output-dir]
#
#   If no arguments given, reads docker-compose.yml from current dir,
#   writes to k8s/ directory.

set -euo pipefail

COMPOSE_FILE="${1:-docker-compose.yml}"
OUT_DIR="${2:-k8s}"

if ! command -v yq &>/dev/null; then
  echo "ERROR: 'yq' is required to parse docker-compose.yml."
  echo "  Install it: pip install yq  (or: snap install yq)"
  echo "  Or use Docker: docker run --rm -v \"$PWD:$PWD\" mikefarah/yq eval ..."
  exit 1
fi

if [ ! -f "$COMPOSE_FILE" ]; then
  echo "ERROR: $COMPOSE_FILE not found."
  exit 1
fi

# Detect yq version (Go yq vs Python yq)
if yq --version 2>/dev/null | grep -qi "mikefarah"; then
  YQ_GO=true
else
  YQ_GO=false
fi

yq_eval() {
  if [ "$YQ_GO" = true ]; then
    yq eval "$1" "$COMPOSE_FILE"
  else
    yq r "$COMPOSE_FILE" "$1"
  fi
}

SERVICES=$(yq_eval ".services | keys | .[]" 2>/dev/null || yq_eval "services" 2>/dev/null | head -1)

if [ -z "$SERVICES" ]; then
  echo "ERROR: No services found in $COMPOSE_FILE"
  exit 1
fi

mkdir -p "$OUT_DIR"

# ---- Namespace ----
cat > "$OUT_DIR/namespace.yaml" <<'EOF'
apiVersion: v1
kind: Namespace
metadata:
  name: ${NAMESPACE}
EOF
echo "  Created $OUT_DIR/namespace.yaml"

# ---- Process each service ----
for service in $SERVICES; do
  echo "==> Converting service: $service"
  svc_name="$service"

  # --- Image ---
  image=$(yq_eval ".services.$svc_name.image" 2>/dev/null || echo "")

  # --- Ports ---
  ports_str=""
  svc_ports=""
  port_count=0
  while IFS= read -r port; do
    [ -z "$port" ] && continue
    port_count=$((port_count + 1))
    container_port=$(echo "$port" | sed 's/.*://' | sed 's/\/.*//')
    service_port=$(echo "$port" | sed 's/:.*//' | sed 's/\/.*//')
    [ -z "$service_port" ] && service_port="$container_port"
    ports_str="${ports_str}            - containerPort: $container_port
"
    svc_ports="${svc_ports}    - protocol: TCP
      port: $service_port
      targetPort: $container_port
"
  done < <(yq_eval ".services.$svc_name.ports[]" 2>/dev/null || echo "")

  # --- Environment ---
  env_vars=""
  while IFS= read -r env; do
    [ -z "$env" ] && continue
    key="${env%%=*}"
    val="${env#*=}"
    env_vars="${env_vars}            - name: $key
              value: \"$val\"
"
  done < <(yq_eval ".services.$svc_name.environment[]" 2>/dev/null || echo "")

  # --- Healthcheck ---
  hc_test=$(yq_eval ".services.$svc_name.healthcheck.test" 2>/dev/null || echo "")
  hc_http=""
  if echo "$hc_test" | grep -qi "curl\|wget\|http"; then
    hc_path=$(echo "$hc_test" | grep -oP 'http[s]?://[^/]+(\K/[^\s]*)' || echo "/")
    hc_http="          httpGet:
            path: $hc_path
            port: $([ "$port_count" -gt 0 ] && echo "$container_port" || echo "8080")
          initialDelaySeconds: 10
          periodSeconds: 30"
  fi

  # --- Volumes ---
  vol_mounts=""
  vol_count=0
  while IFS= read -r vol; do
    [ -z "$vol" ] && continue
    vol_count=$((vol_count + 1))
    src=$(echo "$vol" | cut -d: -f1)
    dst=$(echo "$vol" | cut -d: -f2)
    vol_mounts="${vol_mounts}            - name: vol-$vol_count
              mountPath: $dst
"
  done < <(yq_eval ".services.$svc_name.volumes[]" 2>/dev/null || echo "")

  # --- Command ---
  cmd=$(yq_eval ".services.$svc_name.command" 2>/dev/null || echo "")
  cmd_block=""
  if [ -n "$cmd" ]; then
    cmd_block="          command: [$cmd]"
  fi

  # --- ConfigMap env (only if env vars exist) ---
  if [ -n "$env_vars" ]; then
    cat > "$OUT_DIR/configmap.yaml" <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: ${APP_NAME:-$svc_name}-config
  namespace: \${NAMESPACE}
data:
EOF
    while IFS= read -r env; do
      [ -z "$env" ] && continue
      echo "$env" | sed 's/^/  /' >> "$OUT_DIR/configmap.yaml"
    done < <(yq_eval ".services.$svc_name.environment[]" 2>/dev/null || echo "")
    echo "  Created $OUT_DIR/configmap.yaml"
  fi

  # --- Deployment ---
  deploy_file="$OUT_DIR/deployment-$svc_name.yaml"
  cat > "$deploy_file" <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: $svc_name
  namespace: \${NAMESPACE}
  labels:
    app: $svc_name
    env: \${ENVIRONMENT}
spec:
  replicas: \${REPLICAS:-1}
  selector:
    matchLabels:
      app: $svc_name
  template:
    metadata:
      labels:
        app: $svc_name
        env: \${ENVIRONMENT}
    spec:
      containers:
        - name: $svc_name
          image: ${image:-${APP_NAME:-$svc_name}}
          imagePullPolicy: IfNotPresent
EOF

  [ -n "$ports_str" ] && cat >> "$deploy_file" <<EOF
          ports:
$ports_str
EOF

  [ -n "$env_vars" ] && cat >> "$deploy_file" <<EOF
          env:
$env_vars
EOF

  [ -n "$env_vars" ] && cat >> "$deploy_file" <<EOF
          envFrom:
            - configMapRef:
                name: ${APP_NAME:-$svc_name}-config
EOF

  [ -n "$cmd_block" ] && echo "$cmd_block" >> "$deploy_file"

  if [ -n "$hc_http" ]; then
    cat >> "$deploy_file" <<EOF
          livenessProbe:
$hc_http
          readinessProbe:
$hc_http
EOF
  fi

  if [ -n "$vol_mounts" ]; then
    cat >> "$deploy_file" <<EOF
          volumeMounts:
$vol_mounts
      volumes:
EOF
    # Simple emptyDir per mount for now
    for i in $(seq 1 $vol_count); do
      echo "        - name: vol-$i
          emptyDir: {}" >> "$deploy_file"
    done
  fi

  echo "  Created $deploy_file"

  # --- Service ---
  if [ -n "$svc_ports" ]; then
    svc_file="$OUT_DIR/service-$svc_name.yaml"
    cat > "$svc_file" <<EOF
apiVersion: v1
kind: Service
metadata:
  name: $svc_name
  namespace: \${NAMESPACE}
  labels:
    app: $svc_name
    env: \${ENVIRONMENT}
spec:
  type: ClusterIP
  selector:
    app: $svc_name
  ports:
$svc_ports
EOF
    echo "  Created $svc_file"
  fi

done

echo ""
echo "Done! Manifests written to $OUT_DIR/"
echo "Review them, then run: make deploy"
echo "Tip: Delete any file you don't need (e.g., rm $OUT_DIR/configmap.yaml)"
