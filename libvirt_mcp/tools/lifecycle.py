import time

import libvirt

from ..connections import connect
from ..server import mcp
from ..states import domain_state


@mcp.tool()
def start_domain(name: str, profile: str | None = None) -> dict:
    """Start (boot) a defined domain that is currently shut off.

    Idempotent: returns changed=False if the domain is already running.
    """
    with connect(profile, readonly=False) as conn:
        dom = conn.lookupByName(name)
        if dom.isActive():
            return {
                "name": name,
                "state": "running",
                "changed": False,
                "message": "Already running",
            }
        dom.create()
        state_code, _ = dom.state()
        return {
            "name": name,
            "state": domain_state(state_code),
            "changed": True,
        }


@mcp.tool()
def shutdown_domain(
    name: str, profile: str | None = None, wait_seconds: int = 30
) -> dict:
    """Request graceful ACPI shutdown of a running domain and wait for it to power off.

    Sends an ACPI shutdown signal to the guest, then polls the domain state every
    second for up to `wait_seconds`. Returns success when the domain reaches
    `shutoff`. If the guest does not power off within the timeout, returns with
    `timed_out=True` and state still `running` — the caller can then decide
    whether to call `force_destroy_domain(confirm=True)`.

    Set `wait_seconds=0` to send the signal and return immediately without polling.
    """
    with connect(profile, readonly=False) as conn:
        dom = conn.lookupByName(name)
        if not dom.isActive():
            return {
                "name": name,
                "state": "shutoff",
                "changed": False,
                "message": "Already shut off",
            }
        dom.shutdown()

        if wait_seconds <= 0:
            state_code, _ = dom.state()
            return {
                "name": name,
                "state": domain_state(state_code),
                "changed": True,
                "message": "ACPI shutdown signal sent (no wait)",
            }

        waited = 0
        while waited < wait_seconds:
            time.sleep(1)
            waited += 1
            try:
                state_code, _ = dom.state()
            except libvirt.libvirtError:
                state_code = libvirt.VIR_DOMAIN_RUNNING
            if state_code == libvirt.VIR_DOMAIN_SHUTOFF:
                return {
                    "name": name,
                    "state": "shutoff",
                    "changed": True,
                    "waited_seconds": waited,
                    "message": f"Guest acknowledged ACPI shutdown after {waited}s",
                }

        state_code, _ = dom.state()
        return {
            "name": name,
            "state": domain_state(state_code),
            "changed": False,
            "timed_out": True,
            "wait_seconds": wait_seconds,
            "message": (
                f"Guest did not shut off within {wait_seconds}s. ACPI signal was sent. "
                "If the guest is hung or has no ACPI, call force_destroy_domain(confirm=True)."
            ),
        }


@mcp.tool()
def reboot_domain(name: str, profile: str | None = None) -> dict:
    """Request graceful ACPI reboot of a running domain."""
    with connect(profile, readonly=False) as conn:
        dom = conn.lookupByName(name)
        if not dom.isActive():
            return {"error": f"Domain {name!r} is not running; cannot reboot"}
        dom.reboot()
        return {
            "name": name,
            "state": "running",
            "changed": True,
            "message": "ACPI reboot signal sent",
        }


@mcp.tool()
def force_destroy_domain(
    name: str, profile: str | None = None, confirm: bool = False
) -> dict:
    """Force-stop a running domain (equivalent to yanking the power cable).

    This does NOT delete the domain definition or its disks — only stops
    execution abruptly. Pending guest writes are lost. The domain remains
    defined and can be started again.

    To prevent accidental invocation, `confirm` must be set to True
    explicitly.
    """
    if not confirm:
        return {
            "error": (
                "force_destroy_domain requires confirm=True. This is an "
                "abrupt power-off; pending guest writes will be lost."
            )
        }
    with connect(profile, readonly=False) as conn:
        dom = conn.lookupByName(name)
        if not dom.isActive():
            return {
                "name": name,
                "state": "shutoff",
                "changed": False,
                "message": "Already shut off",
            }
        dom.destroy()
        return {
            "name": name,
            "state": "shutoff",
            "changed": True,
            "message": "Domain forcefully destroyed (power-off)",
        }
