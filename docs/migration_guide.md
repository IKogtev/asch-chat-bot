# Migration Guide: chatbot-test1 → cloud-postgres

## Overview

Migrate the `chatbot-test1` namespace from its local PostgreSQL pod (`chatbot-postgres`) to the shared `cloud-postgres` cluster (`cloud-postgres-cluster-rw.cloud-postgres.svc.cluster.local:5432`).

**User/Password/Port remain unchanged** — only the host changes.

---

## Database Details

| | Source | Target |
|---|---|---|
| **Namespace** | `chatbot-test1` | `cloud-postgres` |
| **Service** | `chatbot-postgres:5432` | `cloud-postgres-cluster-rw.cloud-postgres.svc.cluster.local:5432` |
| **Database** | `aszh-bot` | `aszh-bot` |
| **User** | `aszh-bot` | `aszh-bot` |
| **Password** | same | same |

---

## Pre-Migration: Dump & Restore

```bash
# Dump from source
kubectl port-forward -n chatbot-test1 pod/$(kubectl get pods -n chatbot-test1 -o name | grep chatbot-postgres | sed 's|pod/||') 5432:5432 &
PGPASSWORD=aszh-bot pg_dump -Fc -f aszh-bot.dump -h 127.0.0.1 -p 5432 -U aszh-bot -d aszh-bot
kill %1

# Restore to target
kubectl port-forward -n cloud-postgres svc/cloud-postgres-cluster-rw 5433:5432 &
PGPASSWORD=aszh-bot pg_restore -Fc -h 127.0.0.1 -p 5433 -U aszh-bot -d aszh-bot aszh-bot.dump
kill %1
```

See `scripts/pg_dump.sh` for an automated script.

---

## What Needs to Change

| # | Resource | Repo | Key | Old → New |
|---|---|---|---|---|
| 1 | `configmap/chatbot-postgres-config` | `asch-chat-bot` overlay | `POSTGRES_URL` | `chatbot-postgres` → `cloud-postgres-cluster-rw.cloud-postgres.svc.cluster.local` |
| 2 | `secret/chatbot-postgres-secrets` | `sandbox-2-k8s-secrets` | `database-url` | host → cloud-postgres (see below) |
| 3 | `secret/adk-agent-secrets` | `sandbox-2-k8s-secrets` | `database-url` | host → cloud-postgres |
| 4 | `secret/kb-manager-secrets` | `sandbox-2-k8s-secrets` | `database-url` | host → cloud-postgres |
| 5 | `secret/chatbot-pgadmin-servers-secrets` | `sandbox-2-k8s-secrets` | `pgadmin_servers.json` | `Host` → cloud-postgres |

Secrets live in `it.infra/yandex.infra/sandbox-2-k8s-secrets/namespaces/chatbot-test1/secrets/`.

### Affected Deployments

- `chatbot` — reads `POSTGRES_*` from configmap, builds `DATABASE_URL`
- `adk-agent` — reads `DATABASE_URL` from `adk-agent-secrets`
- `kb-manager` — reads `DATABASE_URL` from `kb-manager-secrets`
- `chatbot-pgadmin` — reads pgadmin server config

---

## Step-by-Step Migration

Apply **both** repos (secrets first or together), then restart workloads:

1. Sync / apply `sandbox-2-k8s-secrets` for namespace `chatbot-test1`
2. Sync ArgoCD app `chatbot-yc-sandbox-2-chatbot-test1` (overlay `yc-sandbox-2-chatbot-test1`)
3. Restart: `chatbot`, `adk-agent`, `kb-manager`, `chatbot-pgadmin`

### 1. Patch ConfigMap (or Git overlay)

```bash
kubectl patch configmap chatbot-postgres-config -n chatbot-test1 --type merge \
  -p '{"data":{"POSTGRES_URL":"cloud-postgres-cluster-rw.cloud-postgres.svc.cluster.local"}}'
```

### 2. Patch Secrets (database-url for chatbot, adk-agent, kb-manager)

```bash
NEW_DB_URL=$(echo -n 'postgresql://aszh-bot:aszh-bot@cloud-postgres-cluster-rw.cloud-postgres.svc.cluster.local:5432/aszh-bot' | base64)

kubectl patch secret chatbot-postgres-secrets -n chatbot-test1 \
  -p "{\"data\":{\"database-url\":\"${NEW_DB_URL}\"}}"

kubectl patch secret adk-agent-secrets -n chatbot-test1 \
  -p "{\"data\":{\"database-url\":\"${NEW_DB_URL}\"}}"

kubectl patch secret kb-manager-secrets -n chatbot-test1 \
  -p "{\"data\":{\"database-url\":\"${NEW_DB_URL}\"}}"
```

### 3. Patch pgadmin Servers Config

