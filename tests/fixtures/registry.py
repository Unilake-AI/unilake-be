"""Cung cấp dữ liệu riêng cho kiểm thử, không đăng ký vào ứng dụng thực tế."""

from __future__ import annotations

from app.domains.registry import DomainRegistry
from tests.fixtures.sample_domain import DOMAIN_DEFINITION


def sample_registry() -> DomainRegistry:
    registry = DomainRegistry()
    registry.register(DOMAIN_DEFINITION)
    return registry
