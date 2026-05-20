from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "libvirt-mcp",
    instructions=(
        "Manage KVM/QEMU virtual machines on remote libvirt hosts via SSH-tunneled "
        "libvirt RPC. Use the `profile` argument to choose a target host (profiles "
        "defined in config.toml); omit `profile` to use the configured default. "
        "Read-only tools: list_domains, domain_info, snapshot_list, screenshot. "
        "Lifecycle tools: start_domain, shutdown_domain (graceful), reboot_domain, "
        "force_destroy_domain (abrupt power-off, requires confirm=True). "
        "Snapshot tools: snapshot_create, snapshot_revert (requires confirm=True), "
        "snapshot_delete (requires confirm=True). "
        "Management tools: clone_from_template (linked clone from an existing "
        "qcow2), delete_domain (undefine, with optional wipe_disks; requires "
        "confirm=True)."
    ),
)

from .tools import readonly, lifecycle, snapshot, management  # noqa: E402,F401 — registers tools on import
