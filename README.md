# Azure Key Vault Multi-Region Sync Solution

A step-by-step walkthrough for syncing secrets from one Azure Key Vault to another across geographies — enabling cross-region disaster recovery without using Azure's native backup/restore, which is [limited to the same Azure geography](https://learn.microsoft.com/en-us/azure/key-vault/general/overview-security-worlds).

An AKS CronJob authenticates via **Microsoft Entra Workload Identity** (no stored credentials), reads secrets from a **source Key Vault**, and writes them to a **target Key Vault** in a different region.

---

## Architecture

```
┌─────────────────────────────┐
│  Source Key Vault           │
│  (West Europe)              │
└────────────┬────────────────┘
             │  read secrets
             ▼
┌─────────────────────────────┐
│  AKS CronJob                │
│  (Workload Identity)        │
└────────────┬────────────────┘
             │  write secrets
             ▼
┌─────────────────────────────┐
│  Target Key Vault           │
│  (Sweden Central)           │
└─────────────────────────────┘
```

| Characteristic        | Value                              |
|-----------------------|------------------------------------|
| Sync direction        | One-way (source → target)          |
| Consistency model     | Eventual (Cron-based RPO)          |
| Credential storage    | None — Workload Identity only      |
| Authorization model   | Azure RBAC (least-privilege)       |
| What is synced        | Secrets only (keys/certs excluded) |
| Default sync interval | Every 15 minutes (`*/15 * * * *`)  |

### How It Works at Runtime

Once deployed, Kubernetes triggers the CronJob on the configured schedule (default: **every 15 minutes**). Each cycle:

1. **Pod creation** — Kubernetes spawns a short-lived pod from the CronJob spec.
2. **Authentication** — The pod receives an Azure AD token automatically via Workload Identity (no secrets stored in the cluster).
3. **Source scan** — The Python script lists all secrets in the source Key Vault.
4. **Diff & sync** — For each secret it compares the value and enabled-state against the target vault:
   - **Missing** in target → created
   - **Different** value or state → updated
   - **Identical** → skipped (no write, no API cost)
5. **Exit** — The pod logs a summary (`Created: N | Updated: N | Skipped: N | Errors: N`) and terminates with status `Completed`.

Kubernetes retains the last 3 successful and 5 failed job records for inspection.

**Key safeguards:**

| Setting | Effect |
|---------|--------|
| `concurrencyPolicy: Forbid` | If the previous run is still in progress, the next scheduled run is skipped — no overlapping syncs |
| `activeDeadlineSeconds: 600` | A run that exceeds 10 minutes is killed to prevent hung pods |
| `backoffLimit: 2` | A failed pod retries up to 2 times before the job is marked failed |

The sync interval equals your **RPO** (Recovery Point Objective) — in the worst case, a rotated secret takes up to 15 minutes to propagate. To tighten the RPO, lower the `schedule` value (e.g. `*/5 * * * *` for 5-minute RPO).

---

## Prerequisites

| Tool          | Minimum version |
|---------------|-----------------|
| Azure CLI     | 2.50            |
| kubectl       | 1.27            |
| Docker        | 24              |

Required steps and permissions:
1. Log in to Azure CLI: `az login`
2. Confirm you have **Contributor** and **User Access Administrator** roles on the subscription

Register required resource providers (idempotent):

```bash
az provider register --namespace Microsoft.KeyVault
az provider register --namespace Microsoft.ContainerService
az provider register --namespace Microsoft.ManagedIdentity
az provider register --namespace Microsoft.ContainerRegistry
```

---

## Step 0 — Define Environment Variables

All variables are collected in [`env.sh`](env.sh) at the root of this repository. Edit the file to set your `SUBSCRIPTION_ID`, then source it at the start of every terminal session:

```bash
# 1. Set your subscription ID in env.sh, then:
source env.sh
az account set --subscription "$SUBSCRIPTION_ID"
```

