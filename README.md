# libvirt-mcp

A Model Context Protocol (MCP) server for managing KVM/QEMU virtual machines on remote
libvirt hosts. Talks libvirt RPC over SSH — no agent on the hypervisor, no need to
install anything on the target host beyond the standard `libvirtd`.

Functionally a "virt-manager for LLMs": inspect, start/stop, snapshot, clone, and delete
KVM/QEMU domains through 13 MCP tools.

## Tools

| Group | Tools |
| --- | --- |
| Inspection | `list_domains`, `domain_info`, `snapshot_list`, `screenshot` |
| Lifecycle | `start_domain`, `shutdown_domain` (ACPI + poll), `reboot_domain`, `force_destroy_domain` |
| Snapshots | `snapshot_create`, `snapshot_revert`, `snapshot_delete` |
| Management | `clone_from_template` (linked clone), `delete_domain` (with optional `wipe_disks`) |

Destructive operations (`force_destroy_domain`, `snapshot_revert`, `snapshot_delete`,
`delete_domain`) require an explicit `confirm=True` argument so an LLM can't fire them
by accident.

## Architecture

```
┌──────────────┐                          ┌──────────────────────┐
│ MCP client   │  ── stdio (JSON-RPC) ──▶ │ libvirt-mcp          │
│ Claude Code, │                          │ (Docker container)   │
│ Desktop, …   │                          │  • libvirt-python    │
└──────────────┘                          │  • mcp SDK           │
                                          └──────────┬───────────┘
                                                     │
                                                     │ qemu+ssh://user@host/system
                                                     │ (uses host's ~/.ssh/)
                                                     ▼
                                          ┌──────────────────────┐
                                          │ Hypervisor           │
                                          │  • libvirtd          │
                                          │  • qemu-kvm          │
                                          └──────────────────────┘
```

The MCP server runs inside a Docker container (so `libvirt-dev` headers don't need to
be installed on your machine). It bind-mounts your `~/.ssh` read-only and uses standard
libvirt SSH transport, inheriting `~/.ssh/config`, agent forwarding, jump hosts, etc.

## Setup

### 1. Pull the image

The container image is published as `ghcr.io/rzippert/libvirt-mcp` (public — no auth
required, even though the source repo is private):

```bash
docker pull ghcr.io/rzippert/libvirt-mcp:latest
```

Tags published:

- `latest` — head of `main`
- `X.Y.Z` and `X.Y` — from git tags `vX.Y.Z` (the `v` prefix is dropped per Docker convention)
- `main`, `sha-<short>` — for traceability

### 2. Create your config

```bash
mkdir -p ~/.config/libvirt-mcp
cat > ~/.config/libvirt-mcp/config.toml <<'EOF'
default_profile = "primary"

[profiles.primary]
uri = "qemu+ssh://user@hypervisor.example.com/system"
description = "Primary KVM hypervisor"
EOF
$EDITOR ~/.config/libvirt-mcp/config.toml
```

The libvirt URI is standard. SSH keys come from your host's `~/.ssh/`. Make sure you
can already connect:

```bash
ssh user@hypervisor.example.com virsh -c qemu:///system list --all
```

### 3. Wire it up to an MCP client

#### Claude Code

```bash
claude mcp add libvirt --scope user -- \
  docker run -i --rm \
    -v "$HOME/.ssh:/home/ubuntu/.ssh:ro" \
    -v "$HOME/.config/libvirt-mcp/config.toml:/etc/libvirt-mcp/config.toml:ro" \
    --network host \
    -e LIBVIRT_MCP_CONFIG=/etc/libvirt-mcp/config.toml \
    ghcr.io/rzippert/libvirt-mcp:latest
```

Verify with `claude mcp list`. Remove with `claude mcp remove libvirt --scope user`.

#### Claude Desktop

