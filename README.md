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

## Step 7 — Run the Sync Script Locally *(optional but recommended)*

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
# Python example:
python main.py
# or Go example:
# go run .
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

## Step 8 — Build and Push the Sync Container Image

The sync application used in this walkthrough is [mburakunuvar/akv-sync](https://github.com/mburakunuvar/akv-sync).

Build and test the image locally first:

```bash
docker build -t akv-sync:local .
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

docker build -t "${ACR_LOGIN_SERVER}/akv-sync:latest" .
docker push "${ACR_LOGIN_SERVER}/akv-sync:latest"
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

Apply the following three manifests. Replace the placeholder values with your actual `$CLIENT_ID`, `$TENANT_ID`, `$SOURCE_KV` and `$TARGET_KV` before applying, or use `envsubst` as shown below.

```bash
# Already in env.sh — run manually if not yet sourced
export TENANT_ID=$(az account show --query tenantId -o tsv)
```

### namespace.yaml

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: akv-sync
```

### serviceaccount.yaml

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

### cronjob.yaml

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
              image: "${ACR_LOGIN_SERVER}/akv-sync:latest"
              env:
                - name: SOURCE_VAULT_URL
                  value: "https://${SOURCE_KV}.vault.azure.net"
                - name: TARGET_VAULT_URL
                  value: "https://${TARGET_KV}.vault.azure.net"
                - name: LOG_LEVEL
                  value: "info"
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

Expected output should show something like:

```
INFO  listing secrets from source vault: https://kv-akvsync-source.vault.azure.net
INFO  found 3 secret(s)
INFO  [db-password]         → synced (created in target)
INFO  [api-key]             → synced (created in target)
INFO  [storage-account-key] → synced (created in target)
INFO  sync complete. 3 synced, 0 skipped, 0 errors
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

```bash
# This should FAIL — identity must not write to source
az keyvault secret set \
  --vault-name "$SOURCE_KV" \
  --name "injected-secret" \
  --value "should-not-work"
# Expected: (403) Forbidden
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
