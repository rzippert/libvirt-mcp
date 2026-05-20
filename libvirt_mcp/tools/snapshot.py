import xml.sax.saxutils as saxutils

import libvirt

from ..connections import connect
from ..server import mcp


def _snapshot_xml(name: str | None, description: str | None) -> str:
    parts = ["<domainsnapshot>"]
    if name:
        parts.append(f"<name>{saxutils.escape(name)}</name>")
    if description:
        parts.append(f"<description>{saxutils.escape(description)}</description>")
    parts.append("</domainsnapshot>")
    return "".join(parts)


@mcp.tool()
def snapshot_create(
    domain: str,
    name: str | None = None,
    description: str | None = None,
    profile: str | None = None,
) -> dict:
    """Create a snapshot of a domain.

    Works on both running and shut-off domains. If `name` is omitted, libvirt
    auto-generates one based on the current epoch time. For running domains a
    memory snapshot is captured as well unless the host has only disk-snapshot
    capability.
    """
    with connect(profile, readonly=False) as conn:
        dom = conn.lookupByName(domain)
        xml = _snapshot_xml(name, description)
        snap = dom.snapshotCreateXML(xml, 0)
        return {"domain": domain, "snapshot": snap.getName(), "created": True}


@mcp.tool()
def snapshot_revert(
    domain: str,
    snapshot: str,
    profile: str | None = None,
    confirm: bool = False,
) -> dict:
    """Revert a domain to a previous snapshot.

    DESTRUCTIVE: the current state of the domain is discarded and replaced by
    the snapshot's state. Requires `confirm=True`.
    """
    if not confirm:
        return {
            "error": (
                "snapshot_revert requires confirm=True. The current state of "
                "the domain will be discarded and replaced by the snapshot."
            )
        }
    with connect(profile, readonly=False) as conn:
        dom = conn.lookupByName(domain)
        snap = dom.snapshotLookupByName(snapshot)
        dom.revertToSnapshot(snap)
        return {"domain": domain, "snapshot": snapshot, "reverted": True}


@mcp.tool()
def snapshot_delete(
    domain: str,
    snapshot: str,
    profile: str | None = None,
    confirm: bool = False,
    children: bool = False,
) -> dict:
    """Delete a snapshot. Requires `confirm=True`.

    If `children=True`, also delete all descendant snapshots.
    """
    if not confirm:
        return {"error": "snapshot_delete requires confirm=True"}
    with connect(profile, readonly=False) as conn:
        dom = conn.lookupByName(domain)
        snap = dom.snapshotLookupByName(snapshot)
        flags = libvirt.VIR_DOMAIN_SNAPSHOT_DELETE_CHILDREN if children else 0
        snap.delete(flags)
        return {
            "domain": domain,
            "snapshot": snapshot,
            "deleted": True,
            "children": children,
        }
