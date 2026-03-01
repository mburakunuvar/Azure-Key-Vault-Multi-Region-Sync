# Status Update — AKV Multi-Region Sync

## Completed

- All prerequisites verified (Azure CLI 2.83.0, kubectl 1.34.2, Docker 28.5.1)
- Resource groups created in West Europe (source), Sweden Central (target), and West Europe (AKS)
- Source Key Vault (`kv-akvsync-source`) created with RBAC authorization, soft delete, and purge protection
- Three demo secrets populated in source vault: `db-password`, `api-key`, `storage-account-key`
- Target Key Vault (`kv-akvsync-target-dr`) created with the same protection settings and confirmed empty
- AKS cluster (`aks-akvsync`, Kubernetes 1.33.6) created with OIDC issuer and Workload Identity enabled; kubectl access configured and OIDC issuer URL captured
- User-Assigned Managed Identity (`id-akvsync`) created; `CLIENT_ID` and `PRINCIPAL_ID` captured; federated identity credential created linking the AKS OIDC issuer to `system:serviceaccount:akv-sync:akv-sync-sa`
- RBAC assignments applied: `Key Vault Secrets User` on source vault (read-only) and `Key Vault Secrets Officer` on target vault (write); confirmed no write role exists on the source vault for the managed identity

## Remaining

- **Step 7B *(optional but recommended)*:** Run the Python sync script (`akv-sync-python/akv_sync.py`) locally — install dependencies, set `SOURCE_VAULT_URL` / `TARGET_VAULT_URL`, optionally dry-run, execute with `az login` credentials, and verify all 3 secrets sync to the target vault
- **Step 8:** Build the Docker image locally, create ACR, push the image, and attach ACR to AKS
- **Step 9:** Export `TENANT_ID`, create the three Kubernetes manifests (`namespace.yaml`, `serviceaccount.yaml`, `cronjob.yaml`), and apply them via `envsubst`
- **Step 10:** Trigger a manual sync job, verify all three secrets appear in the target vault, test a secret rotation end-to-end, and confirm the identity returns 403 when attempting to write to the source vault

---

## Previously Completed (reference)

- ~~**Step 7A *(optional but recommended)*:** Clone `mburakunuvar/akv-sync`, run the bash script locally with `az login` credentials, and verify all 3 secrets sync to the target vault~~ **DONE** — cloned repo to `/workspaces/akv-sync`; wrote `local-sync-test.sh` wrapper (akv-sync's built-in workload-identity auth requires AKS pod env vars, not available locally); granted `Key Vault Secrets Officer` to local user on target vault; all 3 secrets (`api-key`, `db-password`, `storage-account-key`) synced and values verified as matching