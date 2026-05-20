import os
import re
import uuid
import xml.etree.ElementTree as ET

import libvirt

from ..connections import connect
from ..server import mcp

_VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
_VALID_BRIDGE_RE = re.compile(r"^[a-zA-Z0-9._-]{1,15}$")


def _check_absolute_path(value: str, label: str) -> str | None:
    if not isinstance(value, str) or not value.startswith("/"):
        return f"{label} must be an absolute path"
    if ".." in value.split("/"):
        return f"{label} must not contain '..' segments"
    return None


def _build_domain_xml(
    name: str,
    memory_mib: int,
    vcpus: int,
    disk_path: str,
    bridge: str | None,
) -> str:
    nic = ""
    if bridge:
        nic = (
            "    <interface type='bridge'>\n"
            f"      <source bridge='{bridge}'/>\n"
            "      <model type='virtio'/>\n"
            "    </interface>\n"
        )
    return (
        "<domain type='kvm'>\n"
        f"  <name>{name}</name>\n"
        f"  <memory unit='MiB'>{memory_mib}</memory>\n"
        f"  <vcpu placement='static'>{vcpus}</vcpu>\n"
        "  <os>\n"
        "    <type arch='x86_64' machine='pc'>hvm</type>\n"
        "    <boot dev='hd'/>\n"
        "  </os>\n"
        "  <features><acpi/><apic/></features>\n"
        "  <clock offset='utc'/>\n"
        "  <on_poweroff>destroy</on_poweroff>\n"
        "  <on_reboot>restart</on_reboot>\n"
        "  <on_crash>destroy</on_crash>\n"
        "  <devices>\n"
        "    <emulator>/usr/bin/qemu-system-x86_64</emulator>\n"
        "    <disk type='file' device='disk'>\n"
        "      <driver name='qemu' type='qcow2'/>\n"
        f"      <source file='{disk_path}'/>\n"
        "      <target dev='vda' bus='virtio'/>\n"
        "    </disk>\n"
        f"{nic}"
        "    <graphics type='vnc' port='-1' autoport='yes' listen='127.0.0.1'/>\n"
        "    <video><model type='cirrus'/></video>\n"
        "    <memballoon model='virtio'/>\n"
        "  </devices>\n"
        "</domain>\n"
    )


def _find_active_pool_covering(conn, dir_path: str):
    try:
        pools = conn.listAllStoragePools(
            libvirt.VIR_CONNECT_LIST_STORAGE_POOLS_ACTIVE
        )
    except libvirt.libvirtError:
        return None
    for p in pools:
        try:
            root = ET.fromstring(p.XMLDesc(0))
            if root.findtext("./target/path") == dir_path:
                return p
        except libvirt.libvirtError:
            continue
    return None


def _acquire_dir_pool(conn, dir_path: str):
    """Return (pool, is_transient). Reuses an active pool whose target matches
    dir_path, otherwise creates a transient one. Always returns a refreshed pool."""
    existing = _find_active_pool_covering(conn, dir_path)
    if existing is not None:
        try:
            existing.refresh(0)
        except libvirt.libvirtError:
            pass
        return existing, False
    pool_xml = (
        "<pool type='dir'>\n"
        f"  <name>libvirt-mcp-{uuid.uuid4().hex[:10]}</name>\n"
        f"  <target><path>{dir_path}</path></target>\n"
        "</pool>\n"
    )
    pool = conn.storagePoolCreateXML(pool_xml, 0)
    pool.refresh(0)
    return pool, True


def _release_pool(pool, is_transient: bool) -> None:
    if is_transient and pool is not None:
        try:
            pool.destroy()
        except Exception:
            pass


