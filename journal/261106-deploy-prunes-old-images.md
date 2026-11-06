# The deploy prunes old images

> Purpose
>
> Records why `scripts/deploy.sh` now removes old images after each deploy,
> and the one-off cleanup that prompted it.

**Date:** 2026-11-06
**Branch:** `deploy-prune-images`

## 1. What happened

The `documents` LXC's root disk (16G) reached 97% full with 572M free.
Docker held 24 images. 17 of them were untagged and unused: 15 earlier
`library` builds at 1.42GB each and 2 old `pgvector` builds. Every deploy
pulls a new `:latest` and leaves the previous image behind with no tag, and
nothing removed them. About two weeks of deploys filled the disk.

## 2. The one-off cleanup

- `docker image prune -f` reclaimed 12GB.
- `docker volume rm apps_promtail_positions` removed the only Docker volume
  on the host. Promtail left it behind when alloy replaced it. The database
  is a bind mount (`/srv/apps/library/pgdata`), not a volume, so no volume
  prune could reach it.
- The systemd journal held 1.1G, almost all of it in the active file.
  `journalctl --vacuum-size` only deletes archived files, so it freed nothing
  until `journalctl --rotate` ran first. It now holds 191M.

The disk ended at 50% used, with 8.1G free. ZFS returned the space over a
few seconds, so `df` read lower than Docker's figure at first.

## 3. The durable fix

`deploy()` now calls `prune_old_images` after the `/healthz` checks pass.
Three decisions shaped it:

- **Dangling only, never `-a`.** `-a` also deletes tagged images that no
  container runs at that moment.
- **Rollback does not need the old images.** Rollback pins a `:<sha>` tag,
  which CI keeps on ghcr. The runbook's rollback step 1 used to list local
  images to find the SHA, which could never have worked: the old images were
  `<none>` locally. It now reads the SHA from `git log`.
- **Non-fatal, and only after a healthy deploy.** A failed prune costs only
  disk. A deploy that aborts on a failed migration or health check prunes
  nothing.

`tests/test_deploy_script.py` now runs the whole `deploy()` path against the
fake ssh/docker. The new tests went red with the call removed and with `-a`.

## 4. Left open

- The journal cap is still journald's default (10% of the filesystem, about
  1.6G). A lower `SystemMaxUse` belongs in the home-server ansible role,
  which owns the host's config.
- The prune is host-wide. When another service on the host is updated, its
  old image goes dangling too, and the next library deploy removes it.
