# AKV Multi-Region Sync — Implementation Checklist

Work through this list top to bottom. Check off each item as you complete it.  
All commands are in [README.md](README.md).

---

## Prerequisites

- [x] Azure CLI installed (`az version` → 2.50 or higher) — **2.83.0**
- [x] kubectl installed (`kubectl version --client` → 1.27 or higher) — **1.34.2**
- [x] Docker installed (`docker --version` → 24 or higher) — **28.5.1**
- [x] Logged in to Azure CLI (`az login`)
- [x] Azure subscription available and you have **Contributor** + **User Access Administrator** rights
- [x] Registered resource providers:
  - [x] `Microsoft.KeyVault`
  - [x] `Microsoft.ContainerService`
  - [x] `Microsoft.ManagedIdentity`
  - [x] `Microsoft.ContainerRegistry`

---

## Step 0 — Environment Variables

- [ ] Open a terminal session you will keep open for the entire walkthrough
- [ ] Set `SUBSCRIPTION_ID` and run `az account set`
- [ ] Set all resource group, location, Key Vault, AKS, ACR, and identity variables
- [ ] Confirm no variable is empty: `echo "$SOURCE_KV $TARGET_KV $AKS_NAME"`

---

## Step 1 — Resource Groups

- [x] Create source resource group (`rg-akv-sync-source` in West Europe)
- [x] Create target resource group (`rg-akv-sync-target` in Sweden Central)
- [x] Create AKS resource group (`rg-akv-sync-aks` in West Europe)
- [x] Verify: `az group list --output table`

---

## Step 2 — Source Key Vault

- [x] Create source Key Vault with RBAC authorization, soft delete, and purge protection enabled
- [x] Grant your own user account the **Key Vault Secrets Officer** role on the source vault
  - [x] Confirm `MY_OBJECT_ID` is populated: `echo "$MY_OBJECT_ID"`
- [x] Add the three demo secrets:
  - [x] `db-password`
  - [x] `api-key`
  - [x] `storage-account-key`
- [x] Verify secrets exist: `az keyvault secret list --vault-name "$SOURCE_KV" --output table`

---

## Step 3 — Target Key Vault

- [x] Create target Key Vault (`kv-akvsync-target-dr`) in Sweden Central with the same protection flags
- [x] Confirm the target vault is empty: `az keyvault secret list --vault-name "$TARGET_KV" --output table`
- [x] **Do not** add any secrets manually — sync will own this vault

---

## Step 4 — AKS Cluster

- [x] Create AKS cluster with `--enable-oidc-issuer` and `--enable-workload-identity`
- [x] Run `az aks get-credentials` to configure kubectl
- [x] Capture the OIDC issuer URL into `$OIDC_ISSUER`
- [x] Verify: `echo "$OIDC_ISSUER"` — should print a URL ending in `/`
- [x] Verify kubectl access: `kubectl get nodes`

---

## Step 5 — Managed Identity and Federated Credential

- [ ] Create the User-Assigned Managed Identity (`id-akvsync`)
- [ ] Capture `CLIENT_ID` and `PRINCIPAL_ID` into variables
- [ ] Verify: `echo "$CLIENT_ID"` and `echo "$PRINCIPAL_ID"` — both should be non-empty GUIDs
- [ ] Create the federated identity credential linking:
  - [ ] The AKS OIDC issuer (`$OIDC_ISSUER`)
  - [ ] The Kubernetes subject `system:serviceaccount:akv-sync:akv-sync-sa`
  - [ ] Audience `api://AzureADTokenExchange`
- [ ] Verify: `az identity federated-credential list --identity-name "$IDENTITY_NAME" --resource-group "$IDENTITY_RG" --output table`

---

## Step 6 — RBAC Assignments

- [ ] Capture `SOURCE_KV_ID` and `TARGET_KV_ID` into variables
- [ ] Assign **Key Vault Secrets User** to the identity on the **source** vault (read-only)
- [ ] Assign **Key Vault Secrets Officer** to the identity on the **target** vault (write)
- [ ] Verify both assignments: `az role assignment list --assignee "$PRINCIPAL_ID" --output table`
- [ ] Confirm there is **no write role** on the source vault for this identity