@mcp.tool()
def clone_from_template(
    new_name: str,
    template_disk: str,
    profile: str | None = None,
    memory_mib: int = 2048,
    vcpus: int = 2,
    disk_path: str | None = None,
    bridge: str | None = None,
) -> dict:
    """Create a new domain backed by a copy-on-write clone of an existing qcow2 template.

    The new VM gets its own qcow2 with the template as backing file (linked clone):
    fast, space-efficient, and the template is never modified. Inherits the
    template's virtual disk size automatically.

    Args:
        new_name: Domain name. Must match ^[a-z0-9][a-z0-9-]{0,30}$.
        template_disk: Absolute path on the hypervisor to the source qcow2.
        memory_mib: RAM in MiB (default 2048).
        vcpus: vCPU count (default 2).
        disk_path: Absolute path for the new disk. Default: same dir as
            template, named "<new_name>.qcow2".
        bridge: Host bridge to attach a single virtio NIC to (e.g., "br0").
            Omit for a domain without networking.
    """
    if not _VALID_NAME_RE.match(new_name):
        return {
            "error": (
                f"Invalid new_name {new_name!r}. "
                "Must match ^[a-z0-9][a-z0-9-]{0,30}$ "
                "(lowercase letters, digits, hyphens; not starting with hyphen)."
            )
        }
    err = _check_absolute_path(template_disk, "template_disk")
    if err:
        return {"error": err}
    if bridge is not None and not _VALID_BRIDGE_RE.match(bridge):
        return {"error": f"Invalid bridge name {bridge!r}"}
    if disk_path is None:
        disk_path = os.path.join(os.path.dirname(template_disk), f"{new_name}.qcow2")
    err = _check_absolute_path(disk_path, "disk_path")
    if err:
        return {"error": err}
    if disk_path == template_disk:
        return {"error": "disk_path must differ from template_disk"}
    if not isinstance(memory_mib, int) or not (64 <= memory_mib <= 1024 * 1024):
        return {"error": "memory_mib must be an int between 64 and 1048576"}
    if not isinstance(vcpus, int) or not (1 <= vcpus <= 128):
        return {"error": "vcpus must be an int between 1 and 128"}

    with connect(profile, readonly=False) as conn:
        try:
            existing = conn.lookupByName(new_name)
            return {
                "error": f"Domain {new_name!r} already exists (UUID={existing.UUIDString()})"
            }
        except libvirt.libvirtError:
            pass

        new_dir = os.path.dirname(disk_path)
        tmpl_dir = os.path.dirname(template_disk)

        pool, pool_transient = _acquire_dir_pool(conn, new_dir)
        tpool, tpool_transient = None, False
        try:
            try:
                tmpl_vol = conn.storageVolLookupByPath(template_disk)
            except libvirt.libvirtError:
                tmpl_vol = None

            if tmpl_vol is None and tmpl_dir != new_dir:
                tpool, tpool_transient = _acquire_dir_pool(conn, tmpl_dir)
                try:
                    tmpl_vol = conn.storageVolLookupByPath(template_disk)
                except libvirt.libvirtError as e:
                    return {"error": f"Could not find template at {template_disk!r}: {e}"}

            if tmpl_vol is None:
                return {"error": f"Template disk not found: {template_disk!r}"}

            capacity_bytes = tmpl_vol.info()[1]

            vol_xml = (
                "<volume>\n"
                f"  <name>{os.path.basename(disk_path)}</name>\n"
                f"  <capacity unit='bytes'>{capacity_bytes}</capacity>\n"
                "  <target>\n"
                "    <format type='qcow2'/>\n"
                "  </target>\n"
                "  <backingStore>\n"
                f"    <path>{template_disk}</path>\n"
                "    <format type='qcow2'/>\n"
                "  </backingStore>\n"
                "</volume>\n"
            )
            vol = pool.createXML(vol_xml, 0)
            new_disk_actual = vol.path()
        finally:
            _release_pool(tpool, tpool_transient)
            _release_pool(pool, pool_transient)

        domain_xml = _build_domain_xml(new_name, memory_mib, vcpus, new_disk_actual, bridge)
        dom = conn.defineXML(domain_xml)

        return {
            "name": new_name,
            "uuid": dom.UUIDString(),
            "disk_path": new_disk_actual,
            "template": template_disk,
            "memory_mib": memory_mib,
            "vcpus": vcpus,
            "bridge": bridge,
            "state": "shutoff",
            "defined": True,
        }


@mcp.tool()
def delete_domain(
    name: str,
    profile: str | None = None,
    confirm: bool = False,
    wipe_disks: bool = False,
) -> dict:
    """Undefine a domain. Requires the domain to be shut off.

    Removes the domain definition along with its managed save state, snapshot
    metadata, NVRAM, and checkpoint metadata.

    If `wipe_disks=True`, also deletes the qcow2/raw disk files that the
    domain was using (only the disks declared as device='disk' — CDROMs and
    other media are left alone). Each disk is removed via a transient libvirt
    storage pool so this works even on hosts that don't have managed pools
    configured.

    Requires `confirm=True`.
    """
    if not confirm:
        return {
            "error": (
                "delete_domain requires confirm=True. This permanently undefines "
                "the domain (and deletes its disks if wipe_disks=True)."
            )
        }
    with connect(profile, readonly=False) as conn:
        dom = conn.lookupByName(name)
        if dom.isActive():
            return {
                "error": (
                    f"Domain {name!r} is still running. Stop it first with "
                    "shutdown_domain or force_destroy_domain(confirm=True)."
                )
            }

        disk_paths: list[str] = []
        if wipe_disks:
            xml_root = ET.fromstring(dom.XMLDesc(0))
            for d in xml_root.findall("./devices/disk"):
                if d.get("device") != "disk":
                    continue
                src = d.find("source")
                if src is None:
                    continue
                path = src.get("file") or src.get("dev")
                if path:
                    disk_paths.append(path)

        flags = (
            libvirt.VIR_DOMAIN_UNDEFINE_MANAGED_SAVE
            | libvirt.VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA
            | libvirt.VIR_DOMAIN_UNDEFINE_NVRAM
            | libvirt.VIR_DOMAIN_UNDEFINE_CHECKPOINTS_METADATA
        )
        dom.undefineFlags(flags)

        deleted: list[str] = []
        failed: list[dict] = []
        if wipe_disks:
            for path in disk_paths:
                parent = os.path.dirname(path)
                pool, is_transient = None, False
                try:
                    pool, is_transient = _acquire_dir_pool(conn, parent)
                    vol = conn.storageVolLookupByPath(path)
                    vol.delete()
                    deleted.append(path)
                except libvirt.libvirtError as e:
                    failed.append({"path": path, "error": str(e)})
                finally:
                    _release_pool(pool, is_transient)

        return {
            "name": name,
            "undefined": True,
            "wipe_disks": wipe_disks,
            "deleted_disks": deleted,
            "failed_disks": failed,
        }