```bash
NEW_PGAJSON=$(echo -n '{"Servers":{"1":{"Name":"aszh-bot","Group":"Servers","Host":"cloud-postgres-cluster-rw.cloud-postgres.svc.cluster.local","Port":5432,"MaintenanceDB":"aszh-bot","Username":"aszh-bot","Password":"aszh-bot","SSLMode":"prefer","ConnectNow":true}}}' | base64)

kubectl patch secret chatbot-pgadmin-servers-secrets -n chatbot-test1 \
  -p "{\"data\":{\"pgadmin_servers.json\":\"${NEW_PGAJSON}\"}}"
```

### 4. Restart Affected Deployments

```bash
kubectl rollout restart deployment/chatbot -n chatbot-test1
kubectl rollout restart deployment/adk-agent -n chatbot-test1
kubectl rollout restart deployment/kb-manager -n chatbot-test1
kubectl rollout restart deployment/chatbot-pgadmin -n chatbot-test1
```

### 5. Verify Everything Works

```bash
# Check pods are running
kubectl get pods -n chatbot-test1

# Check logs for connection errors
kubectl logs -n chatbot-test1 deploy/chatbot --tail=50
kubectl logs -n chatbot-test1 deploy/adk-agent --tail=50
kubectl logs -n chatbot-test1 deploy/kb-manager --tail=50
```

### 6. Network Policies (Git / ArgoCD)

The overlay adds egress policies so `chatbot`, `adk-agent`, `kb-manager`, and `chatbot-pgadmin` can reach the `cloud-postgres` namespace on port 5432. Apply via ArgoCD sync or:

```bash
kubectl apply -k deployment/kubernetes/overlays/yc-sandbox-2-chatbot-test1 --dry-run=client -o yaml | kubectl apply -f -
```

### 7. Scale Down Old PostgreSQL (Optional)

Only after confirming everything works:

```bash
kubectl scale deployment chatbot-postgres -n chatbot-test1 --replicas=0
```

Or enable `patch/patch-scale-chatbot-postgres-zero.yaml` in the overlay `kustomization.yaml`.

---

## Rollback

```bash
# 1. Scale up old postgres
kubectl scale deployment chatbot-postgres -n chatbot-test1 --replicas=1

# 2. Restore configmap
kubectl patch configmap chatbot-postgres-config -n chatbot-test1 --type merge \
  -p '{"data":{"POSTGRES_URL":"chatbot-postgres"}}'

# 3. Restore secrets
OLD_DB_URL=$(echo -n 'postgresql://aszh-bot:aszh-bot@chatbot-postgres:5432/aszh-bot' | base64)

kubectl patch secret chatbot-postgres-secrets -n chatbot-test1 \
  -p "{\"data\":{\"database-url\":\"${OLD_DB_URL}\"}}"

kubectl patch secret adk-agent-secrets -n chatbot-test1 \
  -p "{\"data\":{\"database-url\":\"${OLD_DB_URL}\"}}"

kubectl patch secret kb-manager-secrets -n chatbot-test1 \
  -p "{\"data\":{\"database-url\":\"${OLD_DB_URL}\"}}"

OLD_PGAJSON=$(echo -n '{"Servers":{"1":{"Name":"aszh-bot","Group":"Servers","Host":"chatbot-postgres","Port":5432,"MaintenanceDB":"aszh-bot","Username":"aszh-bot","Password":"aszh-bot","SSLMode":"prefer","ConnectNow":true}}}' | base64)
kubectl patch secret chatbot-pgadmin-servers-secrets -n chatbot-test1 \
  -p "{\"data\":{\"pgadmin_servers.json\":\"${OLD_PGAJSON}\"}}"

# 4. Restart deployments
kubectl rollout restart deployment/chatbot deployment/adk-agent deployment/kb-manager deployment/chatbot-pgadmin -n chatbot-test1
```

---

## Alternative: Zero-Config Switch (Service Alias)

Instead of patching every secret/configmap, re-point the existing `chatbot-postgres` service to the new PostgreSQL's ClusterIP. Lower effort but more fragile (ClusterIPs can change).

```bash
# Get the ClusterIP of the new postgres service
NEW_IP=$(kubectl get svc cloud-postgres-cluster-rw -n cloud-postgres -o jsonpath='{.spec.clusterIP}')

# Scale down old postgres so service has no endpoints
kubectl scale deployment chatbot-postgres -n chatbot-test1 --replicas=0

# Re-IP the old service to point to new postgres
kubectl patch service chatbot-postgres -n chatbot-test1 \
  -p "{\"spec\":{\"clusterIP\":\"${NEW_IP}\"}}"
```

**Rollback:**

```bash
kubectl scale deployment chatbot-postgres -n chatbot-test1 --replicas=1
kubectl patch service chatbot-postgres -n chatbot-test1 -p '{"spec":{"clusterIP":"None"}}'
kubectl patch service chatbot-postgres -n chatbot-test1 -p '{"spec":{"clusterIP":null}}'
```

---

## Notes

- This environment is managed by ArgoCD (`chatbot-yc-sandbox-2-chatbot-test1`). **Do not rely on `kubectl patch`** — update the overlay in `asch-chat-bot` and secrets in `sandbox-2-k8s-secrets`, then sync.
- Always verify connectivity after migration with `kubectl exec` before scaling down the old database.
