## Plan: Python AKV sync with raw K8s manifests

The bash script reads secrets from a source Key Vault and writes them to a target vault using Azure Workload Identity. The Python version replicates that core loop using the **Azure SDK for Python** (`azure-identity` + `azure-keyvault-secrets`) instead of shelling out to `az` CLI. Deployment is via raw Kubernetes manifests (`k8s/`) instead of Helm.

**Files created in [akv-sync-python/](../../akv-sync-python/)**

| File | Purpose |
|---|---|
| `akv_sync.py` | Main sync script |
| `requirements.txt` | Python dependencies |
| `Dockerfile` | Container build (replaces existing bash-oriented one) |
| `k8s/namespace.yaml` | Namespace `akv-sync` |
| `k8s/serviceaccount.yaml` | SA with Workload Identity annotations |
| `k8s/configmap.yaml` | Non-secret config (vault URLs, flags) |
| `k8s/cronjob.yaml` | CronJob wiring SA + ConfigMap + image |

---

**Steps**

1. **`requirements.txt`** — `azure-identity>=1.15.0`, `azure-keyvault-secrets>=4.7.0` (no `az` CLI dependency)

2. **`akv_sync.py`** — env var config, then:
   - Auth: `WorkloadIdentityCredential()` — reads `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_FEDERATED_TOKEN_FILE` from the environment automatically (injected by AKS Workload Identity mutating webhook); no explicit login code needed
   - Build `SecretClient` for source and target vault URLs (`SOURCE_VAULT_URL`, `TARGET_VAULT_URL`)
   - List source secrets via `list_properties_of_secrets()` → filter by `EXCLUDE_SECRETS` (comma list) and `SYNC_DISABLED` flag
   - List target secrets into a `dict` for O(1) lookup
   - For each source secret: fetch value → compare with target value (fetched only when secret exists in target) → **create** if absent, **update** if value differs or enabled-state differs → respect `DRY_RUN`
   - Track `created`, `updated`, `skipped`, `errors` counters; print summary
   - Exit code `1` if any errors, `0` otherwise

3. **Clean env var names**: `SOURCE_VAULT_URL`, `TARGET_VAULT_URL`, `DRY_RUN` (`true`/`false`), `LOG_LEVEL`, `EXCLUDE_SECRETS` (comma-separated), `SYNC_DISABLED` (whether to sync disabled secrets)

4. **`Dockerfile`** — `FROM python:3.12-slim`, copies `requirements.txt` + `akv_sync.py`, `pip install --no-cache-dir`, `ENTRYPOINT ["python", "-u", "/app/akv_sync.py"]` — no `az` CLI, no Alpine

5. **`k8s/namespace.yaml`** — namespace `akv-sync`

6. **`k8s/serviceaccount.yaml`** — `akv-sync-sa` with annotations `azure.workload.identity/client-id: "${CLIENT_ID}"` and `azure.workload.identity/tenant-id: "${TENANT_ID}"`; label `azure.workload.identity/use: "true"`

7. **`k8s/configmap.yaml`** — `SOURCE_VAULT_URL`, `TARGET_VAULT_URL`, `DRY_RUN`, `LOG_LEVEL`, `EXCLUDE_SECRETS`, `SYNC_DISABLED`; uses `${SOURCE_KV}` / `${TARGET_KV}` placeholders for `envsubst`

8. **`k8s/cronjob.yaml`** — schedule `*/15 * * * *`, `concurrencyPolicy: Forbid`, `backoffLimit: 2`, `activeDeadlineSeconds: 600`, pod label `azure.workload.identity/use: "true"`, `serviceAccountName: akv-sync-sa`, `envFrom.configMapRef`, non-root security context (`runAsUser: 1000`, `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`)

---

**Verification**

```bash
# Build
cd akv-sync-python && docker build -t akv-sync-python:local .

# Local test (uses az login session via DefaultAzureCredential)
SOURCE_VAULT_URL=https://kv-akvsync-source.vault.azure.net \
TARGET_VAULT_URL=https://kv-akvsync-target-dr.vault.azure.net \
DRY_RUN=true python akv_sync.py

# Render and apply manifests
source env.sh
envsubst < k8s/namespace.yaml     | kubectl apply -f -
envsubst < k8s/serviceaccount.yaml | kubectl apply -f -
envsubst < k8s/configmap.yaml      | kubectl apply -f -
envsubst < k8s/cronjob.yaml        | kubectl apply -f -
kubectl create job akv-sync-test --from=cronjob/akv-sync -n akv-sync
kubectl logs -n akv-sync -l job-name=akv-sync-test
```

---

**Decisions**
- Azure SDK (`azure-identity`) vs `az` CLI: SDK is smaller, faster in a container, and handles Workload Identity natively without shell invocations
- `python:3.12-slim` base: no `az` CLI needed, much smaller than `alpine/azure-cli`
- `DefaultAzureCredential` **not** used — `WorkloadIdentityCredential` is explicit so the container fails fast with a clear error if token injection is missing, rather than falling back silently to other credential chains
