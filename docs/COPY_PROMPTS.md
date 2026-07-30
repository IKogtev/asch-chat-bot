# Copy agent prompts to kb-file-ui

Use this when deploying updated prompt files from the repo into the shared KB storage used by `kb-file-ui` and `adk-agent` / `adk-web`.

## Paths

| Location | Path |
|---|---|
| Source in repo | `kb_storage/prompts/<agent>/<agent>_agent_prompt.md` |
| `kb-file-ui` container | `/var/www/filegator/repository/prompts/<agent>/<agent>_agent_prompt.md` |
| `adk-agent` / `adk-web` container | `/app/agent/prompts/<agent>/<agent>_agent_prompt.md` |

`kb-file-ui` and the agent pods share the same PVC (`kb-shared-rwx`). After copying into `/var/www/filegator/repository/prompts/`, agents pick up the files from the `prompts` subpath without a separate copy.

## Kubernetes

Set the target namespace:

```bash
# dev
export NAMESPACE=chatbot-dev

# test1
# export NAMESPACE=chatbot-test1

# prod
# export NAMESPACE=chatbot-prod
```

Resolve the `kb-file-ui` pod:

```bash
export POD="$(
  kubectl -n "$NAMESPACE" get pod \
    -l app=kb-file-ui \
    -o jsonpath='{.items[0].metadata.name}'
)"
export CONTAINER=kb-file-ui
export REMOTE_BASE=/var/www/filegator/repository/prompts
```

Create target folders in the pod:

```bash
kubectl -n "$NAMESPACE" exec "$POD" -c "$CONTAINER" -- \
  mkdir -p \
    "$REMOTE_BASE/dispatcher" \
    "$REMOTE_BASE/doc_search" \
    "$REMOTE_BASE/kb_answer" \
    "$REMOTE_BASE/owasp" \
    "$REMOTE_BASE/product_filter" \
    "$REMOTE_BASE/product_info" \
    "$REMOTE_BASE/smalltalk"
```

Copy prompt files from the repo:

```bash
kubectl -n "$NAMESPACE" cp \
  kb_storage/prompts/dispatcher/dispatcher_agent_prompt.md \
  "$POD:$REMOTE_BASE/dispatcher/dispatcher_agent_prompt.md" \
  -c "$CONTAINER"

kubectl -n "$NAMESPACE" cp \
  kb_storage/prompts/doc_search/doc_search_agent_prompt.md \
  "$POD:$REMOTE_BASE/doc_search/doc_search_agent_prompt.md" \
  -c "$CONTAINER"

kubectl -n "$NAMESPACE" cp \
  kb_storage/prompts/kb_answer/kb_answer_agent_prompt.md \
  "$POD:$REMOTE_BASE/kb_answer/kb_answer_agent_prompt.md" \
  -c "$CONTAINER"

kubectl -n "$NAMESPACE" cp \
  kb_storage/prompts/owasp/owasp_agent_prompt.md \
  "$POD:$REMOTE_BASE/owasp/owasp_agent_prompt.md" \
  -c "$CONTAINER"

kubectl -n "$NAMESPACE" cp \
  kb_storage/prompts/product_filter/product_filter_agent_prompt.md \
  "$POD:$REMOTE_BASE/product_filter/product_filter_agent_prompt.md" \
  -c "$CONTAINER"

kubectl -n "$NAMESPACE" cp \
  kb_storage/prompts/product_info/product_info_agent_prompt.md \
  "$POD:$REMOTE_BASE/product_info/product_info_agent_prompt.md" \
  -c "$CONTAINER"

kubectl -n "$NAMESPACE" cp \
  kb_storage/prompts/smalltalk/smalltalk_agent_prompt.md \
  "$POD:$REMOTE_BASE/smalltalk/smalltalk_agent_prompt.md" \
  -c "$CONTAINER"
```

### One-shot copy loop

Run from the repository root:

```bash
export NAMESPACE=chatbot-dev
export POD="$(
  kubectl -n "$NAMESPACE" get pod \
    -l app=kb-file-ui \
    -o jsonpath='{.items[0].metadata.name}'
)"
export CONTAINER=kb-file-ui
export REMOTE_BASE=/var/www/filegator/repository/prompts

for rel in \
  dispatcher/dispatcher_agent_prompt.md \
  doc_search/doc_search_agent_prompt.md \
  kb_answer/kb_answer_agent_prompt.md \
  owasp/owasp_agent_prompt.md \
  product_filter/product_filter_agent_prompt.md \
  product_info/product_info_agent_prompt.md \
  smalltalk/smalltalk_agent_prompt.md
do
  kubectl -n "$NAMESPACE" exec "$POD" -c "$CONTAINER" -- mkdir -p "$REMOTE_BASE/$(dirname "$rel")"
  kubectl -n "$NAMESPACE" cp "kb_storage/prompts/$rel" "$POD:$REMOTE_BASE/$rel" -c "$CONTAINER"
done
```