Edit `claude_desktop_config.json`:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "libvirt": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-v", "/Users/youruser/.ssh:/home/ubuntu/.ssh:ro",
        "-v", "/Users/youruser/.config/libvirt-mcp/config.toml:/etc/libvirt-mcp/config.toml:ro",
        "--network", "host",
        "-e", "LIBVIRT_MCP_CONFIG=/etc/libvirt-mcp/config.toml",
        "ghcr.io/rzippert/libvirt-mcp:latest"
      ]
    }
  }
}
```

Replace `/Users/youruser` with your home directory. On Windows use forward slashes or
double-escape backslashes. Restart Claude Desktop after editing.

#### VS Code (GitHub Copilot Chat MCP)

Create `.vscode/mcp.json` in your workspace (project-scoped) or add to user settings:

```json
{
  "servers": {
    "libvirt": {
      "type": "stdio",
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-v", "${userHome}/.ssh:/home/ubuntu/.ssh:ro",
        "-v", "${userHome}/.config/libvirt-mcp/config.toml:/etc/libvirt-mcp/config.toml:ro",
        "--network", "host",
        "-e", "LIBVIRT_MCP_CONFIG=/etc/libvirt-mcp/config.toml",
        "ghcr.io/rzippert/libvirt-mcp:latest"
      ]
    }
  }
}
```

VS Code expands `${userHome}` and `${workspaceFolder}`. Open the Copilot Chat panel →
MCP servers → enable `libvirt`.

## Configuration reference

`~/.config/libvirt-mcp/config.toml` (or override with `LIBVIRT_MCP_CONFIG`):

```toml
default_profile = "primary"   # used when a tool is called without `profile=`

[profiles.<name>]
uri = "qemu+ssh://user@host/system"   # standard libvirt URI
description = "Free-form description for humans"
```

Config search order:

1. `$LIBVIRT_MCP_CONFIG` env var
2. `~/.config/libvirt-mcp/config.toml`
3. `./config.dev.toml`
4. `./config.toml`

## Safety model

- **Allow-list validation** on domain names (`^[a-z0-9][a-z0-9-]{0,30}$`), bridge names,
  and absolute paths (no `..` segments).
- **Inspection tools** open read-only libvirt connections; **state-changing tools** open
  read-write connections only when invoked.
- **Confirmation gates** on destructive operations: `force_destroy_domain`,
  `snapshot_revert`, `snapshot_delete`, `delete_domain` all require `confirm=True`.
- **No subprocess** on the hypervisor: every operation is pure libvirt RPC (no
  `qemu-img`, no `virt-install`, no `virsh` shell-out). Disk volumes are created and
  deleted through libvirt's storage volume API, reusing any active dir-pool that
  covers the target path or transparently creating a transient one.

## Development

```bash
git clone git@github.com:rzippert/libvirt-mcp.git
cd libvirt-mcp
cp config.example.toml config.dev.toml && $EDITOR config.dev.toml
docker compose build
docker compose run --rm dev bash
```

Inside the dev container, source on the host (bind-mounted at `/workspace`) shadows the
installed package via `PYTHONPATH`, so edits take effect immediately. Exercise tools
directly:

```bash
python -c "from libvirt_mcp.tools.readonly import list_domains; \
           import json; print(json.dumps(list_domains(), indent=2))"
```

Project layout:

```
libvirt_mcp/
├── __main__.py        # python -m libvirt_mcp → mcp.run() (stdio)
├── server.py          # FastMCP instance
├── connections.py     # config loader + connect() context manager
├── states.py          # VIR_DOMAIN_* → string
└── tools/
    ├── readonly.py    # inspection
    ├── lifecycle.py   # start/shutdown/reboot/force_destroy
    ├── snapshot.py    # snapshot CRUD (revert/delete gated)
    └── management.py  # clone_from_template, delete_domain
```

Publishing happens automatically via `.github/workflows/publish.yml` on push to `main`
and on `v*` tags (multi-arch: `linux/amd64`, `linux/arm64`).

## License

GNU Affero General Public License v3.0 or later (AGPL-3.0-or-later). See [LICENSE](LICENSE).
