# Login instructions name the host directory

**Date:** 2026-10-27
**Branch:** `fix/ask-login-message-host-dir`
**Issue:** #195

## 1. What happened

Every Claude login on the home server, Library's included, failed on the night of
2026-10-26 (`invalid_grant`). Recovering Library took three attempts, and two of them
followed Library's own advice:

1. `/healthz` and the `invalid_grant` log line said "re-authenticate on the host with
   `claude auth login --claudeai`". As root, that logged in `/root/.claude`, which Library
   does not read.
2. Ask's 503 said `CLAUDE_CONFIG_DIR=/app/.claude claude auth login --claudeai`, then
   `chown 999:999 /app/.claude/.credentials.json`. `/app/.claude` is the container path. On
   the host it created a new directory with a valid login in it, and Library never read it.
3. The fix was moving that login into `/srv/apps/library/claude`, the host side of the
   mount, by hand.

The cause was `subscription._explain` formatting the command from
`settings.claude_config_dir`, which is the container's view.

## 2. What changed

- `oauth.login_instructions(config_dir, host_config_dir)` writes the host-side commands.
  With `LIBRARY_CLAUDE_HOST_CONFIG_DIR` set they are literal. Without it they say
  `<host dir>` and name the mount, and never print the container path as a host path.
- `token_health` takes the host directory and ends **every** non-healthy detail with those
  commands, including "expired, no refresh token" and the degraded case, which gave no
  instruction before. `_explain` reuses the detail instead of appending its own command, so
  the 503 no longer says it twice.
- The `invalid_grant` log line points to `/healthz` and §5.1 rather than giving a bare
  command, because the refresh path has no settings to hand.
- `docker-compose.yml` passes `LIBRARY_CLAUDE_HOST_CONFIG_DIR: ${CLAUDE_CONFIG_DIR:-}`, the
  same variable as the mount, so the two cannot disagree. An empty value counts as unset.
- `docs/llm-backends.md` §5.1/§5.2 and `.env.example` document the setting.

## 3. Decisions

- **A setting, not a guess.** The container cannot see the source of its own bind mount,
  and parsing `/proc/self/mountinfo` would give a device path, not the operator's
  directory. Compose already knows the path, so compose passes it.
- **The host directory is a plain `str`, not a `Path`.** It is never opened in the
  container, and a `Path` would suggest it could be.

## 4. What is deliberately not done

- **Production does not use this repo's compose file.** The home-server role
  `document_library_lxc` renders its own, so the live 503 shows `<host dir>` until that
  template sets `LIBRARY_CLAUDE_HOST_CONFIG_DIR=/srv/apps/library/claude`. That is a
  home-server change.
- **Not investigated: the emptied credentials file.** At 22:27:46 CET Library's
  credentials file was rewritten with empty tokens during two failed Ask calls. It is
  *suspected* to be the bundled CLI clearing credentials after an auth failure. Issue #195
  records it.
