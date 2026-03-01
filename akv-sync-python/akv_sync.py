#!/usr/bin/env python3
"""
Azure Key Vault Multi-Region Secret Sync (Python edition).

Reads every secret from a source Key Vault and writes it to a target
Key Vault.  Designed to run as an AKS CronJob with Workload Identity;
falls back to DefaultAzureCredential for local testing with `az login`.

Environment variables
---------------------
SOURCE_VAULT_URL    (required)  e.g. https://kv-source.vault.azure.net
TARGET_VAULT_URL    (required)  e.g. https://kv-target.vault.azure.net
DRY_RUN             true|false  Log actions without making changes (default: false)
LOG_LEVEL           DEBUG|INFO|WARNING|ERROR  (default: INFO)
EXCLUDE_SECRETS     Comma-separated secret names to skip
SYNC_DISABLED       true|false  Whether to sync disabled secrets (default: true)
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential, WorkloadIdentityCredential
from azure.keyvault.secrets import SecretClient

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _env(name: str, default: str | None = None, required: bool = False) -> str:
    """Read an environment variable with optional default / required check."""
    value = os.environ.get(name, default or "")
    if required and not value:
        sys.exit(f"FATAL: environment variable {name} is required but not set")
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    return _env(name, str(default)).lower() in ("true", "1", "yes")


@dataclass
class Config:
    source_vault_url: str = field(default_factory=lambda: _env("SOURCE_VAULT_URL", required=True))
    target_vault_url: str = field(default_factory=lambda: _env("TARGET_VAULT_URL", required=True))
    dry_run: bool = field(default_factory=lambda: _env_bool("DRY_RUN", False))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))
    exclude_secrets: list[str] = field(default_factory=lambda: [
        s.strip() for s in _env("EXCLUDE_SECRETS", "").split(",") if s.strip()
    ])
    sync_disabled: bool = field(default_factory=lambda: _env_bool("SYNC_DISABLED", True))


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging(level_name: str) -> logging.Logger:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    return logging.getLogger("akv-sync")


# ---------------------------------------------------------------------------
# Credential helper
# ---------------------------------------------------------------------------

def _build_credential():
    """Use WorkloadIdentityCredential when running inside AKS, otherwise
    fall back to DefaultAzureCredential (covers ``az login`` for local dev)."""
    wi_vars = ("AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_FEDERATED_TOKEN_FILE")
    if all(os.environ.get(v) for v in wi_vars):
        return WorkloadIdentityCredential()
    return DefaultAzureCredential()


# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------

@dataclass
class Stats:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0


def _sync(cfg: Config, log: logging.Logger) -> Stats:
    credential = _build_credential()
    source = SecretClient(vault_url=cfg.source_vault_url, credential=credential)
    target = SecretClient(vault_url=cfg.target_vault_url, credential=credential)

    stats = Stats()

    # ------------------------------------------------------------------
    # 1. List source secrets (name → enabled flag)
    # ------------------------------------------------------------------
    log.info("Listing secrets from source vault: %s", cfg.source_vault_url)
    source_props: dict[str, bool] = {}       # name → enabled
    for prop in source.list_properties_of_secrets():
        name = prop.name
        enabled = prop.enabled if prop.enabled is not None else True

        if name in cfg.exclude_secrets:
            log.debug("Excluding secret (exclude list): %s", name)
            stats.skipped += 1
            continue

        if not enabled and not cfg.sync_disabled:
            log.debug("Skipping disabled secret: %s", name)
            stats.skipped += 1
            continue

        source_props[name] = enabled

    log.info("Found %d secret(s) to sync (%d skipped)", len(source_props), stats.skipped)

    # ------------------------------------------------------------------
    # 2. Build a set of existing target secret names for O(1) lookups
    # ------------------------------------------------------------------
    log.info("Listing secrets from target vault: %s", cfg.target_vault_url)
    target_names: set[str] = set()
    for prop in target.list_properties_of_secrets():
        target_names.add(prop.name)

    # ------------------------------------------------------------------
    # 3. Sync each secret
    # ------------------------------------------------------------------
    for name, src_enabled in source_props.items():
        try:
            # Fetch source value
            src_secret = source.get_secret(name)
            src_value = src_secret.value

            if name in target_names:
                # Secret exists in target — compare
                tgt_secret = target.get_secret(name)
                tgt_enabled = tgt_secret.properties.enabled if tgt_secret.properties.enabled is not None else True

                if tgt_secret.value == src_value and tgt_enabled == src_enabled:
                    log.debug("[%s] up-to-date — skipped", name)
                    stats.skipped += 1
                    continue

                # Value or enabled-state differs → update
                if cfg.dry_run:
                    log.info("[%s] would update (dry-run)", name)
                else:
                    target.set_secret(name, src_value, enabled=src_enabled)
                    log.info("[%s] → updated", name)
                stats.updated += 1
            else:
                # Secret missing in target → create
                if cfg.dry_run:
                    log.info("[%s] would create (dry-run)", name)
                else:
                    target.set_secret(name, src_value, enabled=src_enabled)
                    log.info("[%s] → created", name)
                stats.created += 1

        except (HttpResponseError, ResourceNotFoundError) as exc:
            log.error("[%s] sync failed: %s", name, exc)
            stats.errors += 1

    return stats


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    cfg = Config()
    log = _setup_logging(cfg.log_level)

    log.info("=== AKV Sync (Python) starting ===")
    log.info("Source : %s", cfg.source_vault_url)
    log.info("Target : %s", cfg.target_vault_url)
    log.info("Dry-run: %s", cfg.dry_run)
    if cfg.exclude_secrets:
        log.info("Exclude: %s", ", ".join(cfg.exclude_secrets))
    log.info("Sync disabled secrets: %s", cfg.sync_disabled)
    log.info("")

    stats = _sync(cfg, log)

    log.info("")
    log.info("=== Sync complete ===")
    log.info(
        "Created: %d | Updated: %d | Skipped: %d | Errors: %d",
        stats.created,
        stats.updated,
        stats.skipped,
        stats.errors,
    )

    if stats.errors > 0:
        log.error("Finished with %d error(s)", stats.errors)
        sys.exit(1)


if __name__ == "__main__":
    main()
