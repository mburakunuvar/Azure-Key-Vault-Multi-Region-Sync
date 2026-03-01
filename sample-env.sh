#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# sample-env.sh — Sample environment variables for the AKV Multi-Region Sync walkthrough
#
# Usage:
#   cp sample-env.sh env.sh
#   # Edit env.sh with your real values, then:
#   source env.sh
#
# This file contains pseudo values for reference only.
# Do NOT use real credentials — env.sh is .gitignored for that reason.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------
export SUBSCRIPTION_ID="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
# az account set --subscription "$SUBSCRIPTION_ID"   # run after sourcing

# ---------------------------------------------------------------------------
# Resource Groups
# ---------------------------------------------------------------------------
export RG_SOURCE="rg-akv-sync-source"
export RG_TARGET="rg-akv-sync-target"
export AKS_RG="rg-akv-sync-aks"

# ---------------------------------------------------------------------------
# Regions
# ---------------------------------------------------------------------------
export LOCATION_SOURCE="westeurope"
export LOCATION_TARGET="swedencentral"
export AKS_LOCATION="westeurope"

# ---------------------------------------------------------------------------
# Step 2 & 3 — Key Vaults
# ---------------------------------------------------------------------------
export SOURCE_KV="kv-akvsync-source"
export TARGET_KV="kv-akvsync-target-dr"

# ---------------------------------------------------------------------------
# Step 2 — Your own user object ID (needed to grant yourself KV access)
# ---------------------------------------------------------------------------
export MY_OBJECT_ID="yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy"

# ---------------------------------------------------------------------------
# Step 4 — AKS Cluster
# ---------------------------------------------------------------------------
export AKS_NAME="aks-akvsync"

export OIDC_ISSUER="https://eastus.oic.prod-aks.azure.com/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz/"

# ---------------------------------------------------------------------------
# Step 5 — Workload Identity
# ---------------------------------------------------------------------------
export IDENTITY_RG="rg-akv-sync-aks"
export IDENTITY_NAME="id-akvsync"
export NAMESPACE="akv-sync"
export SA_NAME="akv-sync-sa"

export CLIENT_ID="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
export PRINCIPAL_ID="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

# ---------------------------------------------------------------------------
# Step 6 — Key Vault resource IDs (derived)
# ---------------------------------------------------------------------------
export SOURCE_KV_ID="/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/rg-akv-sync-source/providers/Microsoft.KeyVault/vaults/kv-akvsync-source"
export TARGET_KV_ID="/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/rg-akv-sync-target/providers/Microsoft.KeyVault/vaults/kv-akvsync-target-dr"

# ---------------------------------------------------------------------------
# Step 7 — Container Registry
# ---------------------------------------------------------------------------
export ACR_NAME="acrakvsync"   # must be globally unique, alphanumeric only
export ACR_LOGIN_SERVER="acrakvsync.azurecr.io"

# ---------------------------------------------------------------------------
# Step 8 — Tenant (needed for ServiceAccount annotation)
# ---------------------------------------------------------------------------
export TENANT_ID="cccccccc-cccc-cccc-cccc-cccccccccccc"

# ---------------------------------------------------------------------------
# Sanity check — print key variables so you can spot any that are empty
# ---------------------------------------------------------------------------
echo "SUBSCRIPTION_ID : ${SUBSCRIPTION_ID}"
echo "SOURCE_KV       : ${SOURCE_KV}"
echo "TARGET_KV       : ${TARGET_KV}"
echo "AKS_NAME        : ${AKS_NAME}"
echo "OIDC_ISSUER     : ${OIDC_ISSUER}"
echo "CLIENT_ID       : ${CLIENT_ID}"
echo "PRINCIPAL_ID    : ${PRINCIPAL_ID}"
echo "ACR_NAME        : ${ACR_NAME}"
echo "TENANT_ID       : ${TENANT_ID}"
