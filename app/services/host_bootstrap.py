"""Resolve configured public API hosts for local Docker runs.

This is a local-infra safety net, not provider-specific business logic.
It helps when the machine/container DNS path fails to resolve a supplier host
that still has a valid public DNS record.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse
from urllib.request import urlopen

DOH_ENDPOINT = "https://dns.google/resolve?type=A&name={host}"
IGNORED_HOSTS = {"localhost", "redis", "host.docker.internal"}


def extract_public_hosts(env: Mapping[str, str]) -> list[str]:
    hosts: set[str] = set()
    for key, value in env.items():
        if not key.endswith("_URL"):
            continue
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https", "redis", "postgresql", "postgresql+asyncpg"}:
            continue
        host = parsed.hostname
        if not host or host in IGNORED_HOSTS:
            continue
        if _is_private_or_internal_host(host):
            continue
        hosts.add(host.lower())
    return sorted(hosts)


def parse_doh_ipv4(payload: dict) -> str | None:
    for answer in payload.get("Answer", []):
        if answer.get("type") != 1:
            continue
        ip = answer.get("data")
        if ip:
            return ip
    return None


def build_host_overrides(env: Mapping[str, str]) -> list[tuple[str, str]]:
    overrides: list[tuple[str, str]] = []
    for host in extract_public_hosts(env):
        if _system_resolves(host):
            continue
        ip = resolve_ipv4_via_doh(host)
        if ip:
            overrides.append((ip, host))
    return overrides


def apply_host_overrides(overrides: list[tuple[str, str]], hosts_path: str = "/etc/hosts") -> int:
    if not overrides:
        return 0

    path = Path(hosts_path)
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    additions: list[str] = []
    for ip, host in overrides:
        marker = f"{ip} {host}"
        if host in current:
            continue
        additions.append(marker)

    if not additions:
        return 0

    with path.open("a", encoding="utf-8") as handle:
        if current and not current.endswith("\n"):
            handle.write("\n")
        for line in additions:
            handle.write(f"{line}\n")
    return len(additions)


def resolve_ipv4_via_doh(host: str) -> str | None:
    with urlopen(DOH_ENDPOINT.format(host=host), timeout=10) as response:
        payload = json.load(response)
    return parse_doh_ipv4(payload)


def _system_resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, 443)
        return True
    except OSError:
        return False


def _is_private_or_internal_host(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host
    return addr.is_private or addr.is_loopback or addr.is_link_local
