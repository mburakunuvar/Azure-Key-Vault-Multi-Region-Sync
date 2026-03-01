# Status Update — AKV Multi-Region Sync

*Last verified: 2026-03-01 — all Azure resources confirmed via CLI*

## Completed

- All prerequisites verified (Azure CLI 2.83.0, kubectl 1.34.2, Docker 28.5.1)
- Resource groups created: `rg-akv-sync-source` (West Europe), `rg-akv-sync-target` (Sweden Central), `rg-akv-sync-aks` (West Europe) — **verified**
- Source Key Vault (`kv-akvsync-source`) created with RBAC authorization, soft delete, and purge protection — **verified: 3 secrets present**
- Three demo secrets populated in source vault: `db-password`, `api-key`, `storage-account-key` — **verified**
- Target Key Vault (`kv-akvsync-target-dr`) created with the same protection settings — **verified: 3 synced secrets present**
- AKS cluster (`aks-akvsync`, Kubernetes 1.33.6) created with OIDC issuer and Workload Identity enabled; kubectl access configured, 1 node `Ready` — **verified**
- User-Assigned Managed Identity (`id-akvsync`) created; `CLIENT_ID` (`8bf8a584-…`) and `PRINCIPAL_ID` (`aae111b0-…`) captured; federated identity credential (`akvsync-federated-cred`) linking AKS OIDC issuer to `system:serviceaccount:akv-sync:akv-sync-sa` — **verified**
- RBAC assignments applied: `Key Vault Secrets User` on source vault (read-only) and `Key Vault Secrets Officer` on target vault (write); confirmed no write role exists on the source vault for the managed identity — **verified via `az role assignment list --all`**
- Local bash sync test (Step 7A) completed: all 3 secrets synced from source to target and values verified
- Local Python sync test (Step 7B) completed: `azure-identity` + `azure-keyvault-secrets` installed; dry-run authenticated via `AzureCliCredential`; real sync ran clean (`Skipped: 3, Errors: 0`); all 3 secrets confirmed in target vault with matching values
- Root README.md updated: Steps 8–9 now document both **bash** and **Python** build paths and reference the pre-built `akv-sync-python/k8s/` manifests
- Docker image built (Python variant, `akv-sync-python:local`); ACR created (`acrakvsync.azurecr.io`, Basic SKU); image pushed as `acrakvsync.azurecr.io/akv-sync-python:latest`; ACR attached to AKS — **verified via `az acr repository list`**
- Documentation updated: `README.md` and `draft-planning.md` now include a "How It Works at Runtime" section explaining the CronJob lifecycle (pod creation → Workload Identity auth → diff & sync → exit), safeguards (`concurrencyPolicy: Forbid`, `activeDeadlineSeconds: 600`, `backoffLimit: 2`), and RPO relationship to the 15-minute default schedule

## Remaining

- **Step 9:** Deploy to AKS — apply k8s manifests (`namespace`, `serviceaccount`, `configmap`/env vars, `cronjob`) via `envsubst`; Python variant has pre-built manifests in `akv-sync-python/k8s/`
- **Step 10:** Trigger a manual sync job, verify all three secrets appear in the target vault, test a secret rotation end-to-end, and confirm the managed identity returns 403 when attempting to write to the source vault

---

## Previously Completed (reference)

- ~~**Step 7B *(optional but recommended)*:** Run the Python sync script (`akv-sync-python/akv_sync.py`) locally — install dependencies, set `SOURCE_VAULT_URL` / `TARGET_VAULT_URL`, optionally dry-run, execute with `az login` credentials, and verify all 3 secrets sync to the target vault~~ **DONE** — installed `azure-identity` and `azure-keyvault-secrets` into venv; dry-run authenticated via `AzureCliCredential` and reported `Skipped: 3, Errors: 0`; real sync also completed clean (`Created: 0 | Updated: 0 | Skipped: 3 | Errors: 0`); all 3 secrets present in target vault with matching values (all were already in sync from Step 7A)
- ~~**Step 7A *(optional but recommended)*:** Clone `mburakunuvar/akv-sync`, run the bash script locally with `az login` credentials, and verify all 3 secrets sync to the target vault~~ **DONE** — cloned repo to `/workspaces/akv-sync`; wrote `local-sync-test.sh` wrapper (akv-sync's built-in workload-identity auth requires AKS pod env vars, not available locally); granted `Key Vault Secrets Officer` to local user on target vault; all 3 secrets (`api-key`, `db-password`, `storage-account-key`) synced and values verified as matching