`env.sh` is `.gitignore`d so your real subscription ID and resource names are never committed. Variables that depend on already-deployed resources (`OIDC_ISSUER`, `CLIENT_ID`, `PRINCIPAL_ID`, `SOURCE_KV_ID`, `TARGET_KV_ID`, `ACR_LOGIN_SERVER`, `TENANT_ID`) are populated automatically via `az` queries when you source the file — they silently return empty before those resources exist, and populate correctly once they do.

Re-source the file in any new terminal to restore the full environment:

```bash
source env.sh
```

---

## Step 1 — Create Resource Groups

```bash
az group create --name "$RG_SOURCE" --location "$LOCATION_SOURCE"
az group create --name "$RG_TARGET" --location "$LOCATION_TARGET"
az group create --name "$AKS_RG"   --location "$AKS_LOCATION"
```

> **Production note:** Placing the source and target vaults in separate resource groups makes it easier to apply independent policies and lock-down RBAC per region.

---

## Step 2 — Create the Source Key Vault

```bash
az keyvault create \
  --name "$SOURCE_KV" \
  --resource-group "$RG_SOURCE" \
  --location "$LOCATION_SOURCE" \
  --enable-rbac-authorization true \
  --enable-purge-protection true \
  --retention-days 90
# Note: --enable-soft-delete is deprecated; soft delete is enabled by default and cannot be disabled
```

Grant yourself the **Key Vault Secrets Officer** role so you can populate demo secrets:

> `MY_OBJECT_ID` is pre-populated by `env.sh`. If it is empty, re-run `source env.sh`.

```bash
az role assignment create \
  --role "Key Vault Secrets Officer" \
  --assignee-object-id "$MY_OBJECT_ID" \
  --assignee-principal-type User \
  --scope "$(az keyvault show --name "$SOURCE_KV" --query id -o tsv)"
```

Add demo secrets:

```bash
az keyvault secret set --vault-name "$SOURCE_KV" --name "db-password"     --value "S3cur3P@ssw0rd!"
az keyvault secret set --vault-name "$SOURCE_KV" --name "api-key"         --value "abcdef-1234-ghijkl-5678"
az keyvault secret set --vault-name "$SOURCE_KV" --name "storage-account-key" --value "base64encodedkeyhere=="
```

---

## Step 3 — Create the Target Key Vault

```bash
az keyvault create \
  --name "$TARGET_KV" \
  --resource-group "$RG_TARGET" \
  --location "$LOCATION_TARGET" \
  --enable-rbac-authorization true \
  --enable-purge-protection true \
  --retention-days 90
# Note: --enable-soft-delete is deprecated; soft delete is enabled by default and cannot be disabled
```

Grant yourself **Key Vault Secrets User** on the target vault so you can verify it is empty. Because the vault uses Azure RBAC authorization, `az keyvault secret list` returns 403 without a role assignment — even for the vault creator:

> `MY_OBJECT_ID` is pre-populated by `env.sh`. If it is empty, re-run `source env.sh`.

```bash
az role assignment create \
  --role "Key Vault Secrets User" \
  --assignee-object-id "$MY_OBJECT_ID" \
  --assignee-principal-type User \
  --scope "$(az keyvault show --name "$TARGET_KV" --query id -o tsv)"
```

Verify the vault is empty (the command should return no output):

```bash
az keyvault secret list --vault-name "$TARGET_KV" --output table
```

> **Important:** Do not manually create secrets in the target vault. Sync owns the target. Manual entries may be silently overwritten.

> **Why this works cross-geography:** We are calling standard Key Vault REST APIs directly — not using Azure backup/restore, which is blocked across geographies.

---

## Step 4 — Create the AKS Cluster

```bash
az aks create \
  --resource-group "$AKS_RG" \
  --name "$AKS_NAME" \
  --location "$AKS_LOCATION" \
  --node-count 1 \
  --node-vm-size Standard_D2as_v6 \
  --enable-oidc-issuer \
  --enable-workload-identity \
  --generate-ssh-keys
```

