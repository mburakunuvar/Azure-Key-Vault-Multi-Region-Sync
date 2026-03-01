# Building an Azure Key Vault Multi-Region Sync Solution

## Why this exists
Azure Key Vault **backup/restore** only works when the source and target vaults are in the **same subscription and the same Azure geography**.  
This makes native backup/restore **unsuitable for cross-geography disaster recovery**.

This repository documents a **custom multi-region sync approach** that:
- Reads secrets from a **source Key Vault** in Region A
- Writes them to a **target Key Vault** in Region B
- Runs on a schedule from **AKS** using **Microsoft Entra Workload Identity**
- Avoids Key Vault backup/restore entirely by using standard Key Vault APIs

This pattern is inspired by the AKV sync approach described here:  
https://dev.to/anderson_leite/building-an-azure-key-vault-multi-region-sync-solution-3ca7

---

## High-Level Architecture

Source Key Vault (Region A)
|
| (read secrets)
v
AKS CronJob (sync logic, workload identity)
|
| (write secrets)
v
Target Key Vault (Region B)


**Key characteristics**
- One-way sync (source → target)
- Eventual consistency (Cron-based RPO)
- No credentials stored in Kubernetes
- Explicit least-privilege RBAC

---

## Prerequisites

- Azure subscription
- Azure CLI
- kubectl
- Container registry (ACR or equivalent)
- Permissions to create:
  - Azure Key Vault
  - AKS
  - Managed Identities
  - Role assignments
- Decision made on:
  - What to sync: **secrets only** (recommended)
  - Sync direction: **one-way**
  - Sync frequency (RPO)

---

## Step 1 — Create the Source Key Vault (example: West Europe)

1. Create a Key Vault in **West Europe**
2. Enable:
   - Soft delete
   - Purge protection
3. Choose **Azure RBAC authorization model**
4. Populate the vault with secrets to be replicated

---

## Step 2 — Create the Target Key Vault (example: Sweden Central)

1. Create a Key Vault in **Sweden Central**
2. Enable:
   - Soft delete
   - Purge protection
3. Use the same naming scheme as the source vault or apply a suffix (e.g. `-dr`)
4. Do **not** manually create secrets (let sync own the target)

> This works across geographies because we are **not using backup/restore**.

---

## Step 3 — Create a Small AKS Cluster

1. Deploy a minimal AKS cluster (system node pool is sufficient)
2. Enable:
   - OIDC issuer
   - Microsoft Entra Workload Identity
3. Configure kubectl access

Reason:
- Workload Identity allows pods to authenticate to Azure without secrets or service principals.

---

## Step 4 — Create Identity and Assign Least-Privilege Access

### Identity
1. Create a **User-Assigned Managed Identity**
2. Configure a **federated identity credential** linking:
   - AKS ServiceAccount
   - AKS OIDC issuer
   - Managed Identity client ID

### RBAC assignments

| Vault        | Role                         | Purpose |
|-------------|------------------------------|---------|
| Source KV   | Key Vault Secrets User       | Read secrets |
| Target KV   | Key Vault Secrets Officer    | Write secrets |

This enforces strict read/write separation.

---

## Step 5 — Build the Sync Logic

Use this repository as a reference:  
https://github.com/mburakunuvar/akv-sync

### Minimum requirements
Your sync logic must:
1. Authenticate using Managed Identity
2. List secrets in the source vault
3. For each secret:
   - Read the latest value
   - Compare with target vault
   - Create or update if missing or different
4. Be idempotent (safe to run repeatedly)
5. Log clear results

### Strongly recommended features
- Dry-run mode
- Allowlist / denylist of secret names
- Prefix-based filtering (e.g. `prod-*`)
- Retry + throttling handling
- Structured logging
- Explicit delete behavior (default: **do not delete**)

---

## Step 6 — Containerize and Deploy as an AKS CronJob

1. Build a container image for the sync app
2. Push image to a container registry
3. Deploy to AKS as a **CronJob**
4. Example schedule:
   - Every 5 minutes
   - Every 15 minutes
   - Based on acceptable RPO

Deployment options:
- Helm chart (recommended)
- Raw Kubernetes manifests

---

## Step 7 — Observability and Alerting *(Optional)*

You should monitor:
- CronJob success/failure
- Execution duration
- Authentication errors
- Key Vault throttling

Recommended:
- Centralized logs (Log Analytics or equivalent)
- Alerts when:
  - Job fails repeatedly
  - No successful sync within expected interval

---

## Step 8 — Validation Checklist

Before relying on this for DR:

- Rotate a secret in the source vault → verify it appears in target
- Confirm identity **cannot write** to source vault
- Confirm identity **cannot read** from target vault unless required
- Validate behavior when:
  - Secret is disabled
  - Secret is updated rapidly
  - Many secrets exist
- Verify purge protection is enabled on both vaults

---

## Limitations & Design Tradeoffs

- This **increases secret blast radius** (secrets exist in multiple geographies)
- Eventual consistency is intentional
- This does **not** replicate cryptographic keys the same way HSM-backed replication does
- Syncing keys/certificates requires additional design and exportability checks
- Manual changes in the target vault may be overwritten

---

## When to Use This Pattern

✅ Cross-geography disaster recovery  
✅ Regional isolation requirements  
✅ Secrets-based configuration replication  

❌ Strong cryptographic guarantees across regions  
❌ Zero-RPO requirements  
❌ Bi-directional secret management  

---

## References

- Azure Key Vault backup & restore geography limitation  
  https://learn.microsoft.com/en-us/azure/key-vault/general/overview-security-worlds

- Multi-region Key Vault sync pattern  
  https://dev.to/anderson_leite/building-an-azure-key-vault-multi-region-sync-solution-3ca7

- AKS Workload Identity  
  https://learn.microsoft.com/en-us/azure/aks/workload-identity-overview

- Reference implementation  
  https://github.com/vakaobr/akv-sync