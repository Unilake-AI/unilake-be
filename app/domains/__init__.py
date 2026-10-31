"""Các hợp đồng mở rộng và cơ chế phát hiện miền nghiệp vụ."""

from __future__ import annotations

from app.domains.base import DomainDefinition
from app.domains.registry import DomainRegistry, get_domain_registry

__all__ = ["DomainDefinition", "DomainRegistry", "get_domain_registry"]