Configure `kubectl` access and capture the OIDC issuer URL:

> `OIDC_ISSUER` is pre-populated by `env.sh`. Re-run `source env.sh` after cluster creation to populate it, or run the export below.

```bash
az aks get-credentials \
  --resource-group "$AKS_RG" \
  --name "$AKS_NAME" \
  --overwrite-existing

# Already in env.sh — run manually if not yet sourced after cluster creation
export OIDC_ISSUER=$(az aks show \
  --resource-group "$AKS_RG" \
  --name "$AKS_NAME" \
  --query "oidcIssuerProfile.issuerUrl" -o tsv)

echo "OIDC Issuer: $OIDC_ISSUER"
```

---

## Step 5 — Create the Managed Identity and Federated Credential

### Create the identity

> `CLIENT_ID` and `PRINCIPAL_ID` are pre-populated by `env.sh`. Re-run `source env.sh` after identity creation to populate them, or run the exports below.

```bash
az identity create \
  --name "$IDENTITY_NAME" \
  --resource-group "$IDENTITY_RG" \
  --location "$AKS_LOCATION"

# Already in env.sh — run manually if not yet sourced after identity creation
export CLIENT_ID=$(az identity show \
  --name "$IDENTITY_NAME" \
  --resource-group "$IDENTITY_RG" \
  --query clientId -o tsv)

export PRINCIPAL_ID=$(az identity show \
  --name "$IDENTITY_NAME" \
  --resource-group "$IDENTITY_RG" \
  --query principalId -o tsv)

echo "Client ID:    $CLIENT_ID"
echo "Principal ID: $PRINCIPAL_ID"
```

### Create the federated credential

This links the AKS ServiceAccount to the Managed Identity via the OIDC issuer, enabling Workload Identity without secrets or service principal keys.

```bash
az identity federated-credential create \
  --name "akvsync-federated-cred" \
  --identity-name "$IDENTITY_NAME" \
  --resource-group "$IDENTITY_RG" \
  --issuer "$OIDC_ISSUER" \
  --subject "system:serviceaccount:${NAMESPACE}:${SA_NAME}" \
  --audiences "api://AzureADTokenExchange"
```

---

## Step 6 — Assign Least-Privilege RBAC

> `SOURCE_KV_ID` and `TARGET_KV_ID` are pre-populated by `env.sh`. Re-run `source env.sh` if empty.

```bash
# Already in env.sh — run manually if not yet sourced
export SOURCE_KV_ID=$(az keyvault show --name "$SOURCE_KV" --query id -o tsv)
export TARGET_KV_ID=$(az keyvault show --name "$TARGET_KV" --query id -o tsv)
```

| Vault      | Role                      | Reason                      |
|------------|---------------------------|-----------------------------|
| Source KV  | Key Vault Secrets User    | Read secret values only     |
| Target KV  | Key Vault Secrets Officer | Create and update secrets   |

```bash
# Read-only on source
az role assignment create \
  --role "Key Vault Secrets User" \
  --assignee-object-id "$PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --scope "$SOURCE_KV_ID"

# Write on target
az role assignment create \
  --role "Key Vault Secrets Officer" \
  --assignee-object-id "$PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --scope "$TARGET_KV_ID"
```

Verify the assignments:

```bash
az role assignment list --assignee "$PRINCIPAL_ID" --output table
```

> The identity intentionally has **no write access to the source vault** and **no read access to the target vault** beyond what `Secrets Officer` permits. This enforces strict directional isolation.

---

## Step 7A — Run the Sync Bash Script Locally to test *(optional but recommended)*

Before containerizing, validate the sync logic directly using your `az login` credentials. This isolates script bugs from identity/container issues.

Clone the sync app and inspect it:

```bash
git clone https://github.com/mburakunuvar/akv-sync.git
cd akv-sync
```