### Verify in kb-file-ui

```bash
kubectl -n "$NAMESPACE" exec "$POD" -c "$CONTAINER" -- \
  find "$REMOTE_BASE" -name '*_agent_prompt.md' | sort
```

### Verify propagation across pods

`kb-shared-rwx` is `csi-s3` with `ReadWriteMany`. All pods see the same PVC, but through different mount paths:

| Pod | Mount path | PVC path for prompts |
|---|---|---|
| `kb-file-ui` | `/var/www/filegator/repository/prompts/...` | `prompts/...` |
| `adk-agent`, `adk-web` | `/app/agent/prompts/...` | `prompts/...` (`subPath: prompts`) |
| `kb-manager` | `/app/data/kb_documents/prompts/...` | `prompts/...` |

S3-CSI can lag on cross-pod visibility, directory listings, and `mtime` updates. After copying, compare the same file from writer and reader pods.

```bash
export NAMESPACE=chatbot-dev
export REL=dispatcher/dispatcher_agent_prompt.md

export KB_UI_POD="$(
  kubectl -n "$NAMESPACE" get pod -l app=kb-file-ui -o jsonpath='{.items[0].metadata.name}'
)"
export ADK_POD="$(
  kubectl -n "$NAMESPACE" get pod -l app=adk-agent -o jsonpath='{.items[0].metadata.name}'
)"

# 1. Compare checksums
kubectl -n "$NAMESPACE" exec "$KB_UI_POD" -c kb-file-ui -- \
  sha256sum "/var/www/filegator/repository/prompts/$REL"
kubectl -n "$NAMESPACE" exec "$ADK_POD" -c adk-agent -- \
  sha256sum "/app/agent/prompts/$REL"

# 2. Compare size and mtime
kubectl -n "$NAMESPACE" exec "$KB_UI_POD" -c kb-file-ui -- \
  stat "/var/www/filegator/repository/prompts/$REL"
kubectl -n "$NAMESPACE" exec "$ADK_POD" -c adk-agent -- \
  stat "/app/agent/prompts/$REL"
```

Quick live write/read test:

```bash
MARKER="propagation-test-$(date +%s).txt"

kubectl -n "$NAMESPACE" exec "$KB_UI_POD" -c kb-file-ui -- \
  sh -c "echo '$MARKER' > /var/www/filegator/repository/prompts/$MARKER && sync"

sleep 2

kubectl -n "$NAMESPACE" exec "$ADK_POD" -c adk-agent -- \
  cat "/app/agent/prompts/$MARKER"

kubectl -n "$NAMESPACE" exec "$KB_UI_POD" -c kb-file-ui -- \
  rm -f "/var/www/filegator/repository/prompts/$MARKER"
```

Check that all PVC consumers are on the same node. `adk-agent` and `kb-manager` are pinned to the `kb-file-ui` node; others only prefer it:

```bash
kubectl -n "$NAMESPACE" get pod -o custom-columns=\
NAME:.metadata.name,\
NODE:.spec.nodeName,\
APP:.metadata.labels.app | grep -E 'kb-file-ui|adk-agent|adk-web|kb-manager|chatbot'
```

Check all pods attached to the PVC:

```bash
kubectl -n "$NAMESPACE" get pod -o json | jq -r '
  .items[]
  | select(any(.spec.volumes[]?; .persistentVolumeClaim.claimName == "kb-shared-rwx"))
  | "\(.metadata.name)\t\(.spec.nodeName)"
'
```

If hashes differ or the marker file is missing in `adk-agent`:

1. Run `sync` in `kb-file-ui` after copy.
2. Re-check after 10-30 seconds; S3-CSI metadata can be delayed.
3. Copy directly into `adk-agent` as a workaround:

```bash
kubectl -n "$NAMESPACE" cp \
  "kb_storage/prompts/$REL" \
  "$ADK_POD:/app/agent/prompts/$REL" \
  -c adk-agent
```

4. Restart `adk-agent` / `adk-web` if the file is visible but prompt reload did not happen.

`adk-agent` reloads prompts by polling `mtime` every second (`agent/prompt_loader.py`). If `mtime` does not change on the reader pod, the agent will keep the old prompt even when the file content is eventually visible.

## Docker Compose

For local `docker compose`, `./kb_storage` is mounted into `kb-file-ui` at `/var/www/filegator/repository`, so repo files under `kb_storage/prompts/` are already visible in the container. No copy step is required unless you edit prompts outside `kb_storage`.
