# AKV Multi-Region Sync — Implementation Checklist

Work through this list top to bottom. Check off each item as you complete it.  
All commands are in [README.md](README.md).

---

## Prerequisites

- [ ] Azure CLI installed (`az version` → 2.50 or higher)
- [ ] kubectl installed (`kubectl version --client` → 1.27 or higher)
- [ ] Docker installed (`docker --version` → 24 or higher)
- [ ] Logged in to Azure CLI (`az login`)
- [ ] Azure subscription available and you have **Contributor** + **User Access Administrator** rights
- [ ] Registered resource providers:
  - [ ] `Microsoft.KeyVault`
  - [ ] `Microsoft.ContainerService`
  - [ ] `Microsoft.ManagedIdentity`
  - [ ] `Microsoft.ContainerRegistry`

---

## Step 0 — Environment Variables

- [ ] Edit `env.sh` — set your real `SUBSCRIPTION_ID`
- [ ] `source env.sh` and run `az account set --subscription "$SUBSCRIPTION_ID"`
- [ ] Confirm no variable is empty: `echo "$SOURCE_KV $TARGET_KV $AKS_NAME"`

---

## Step 1 — Resource Groups

- [ ] Create source resource group (`rg-akv-sync-source` in West Europe)
- [ ] Create target resource group (`rg-akv-sync-target` in Sweden Central)
- [ ] Create AKS resource group (`rg-akv-sync-aks` in West Europe)
- [ ] Verify: `az group list --output table`

---

## Step 2 — Source Key Vault

- [ ] Create source Key Vault with RBAC authorization, soft delete, and purge protection enabled
- [ ] Grant your own user account the **Key Vault Secrets Officer** role on the source vault
  - [ ] Confirm `MY_OBJECT_ID` is populated: `echo "$MY_OBJECT_ID"`
- [ ] Add the three demo secrets:
  - [ ] `db-password`
  - [ ] `api-key`
  - [ ] `storage-account-key`
- [ ] Verify secrets exist: `az keyvault secret list --vault-name "$SOURCE_KV" --output table`

---

## Step 3 — Target Key Vault

- [ ] Create target Key Vault in Sweden Central with the same protection flags
- [ ] Confirm the target vault is empty: `az keyvault secret list --vault-name "$TARGET_KV" --output table`
- [ ] **Do not** add any secrets manually — sync will own this vault

---

## Step 4 — AKS Cluster

- [ ] Create AKS cluster with `--enable-oidc-issuer` and `--enable-workload-identity`
- [ ] Run `az aks get-credentials` to configure kubectl
- [ ] Capture the OIDC issuer URL into `$OIDC_ISSUER`
- [ ] Verify: `echo "$OIDC_ISSUER"` — should print a URL ending in `/`
- [ ] Verify kubectl access: `kubectl get nodes`

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

## Step 7A — Run the Sync Bash Script Locally to test *(optional but recommended)*

> Validate the sync logic with your own Azure CLI credentials before building any container.

- [ ] Clone the sync app: `git clone https://github.com/mburakunuvar/akv-sync.git`
- [ ] Change into the `akv-sync` directory
- [ ] Inspect the code — understand what env vars or config it expects
- [ ] Set required env vars (`SOURCE_VAULT_URL`, `TARGET_VAULT_URL`) pointing at the real vaults
- [ ] Run the script locally (`local-sync-test.sh`) using `az login` credentials
- [ ] Verify all 3 secrets appear in the target vault: `az keyvault secret list --vault-name "$TARGET_KV" --output table`
- [ ] Confirm the values match the source vault

---

## Step 7B — Run the Sync Python Script Locally to test *(optional but recommended)*

> Validate the Python version of the sync logic with your own Azure CLI credentials before building the container.

- [ ] Change into the `akv-sync-python` directory
- [ ] Install Python dependencies: `pip install -r requirements.txt`
- [ ] Set required env vars (`SOURCE_VAULT_URL`, `TARGET_VAULT_URL`) pointing at the real vaults
- [ ] (Optional) Test with dry-run first: `DRY_RUN=true python akv_sync.py`
- [ ] Run the script locally: `python akv_sync.py` using `az login` credentials
- [ ] Verify all 3 secrets appear in the target vault: `az keyvault secret list --vault-name "$TARGET_KV" --output table`
- [ ] Confirm the values match the source vault

---

## Step 8 — Build and Push the Sync Container Image

- [ ] Build the Docker image locally: `docker build -t akv-sync-python:local ./akv-sync-python`
- [ ] Create the Azure Container Registry (`acrakvsync`, Basic SKU, West Europe)
- [ ] Log in to ACR: `az acr login --name "$ACR_NAME"`
- [ ] Capture `ACR_LOGIN_SERVER` into a variable
- [ ] Tag and push the image: `docker push "${ACR_LOGIN_SERVER}/akv-sync-python:latest"`
- [ ] Attach ACR to AKS: `az aks update --attach-acr "$ACR_NAME"`
- [ ] Verify image is in registry: `az acr repository list --name "$ACR_NAME" --output table`

---

## Step 9 — Deploy to AKS

- [ ] Capture `TENANT_ID` into a variable: `export TENANT_ID=$(az account show --query tenantId -o tsv)`
- [ ] Apply pre-built Python manifests from `akv-sync-python/k8s/` via `envsubst`:
  - [ ] `envsubst < namespace.yaml | kubectl apply -f -`
  - [ ] `envsubst < serviceaccount.yaml | kubectl apply -f -`
  - [ ] `envsubst < configmap.yaml | kubectl apply -f -`
  - [ ] `envsubst < cronjob.yaml | kubectl apply -f -`
- [ ] Verify CronJob created: `kubectl get cronjob -n akv-sync`

---

## Step 10 — Validate the Sync

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
- [ ] Run an in-cluster probe job using the Workload Identity to attempt a write to the **source** vault
  - [ ] Confirm the job completes with **403 Forbidden** (the identity must not have write access to source)

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