Set the required environment variables and run the script directly:

```bash
export SOURCE_VAULT_URL="https://${SOURCE_KV}.vault.azure.net"
export TARGET_VAULT_URL="https://${TARGET_KV}.vault.azure.net"

# Run using your az login credentials (not Workload Identity)
bash local-sync-test.sh
# or invoke the sync script directly:
# bash akv-sync.sh
```

Verify all 3 secrets appear in the target vault:

```bash
az keyvault secret list --vault-name "$TARGET_KV" --output table
```

Confirm the values match the source:

```bash
az keyvault secret show --vault-name "$TARGET_KV" --name "db-password" --query value -o tsv
```

> **Note:** This uses your personal CLI token which has broader permissions than the managed identity. It validates the script logic only — RBAC boundary enforcement is tested in Step 10.

---

## Step 7B — Run the Sync Python Script Locally to test *(optional but recommended)*

Validate the Python version of the sync logic using your `az login` credentials before containerizing.

Install the Python dependencies:

```bash
cd akv-sync-python
pip install -r requirements.txt
```

Set the required environment variables and run the script:

```bash
export SOURCE_VAULT_URL="https://${SOURCE_KV}.vault.azure.net"
export TARGET_VAULT_URL="https://${TARGET_KV}.vault.azure.net"

# Run using your az login credentials (DefaultAzureCredential fallback)
python akv_sync.py
```

Optionally test with dry-run first:

```bash
export DRY_RUN=true
python akv_sync.py
```

Verify all 3 secrets appear in the target vault:

```bash
az keyvault secret list --vault-name "$TARGET_KV" --output table
```

Confirm the values match the source:

```bash
az keyvault secret show --vault-name "$TARGET_KV" --name "db-password" --query value -o tsv
```

> **Note:** This uses your personal CLI token via `DefaultAzureCredential` which has broader permissions than the managed identity. It validates the script logic only — RBAC boundary enforcement is tested in Step 10.

---

## Step 8 — Build and Push the Sync Container Image

This repository contains two sync implementations — pick the one you tested in Step 7:

| Variant | Directory | Dockerfile | Image name |
|---------|-----------|------------|------------|
| **Bash** (akv-sync.sh + az CLI) | `akv-sync-bash/` | `akv-sync-bash/Dockerfile` | `akv-sync` |
| **Python** (akv_sync.py + SDK) | `akv-sync-python/` | `akv-sync-python/Dockerfile` | `akv-sync-python` |

Build and test the image locally first (example shows **Python**; substitute the path for Bash):

```bash
# Python variant
docker build -t akv-sync-python:local ./akv-sync-python

# — OR — Bash variant
# docker build -t akv-sync:local ./akv-sync-bash
```

Once the local build passes, create an Azure Container Registry and push the image:

```bash
az acr create \
  --name "$ACR_NAME" \
  --resource-group "$AKS_RG" \
  --sku Basic

az acr login --name "$ACR_NAME"

# Already in env.sh — run manually if not yet sourced after ACR creation
export ACR_LOGIN_SERVER=$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)

# Python variant
docker build -t "${ACR_LOGIN_SERVER}/akv-sync-python:latest" ./akv-sync-python
docker push "${ACR_LOGIN_SERVER}/akv-sync-python:latest"

# — OR — Bash variant
# docker build -t "${ACR_LOGIN_SERVER}/akv-sync:latest" ./akv-sync-bash
# docker push "${ACR_LOGIN_SERVER}/akv-sync:latest"
```

Attach the ACR to AKS so it can pull images without separate credentials:

```bash
az aks update \
  --name "$AKS_NAME" \
  --resource-group "$AKS_RG" \
  --attach-acr "$ACR_NAME"
```

---

## Step 9 — Deploy to AKS as a CronJob

Both sync variants ship with ready-to-use Kubernetes manifests that contain `${…}` placeholders for `envsubst`.

| Variant | Manifests directory |
|---------|--------------------|
| **Bash** | `akv-sync-bash/helm-chart/` (Helm) — or write raw manifests following the YAML below |
| **Python** | `akv-sync-python/k8s/` (raw manifests with `envsubst` placeholders) |

Ensure the required variables are set (already in `env.sh`):

```bash
# Already in env.sh — run manually if not yet sourced
export TENANT_ID=$(az account show --query tenantId -o tsv)
export ACR_LOGIN_SERVER=$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)
```

### Option A — Use the pre-built Python manifests

The `akv-sync-python/k8s/` directory contains four files: `namespace.yaml`, `serviceaccount.yaml`, `configmap.yaml`, and `cronjob.yaml`. Apply them with `envsubst`:

```bash
envsubst < akv-sync-python/k8s/namespace.yaml      | kubectl apply -f -
envsubst < akv-sync-python/k8s/serviceaccount.yaml  | kubectl apply -f -
envsubst < akv-sync-python/k8s/configmap.yaml       | kubectl apply -f -
envsubst < akv-sync-python/k8s/cronjob.yaml         | kubectl apply -f -
```

### Option B — Write inline manifests (works for either variant)

If you prefer to keep manifests self-contained, create and apply the following three files.

#### namespace.yaml

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: akv-sync
```

#### serviceaccount.yaml

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: akv-sync-sa
  namespace: akv-sync
  annotations:
    azure.workload.identity/client-id: "${CLIENT_ID}"
    azure.workload.identity/tenant-id: "${TENANT_ID}"
```

#### cronjob.yaml

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: akv-sync
  namespace: akv-sync
spec:
  schedule: "*/15 * * * *"          # every 15 minutes — adjust to your RPO
  concurrencyPolicy: Forbid          # prevent overlapping runs
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 5
  jobTemplate:
    spec:
      backoffLimit: 2
      template:
        metadata:
          labels:
            azure.workload.identity/use: "true"   # required for token injection
        spec:
          serviceAccountName: akv-sync-sa
          restartPolicy: OnFailure
          containers:
            - name: akv-sync
              image: "${ACR_LOGIN_SERVER}/akv-sync-python:latest"
              env:
                - name: SOURCE_VAULT_URL
                  value: "https://${SOURCE_KV}.vault.azure.net"
                - name: TARGET_VAULT_URL
                  value: "https://${TARGET_KV}.vault.azure.net"
                - name: LOG_LEVEL
                  value: "INFO"
              resources:
                requests:
                  cpu: "100m"
                  memory: "128Mi"
                limits:
                  cpu: "500m"
                  memory: "256Mi"
```

Apply all manifests using `envsubst` to substitute environment variables:

```bash
envsubst < namespace.yaml     | kubectl apply -f -
envsubst < serviceaccount.yaml | kubectl apply -f -
envsubst < cronjob.yaml        | kubectl apply -f -
```

Verify the CronJob was created:

```bash
kubectl get cronjob -n akv-sync
```

---

## Step 10 — Validate the Sync

### Trigger the job manually (don't wait for the next scheduled run)

```bash
kubectl create job akv-sync-manual-run \
  --from=cronjob/akv-sync \
  --namespace akv-sync
```

### Watch the job complete

```bash
kubectl get pods -n akv-sync --watch
```

### Check logs

```bash
kubectl logs -n akv-sync \
  -l job-name=akv-sync-manual-run \
  --tail=100
```

Expected output (Python variant) ends with a summary line:

```
2026-03-01 18:24:28  INFO      === Sync complete ===
2026-03-01 18:24:28  INFO      Created: 3 | Updated: 0 | Skipped: 0 | Errors: 0
```

On subsequent runs (secrets already in sync):

```
2026-03-01 18:24:28  INFO      === Sync complete ===
2026-03-01 18:24:28  INFO      Created: 0 | Updated: 0 | Skipped: 3 | Errors: 0
```

### Confirm secrets exist in the target vault

```bash
az keyvault secret list --vault-name "$TARGET_KV" --output table
az keyvault secret show --vault-name "$TARGET_KV" --name "db-password" --query value -o tsv
```

### Test a secret rotation (end-to-end DR validation)

```bash
# Rotate a secret in the source
az keyvault secret set --vault-name "$SOURCE_KV" --name "db-password" --value "N3wP@ssw0rd2026!"

