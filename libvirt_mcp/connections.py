import os
import tomllib
from contextlib import contextmanager
from pathlib import Path

import libvirt

_DEFAULT_CONFIG_CANDIDATES = (
    os.environ.get("LIBVIRT_MCP_CONFIG"),
    str(Path.home() / ".config" / "libvirt-mcp" / "config.toml"),
    "config.dev.toml",
    "config.toml",
)

_config_cache: dict | None = None


def _find_config_path() -> str:
    for path in _DEFAULT_CONFIG_CANDIDATES:
        if path and Path(path).is_file():
            return path
    tried = [p for p in _DEFAULT_CONFIG_CANDIDATES if p]
    raise FileNotFoundError(
        "No libvirt-mcp config found. Tried: " + ", ".join(tried)
    )


def load_config() -> dict:
    global _config_cache
    if _config_cache is None:
        path = _find_config_path()
        with open(path, "rb") as f:
            _config_cache = tomllib.load(f)
        _config_cache["__path__"] = path
    return _config_cache


def list_profiles() -> dict[str, str]:
    cfg = load_config()
    return {name: prof.get("uri", "") for name, prof in cfg.get("profiles", {}).items()}


def resolve_profile(profile: str | None) -> tuple[str, str]:
    cfg = load_config()
    name = profile or cfg.get("default_profile")
    if not name:
        raise ValueError("No profile specified and no default_profile in config")
    prof = cfg.get("profiles", {}).get(name)
    if not prof:
        raise ValueError(
            f"Unknown profile {name!r}. Known: {sorted(cfg.get('profiles', {}).keys())}"
        )
    uri = prof.get("uri")
    if not uri:
        raise ValueError(f"Profile {name!r} has no `uri` set")
    return name, uri


@contextmanager
def connect(profile: str | None = None, readonly: bool = False):
    name, uri = resolve_profile(profile)
    conn = libvirt.openReadOnly(uri) if readonly else libvirt.open(uri)
    if conn is None:
        raise RuntimeError(
            f"Failed to open libvirt connection (profile={name!r}, uri={uri!r})"
        )
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass
