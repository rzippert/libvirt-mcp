# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An MCP (Model Context Protocol) server that manages KVM/QEMU domains on remote libvirt
hosts over SSH-tunneled libvirt RPC (`qemu+ssh://user@host/system`). No agent runs on the
hypervisor — every operation is pure libvirt RPC (no `qemu-img`, `virt-install`, `virsh`
shell-out, or SSH command execution). Exposes 13 MCP tools spread across four tool modules.

## Architecture

- `server.py` — the single `FastMCP("libvirt-mcp")` instance plus its `instructions`
  string (the system prompt the LLM sees). The last line imports the four tool modules
  purely for their import side effect: each `@mcp.tool()` decorator registers a tool on
  this shared `mcp` object. **Adding a tool module means adding it to that import line**,
  or its tools won't register.
- `__main__.py` — `python -m libvirt_mcp` → `mcp.run()`, stdio JSON-RPC transport.
- `connections.py` — config loading (`load_config`, cached) and the `connect(profile,
  readonly)` context manager. Every tool acquires its libvirt connection through `connect`
  and lets the context manager close it. `resolve_profile` maps a profile name (or the
  configured `default_profile`) to a libvirt URI.
- `states.py` — `domain_state(code)` maps `VIR_DOMAIN_*` ints to lowercase strings.
- `tools/readonly.py` — inspection: `list_domains`, `domain_info`, `snapshot_list`,
  `screenshot`. Domain XML is parsed with `xml.etree.ElementTree` to extract disks/NICs.
- `tools/lifecycle.py` — `start_domain`, `shutdown_domain`, `reboot_domain`,
  `force_destroy_domain`.
- `tools/snapshot.py` — `snapshot_create`, `snapshot_revert`, `snapshot_delete`.
- `tools/management.py` — `clone_from_template` (linked qcow2 clone), `delete_domain`.

## Conventions to preserve when editing tools

- **Read-only by default.** Inspection tools call `connect(profile, readonly=True)`
  (`libvirt.openReadOnly`); anything that mutates state uses `readonly=False`. Exception:
  `screenshot` opens read-write because libvirt rejects `virDomainScreenshot` on a
  read-only connection — it still only reads framebuffer pixels.
- **Confirmation gates.** Destructive tools (`force_destroy_domain`, `snapshot_revert`,
  `snapshot_delete`, `delete_domain`) take `confirm: bool = False` and return an `{"error":
  ...}` dict early when `confirm` is falsy. This stops an LLM firing them by accident — keep
  the gate as the first thing the function does.
- **Errors are returned, not raised.** Tools return plain dicts; recoverable/validation
  failures are reported as `{"error": "..."}` rather than raising, so the LLM gets a usable
  message. Successful mutations typically include `changed: bool` and a `message`.
- **Input validation / allow-lists.** Domain names must match
  `^[a-z0-9][a-z0-9-]{0,30}$` (`_VALID_NAME_RE` in `management.py`), bridge names match
  `_VALID_BRIDGE_RE`, and disk paths must be absolute with no `..` segments
  (`_check_absolute_path`). Validate before opening a connection.
- **Storage via libvirt volume API, never shell.** `clone_from_template` and
  `delete_domain --wipe_disks` create/delete qcow2 volumes through libvirt's storage-pool
  API. `_acquire_dir_pool` reuses an active dir-pool whose target path matches, otherwise
  creates a *transient* pool (`storagePoolCreateXML`) that `_release_pool` destroys in a
  `finally`. Always pair acquire/release.
- Snapshot/domain XML built by hand: escape user strings (`saxutils.escape` in
  `snapshot.py`).

## Configuration

TOML config; search order is `$LIBVIRT_MCP_CONFIG` → `~/.config/libvirt-mcp/config.toml`
→ `./config.dev.toml` → `./config.toml`. A config defines `default_profile` and one or
more `[profiles.<name>]` tables each with a `uri` (and optional `description`). SSH keys
come from the host's `~/.ssh/` (bind-mounted read-only into the container).

## Development

There is no test suite, linter config, or formatter wired up. Development happens inside
the Docker dev container (so `libvirt-dev` headers aren't needed on the host):

```bash
cp config.example.toml config.dev.toml && $EDITOR config.dev.toml
docker compose build
docker compose run --rm dev bash
```

Inside the container the repo is bind-mounted at `/workspace` with `PYTHONPATH=/workspace`,
so source edits take effect live (host source shadows the installed package). Exercise a
tool directly — the underlying functions are plain Python, callable without the MCP layer:

```bash
python -c "from libvirt_mcp.tools.readonly import list_domains; \
           import json; print(json.dumps(list_domains(), indent=2))"
```

Connectivity sanity check before anything else:

```bash
ssh user@hypervisor.example.com virsh -c qemu:///system list --all
```

## Publishing

`.github/workflows/publish.yml` builds and pushes a multi-arch (`linux/amd64`,
`linux/arm64`) image to `ghcr.io/rzippert/libvirt-mcp` on push to `main` and on `v*` tags.
Git tag `vX.Y.Z` → Docker tags `X.Y.Z` and `X.Y` (the `v` prefix is dropped). Runs on
GitHub-hosted `ubuntu-latest` runners. Package visibility is **not** flipped by CI (the
default `GITHUB_TOKEN` can't); a maintainer sets it public once via the GitHub UI.