# Trigger sync
kubectl create job akv-sync-rotation-test \
  --from=cronjob/akv-sync \
  --namespace akv-sync

# Wait for job to finish, then verify
az keyvault secret show --vault-name "$TARGET_KV" --name "db-password" --query value -o tsv
# Expected: N3wP@ssw0rd2026!
```

### Verify RBAC boundaries

The RBAC boundary must be tested **as the managed identity**, not with your personal CLI token. Run a probe job inside the cluster that uses Workload Identity:

```bash
kubectl create job akv-rbac-boundary-check \
  --from=cronjob/akv-sync \
  --namespace akv-sync
```

Override the entrypoint to attempt a write on the source vault. If RBAC is correct the pod completes with:

```
403 Forbidden confirmed — identity cannot write to source vault. RBAC boundary holds. ✓
```

If you only have CLI access, you can also confirm the identity has no write role on the source:

```bash
# Should show only "Key Vault Secrets User" for the source scope — no "Secrets Officer"
az role assignment list \
  --assignee "$PRINCIPAL_ID" \
  --scope "$SOURCE_KV_ID" \
  --output table
```

---

## Limitations and Design Tradeoffs

| Consideration                  | Detail                                                                 |
|--------------------------------|------------------------------------------------------------------------|
| Blast radius                   | Secrets now exist in multiple geographies                              |
| Consistency                    | Eventual — RPO equals the CronJob interval                             |
| Keys and certificates          | Not covered — require exportability checks and separate design         |
| HSM-backed replication         | Not equivalent — this does not replicate cryptographic boundaries      |
| Target overwrite               | Manual changes in the target vault will be overwritten on next sync    |
| Delete behavior                | Default is **do not delete** — orphaned secrets in target persist      |

### When to use this pattern

| Use case                                    | Suitable? |
|---------------------------------------------|-----------|
| Cross-geography disaster recovery           | ✅         |
| Regional isolation / data residency         | ✅         |
| Secrets-based configuration replication     | ✅         |
| Strong cryptographic guarantees per region  | ❌         |
| Zero-RPO requirements                       | ❌         |
| Bi-directional or multi-primary sync        | ❌         |

---

## Cleanup

When you are finished with the demo, delete all resources:

```bash
az group delete --name "$RG_SOURCE"  --yes --no-wait
az group delete --name "$RG_TARGET"  --yes --no-wait
az group delete --name "$AKS_RG"     --yes --no-wait
```

> Soft-deleted Key Vaults with purge protection enabled must wait out the retention period before the names can be reused. To purge immediately (if purge protection is **not** enabled):
>
> ```bash
> az keyvault purge --name "$SOURCE_KV" --location "$LOCATION_SOURCE"
> az keyvault purge --name "$TARGET_KV" --location "$LOCATION_TARGET"
> ```

---

## References

- [Azure Key Vault backup/restore geography limitation](https://learn.microsoft.com/en-us/azure/key-vault/general/overview-security-worlds)
- [AKS Workload Identity overview](https://learn.microsoft.com/en-us/azure/aks/workload-identity-overview)
- [Azure Key Vault RBAC guide](https://learn.microsoft.com/en-us/azure/key-vault/general/rbac-guide)
- [Reference sync implementation — mburakunuvar/akv-sync](https://github.com/mburakunuvar/akv-sync)
- [Multi-region Key Vault sync pattern (dev.to)](https://dev.to/anderson_leite/building-an-azure-key-vault-multi-region-sync-solution-3ca7)