---

## Step 7 — Build and Push the Sync Container Image

- [ ] Clone the sync app: `git clone https://github.com/mburakunuvar/akv-sync.git`
- [ ] Change into the `akv-sync` directory
- [ ] Create the Azure Container Registry (`acrakvsync` — must be globally unique)
- [ ] Log in to ACR: `az acr login --name "$ACR_NAME"`
- [ ] Capture `ACR_LOGIN_SERVER` into a variable
- [ ] Build the Docker image: `docker build -t "${ACR_LOGIN_SERVER}/akv-sync:latest" .`
- [ ] Push the image: `docker push "${ACR_LOGIN_SERVER}/akv-sync:latest"`
- [ ] Attach ACR to AKS: `az aks update --attach-acr "$ACR_NAME"`
- [ ] Verify image is in registry: `az acr repository list --name "$ACR_NAME" --output table`

---

## Step 8 — Deploy to AKS

- [ ] Capture `TENANT_ID` into a variable: `export TENANT_ID=$(az account show --query tenantId -o tsv)`
- [ ] Create `namespace.yaml` with the `akv-sync` namespace definition
- [ ] Create `serviceaccount.yaml` with `azure.workload.identity/client-id` and `azure.workload.identity/tenant-id` annotations
- [ ] Create `cronjob.yaml` with:
  - [ ] Schedule set to your target RPO (e.g. `*/15 * * * *`)
  - [ ] `azure.workload.identity/use: "true"` label on the pod template
  - [ ] `serviceAccountName: akv-sync-sa`
  - [ ] `SOURCE_VAULT_URL` and `TARGET_VAULT_URL` env vars set correctly
  - [ ] `concurrencyPolicy: Forbid` to prevent overlapping runs
- [ ] Apply manifests using `envsubst`:
  - [ ] `envsubst < namespace.yaml | kubectl apply -f -`
  - [ ] `envsubst < serviceaccount.yaml | kubectl apply -f -`
  - [ ] `envsubst < cronjob.yaml | kubectl apply -f -`
- [ ] Verify CronJob created: `kubectl get cronjob -n akv-sync`

---

## Step 9 — Validate the Sync

- [ ] Trigger a manual job run: `kubectl create job akv-sync-manual-run --from=cronjob/akv-sync --namespace akv-sync`
- [ ] Watch the pod reach `Completed` state: `kubectl get pods -n akv-sync --watch`
- [ ] Check logs — confirm all 3 secrets were synced: `kubectl logs -n akv-sync -l job-name=akv-sync-manual-run --tail=100`
- [ ] Verify secrets appeared in target vault:
  - [ ] `az keyvault secret list --vault-name "$TARGET_KV" --output table` — should list 3 secrets
  - [ ] `az keyvault secret show --vault-name "$TARGET_KV" --name "db-password" --query value -o tsv`

**End-to-end rotation test:**
- [ ] Rotate `db-password` in the source vault to a new value
- [ ] Trigger another manual sync job
- [ ] Confirm the new value propagated to the target vault

**RBAC boundary check:**
- [ ] Attempt to write a secret directly to the **source** vault as the managed identity
  - [ ] Confirm the command returns **403 Forbidden** (the identity must not have write access to source)

---

## Final Sign-off

Before declaring the demo complete, confirm all of the following:

- [ ] Source vault has 3 secrets
- [ ] Target vault has the same 3 secrets with matching values
- [ ] A secret rotation in the source propagates to the target after a sync run
- [ ] The managed identity cannot write to the source vault (403 confirmed)
- [ ] CronJob runs on schedule and completes without errors

---

## Cleanup (when done)

- [ ] `az group delete --name "$RG_SOURCE" --yes --no-wait`
- [ ] `az group delete --name "$RG_TARGET" --yes --no-wait`
- [ ] `az group delete --name "$AKS_RG" --yes --no-wait`
- [ ] If Key Vault purge protection was **not** enabled, purge the soft-deleted vaults to free the names
