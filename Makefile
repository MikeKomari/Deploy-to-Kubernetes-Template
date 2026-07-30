# === Variables - override via env or .env file ===
APP_NAME        ?= myapp
NAMESPACE       ?= $(APP_NAME)
ENVIRONMENT     ?= production
IMAGE           ?= $(APP_NAME):latest
REPLICAS        ?= 2
APP_PORT        ?= 3000
SERVICE_PORT    ?= 80
SERVICE_TYPE    ?= ClusterIP
INGRESS_HOST    ?= $(APP_NAME).example.com
INGRESS_CLASS   ?= nginx
INGRESS_PATH    ?= /
HEALTHCHECK_PATH ?= /health
MEMORY_REQUEST  ?= 128Mi
CPU_REQUEST     ?= 100m
MEMORY_LIMIT    ?= 256Mi
CPU_LIMIT       ?= 500m

export APP_NAME NAMESPACE ENVIRONMENT IMAGE REPLICAS APP_PORT SERVICE_PORT
export SERVICE_TYPE INGRESS_HOST INGRESS_CLASS INGRESS_PATH HEALTHCHECK_PATH
export MEMORY_REQUEST CPU_REQUEST MEMORY_LIMIT CPU_LIMIT

# === Targets ===

.PHONY: help preview deploy undeploy status logs envfile compose-up compose-preview

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

preview: ## Print rendered YAML (dry-run)
	@echo "--- Rendered Kubernetes manifests ---"
	@for f in k8s/*.yaml; do \
		echo "---"; envsubst < "$$f"; \
	done

deploy: ## Deploy to Kubernetes
	@bash scripts/deploy.sh

undeploy: ## Delete the entire namespace (tears down everything)
	@kubectl delete namespace $(NAMESPACE) --ignore-not-found

status: ## Show deployment status
	@kubectl get all -n $(NAMESPACE)

logs: ## Tail logs from running pods
	@kubectl logs -n $(NAMESPACE) -l app=$(APP_NAME) --tail=100 -f

envfile: ## Generate .env.example from defaults
	@{ \
		echo "# Kubernetes deployment configuration"; \
		echo "APP_NAME=$(APP_NAME)"; \
		echo "NAMESPACE=$(NAMESPACE)"; \
		echo "ENVIRONMENT=$(ENVIRONMENT)"; \
		echo "IMAGE=$(IMAGE)"; \
		echo "REPLICAS=$(REPLICAS)"; \
		echo "APP_PORT=$(APP_PORT)"; \
		echo "SERVICE_PORT=$(SERVICE_PORT)"; \
		echo "SERVICE_TYPE=$(SERVICE_TYPE)"; \
		echo "INGRESS_HOST=$(INGRESS_HOST)"; \
		echo "INGRESS_CLASS=$(INGRESS_CLASS)"; \
		echo "INGRESS_PATH=$(INGRESS_PATH)"; \
		echo "HEALTHCHECK_PATH=$(HEALTHCHECK_PATH)"; \
		echo "MEMORY_REQUEST=$(MEMORY_REQUEST)"; \
		echo "CPU_REQUEST=$(CPU_REQUEST)"; \
		echo "MEMORY_LIMIT=$(MEMORY_LIMIT)"; \
		echo "CPU_LIMIT=$(CPU_LIMIT)"; \
	} > .env.example
	@echo "Created .env.example"

# === Docker Compose bridge ===

compose: ## Generate K8s manifests from docker-compose.yml
	@bash scripts/compose-to-k8s.sh

compose-preview: ## Run docker-compose config (validate your compose file)
	@docker-compose config

# === Quick pod for testing ===

pod: ## Run a standalone test pod (no service, no deployment)
	APP_NAME=$(APP_NAME) IMAGE=$(IMAGE) NAMESPACE=$(NAMESPACE) \
		envsubst < k8s/pod.yaml | kubectl apply -f -
	@echo "Pod $(APP_NAME) created. Run 'make logs' to see output."
