import base64
import xml.etree.ElementTree as ET

import libvirt

from ..connections import connect
from ..server import mcp
from ..states import domain_state


def _disk_to_dict(d: ET.Element) -> dict:
    tgt = d.find("target")
    src = d.find("source")
    src_path = None
    if src is not None:
        src_path = (
            src.get("file")
            or src.get("dev")
            or src.get("name")
            or src.get("volume")
        )
    return {
        "device": d.get("device"),
        "target": tgt.get("dev") if tgt is not None else None,
        "bus": tgt.get("bus") if tgt is not None else None,
        "source": src_path,
    }


def _nic_to_dict(n: ET.Element) -> dict:
    mac = n.find("mac")
    src = n.find("source")
    mdl = n.find("model")
    src_name = None
    if src is not None:
        src_name = src.get("network") or src.get("bridge") or src.get("dev")
    return {
        "type": n.get("type"),
        "mac": mac.get("address") if mac is not None else None,
        "source": src_name,
        "model": mdl.get("type") if mdl is not None else None,
    }


@mcp.tool()
def list_domains(profile: str | None = None) -> dict:
    """List all domains on a libvirt host with state and basic metadata.

    Args:
        profile: Connection profile name from config.toml. If omitted, the
            configured default_profile is used.
    """
    with connect(profile, readonly=True) as conn:
        domains = conn.listAllDomains()
        items = []
        for d in domains:
            state_code, _reason = d.state()
            items.append(
                {
                    "name": d.name(),
                    "uuid": d.UUIDString(),
                    "id": d.ID() if d.isActive() else None,
                    "state": domain_state(state_code),
                    "persistent": bool(d.isPersistent()),
                    "autostart": bool(d.autostart()),
                }
            )
        return {
            "host": conn.getHostname(),
            "domains_total": len(items),
            "domains": items,
        }


@mcp.tool()
def domain_info(name: str, profile: str | None = None) -> dict:
    """Detailed info about a single domain: state, CPU/RAM, disks, NICs, leased IPs, snapshots.

    Args:
        name: Domain name (as shown by list_domains).
        profile: Connection profile name.
    """
    with connect(profile, readonly=True) as conn:
        dom = conn.lookupByName(name)
        state_code, _ = dom.state()
        # info() -> [state, maxMem_KB, memory_KB, nrVirtCpu, cpuTime_ns]
        info = dom.info()
        xml_root = ET.fromstring(dom.XMLDesc(0))

        disks = [_disk_to_dict(d) for d in xml_root.findall("./devices/disk")]
        nics = [_nic_to_dict(n) for n in xml_root.findall("./devices/interface")]

        ips: list[dict] = []
        if dom.isActive():
            for src_code, src_label in (
                (libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_LEASE, "lease"),
                (libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_ARP, "arp"),
            ):
                try:
                    addrs = dom.interfaceAddresses(src_code)
                except libvirt.libvirtError:
                    continue
                for iface, data in (addrs or {}).items():
                    for a in data.get("addrs") or []:
                        ips.append(
                            {
                                "iface": iface,
                                "mac": data.get("hwaddr"),
                                "addr": a.get("addr"),
                                "prefix": a.get("prefix"),
                                "source": src_label,
                            }
                        )
                if ips:
                    break

        try:
            snap_names = dom.snapshotListNames() or []
        except libvirt.libvirtError:
            snap_names = []

        return {
            "name": dom.name(),
            "uuid": dom.UUIDString(),
            "id": dom.ID() if dom.isActive() else None,
            "state": domain_state(state_code),
            "max_memory_mib": info[1] // 1024,
            "memory_mib": info[2] // 1024,
            "vcpus": info[3],
            "persistent": bool(dom.isPersistent()),
            "autostart": bool(dom.autostart()),
            "disks": disks,
            "nics": nics,
            "ips": ips,
            "snapshots": snap_names,
        }


@mcp.tool()
def snapshot_list(name: str, profile: str | None = None) -> dict:
    """List snapshots of a domain with creation time, state, parent and description.

    Args:
        name: Domain name.
        profile: Connection profile name.
    """
    with connect(profile, readonly=True) as conn:
        dom = conn.lookupByName(name)
        snaps = dom.listAllSnapshots()
        items = []
        for s in snaps:
            xroot = ET.fromstring(s.getXMLDesc())
            ct = xroot.findtext("creationTime")
            items.append(
                {
                    "name": s.getName(),
                    "creation_time_epoch": int(ct) if ct else None,
                    "state": xroot.findtext("state"),
                    "parent": xroot.findtext("parent/name"),
                    "description": xroot.findtext("description"),
                    "is_current": bool(s.isCurrent()),
                }
            )
        return {"domain": name, "snapshots_total": len(items), "snapshots": items}


@mcp.tool()
def screenshot(name: str, profile: str | None = None, screen: int = 0) -> dict:
    """Capture a screenshot of a running domain's display, returned as base64-encoded image.

    Args:
        name: Domain name.
        profile: Connection profile name.
        screen: Screen index (default 0 = primary).

    Returns:
        dict with mime_type, size_bytes, data_base64. If the domain is not
        running, returns {"error": ...}.

    Note: libvirt refuses virDomainScreenshot on a read-only connection, so
    this tool opens a read-write connection. The function itself only reads
    framebuffer pixels and never mutates domain state.
    """
    with connect(profile, readonly=False) as conn:
        dom = conn.lookupByName(name)
        if not dom.isActive():
            return {"error": f"Domain {name!r} is not running"}
        stream = conn.newStream(0)
        try:
            mime = dom.screenshot(stream, screen, 0)
            chunks: list[bytes] = []
            while True:
                data = stream.recv(262144)
                if data is None or len(data) == 0:
                    break
                chunks.append(data)
            stream.finish()
        except libvirt.libvirtError as e:
            try:
                stream.abort()
            except Exception:
                pass
            return {"error": f"Screenshot failed: {e}"}
        blob = b"".join(chunks)
        return {
            "domain": name,
            "screen": screen,
            "mime_type": mime,
            "size_bytes": len(blob),
            "data_base64": base64.b64encode(blob).decode("ascii"),
        }
