# Status Update — AKV Multi-Region Sync

## Completed

- All prerequisites verified (Azure CLI 2.83.0, kubectl 1.34.2, Docker 28.5.1)
- Resource groups created in West Europe (source), Sweden Central (target), and West Europe (AKS)
- Source Key Vault (`kv-akvsync-source`) created with RBAC authorization, soft delete, and purge protection
- Three demo secrets populated in source vault: `db-password`, `api-key`, `storage-account-key`
- Target Key Vault (`kv-akvsync-target-dr`) created with the same protection settings and confirmed empty
- AKS cluster created with OIDC issuer and Workload Identity enabled; kubectl access configured and OIDC issuer URL captured

## Remaining

- **Step 5:** Create the User-Assigned Managed Identity (`id-akvsync`) and configure the federated identity credential linking it to the AKS ServiceAccount
- **Step 6:** Assign `Key Vault Secrets User` on the source vault and `Key Vault Secrets Officer` on the target vault to the managed identity
- **Step 7:** Clone `mburakunuvar/akv-sync`, create ACR, build and push the container image, and attach ACR to AKS
- **Step 8:** Export `TENANT_ID`, create the three Kubernetes manifests (`namespace.yaml`, `serviceaccount.yaml`, `cronjob.yaml`), and apply them via `envsubst`
- **Step 9:** Trigger a manual sync job, verify all three secrets appear in the target vault, test a secret rotation end-to-end, and confirm the identity returns 403 when attempting to write to the source vault