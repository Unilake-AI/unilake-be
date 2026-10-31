"""Đăng ký, kiểm tra và phát hiện miền nghiệp vụ theo quy ước."""

from __future__ import annotations

import importlib
import pkgutil
import re
from functools import lru_cache
from typing import TYPE_CHECKING

from app.domains.base import DataLayer, DomainDefinition

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType

DOMAIN_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
DOMAIN_EXPORT = "DOMAIN_DEFINITION"


class DomainRegistryError(ValueError):
    """Phát sinh khi không thể đăng ký miền nghiệp vụ một cách an toàn."""


class DomainRegistry:
    """Bộ đăng ký trong bộ nhớ đã kiểm tra, dùng chung cho các mô-đun ứng dụng."""

    def __init__(self) -> None:
        self._domains: dict[str, DomainDefinition] = {}

    def register(self, definition: DomainDefinition) -> None:
        """Kiểm tra và đăng ký một định nghĩa miền bất biến."""
        self._validate(definition)
        if definition.domain_id in self._domains:
            raise DomainRegistryError(f"Duplicate domain id: {definition.domain_id}")
        self._domains[definition.domain_id] = definition

    def get(self, domain_id: str) -> DomainDefinition:
        """Tra cứu miền nghiệp vụ bằng mã định danh ổn định."""
        try:
            return self._domains[domain_id]
        except KeyError as exc:
            raise LookupError(f"Unknown domain: {domain_id}") from exc

    def all(self) -> tuple[DomainDefinition, ...]:
        """Trả về các định nghĩa theo thứ tự mã định danh xác định."""
        return tuple(self._domains[key] for key in sorted(self._domains))

    def __iter__(self) -> Iterator[DomainDefinition]:
        return iter(self.all())

    @staticmethod
    def _validate(definition: DomainDefinition) -> None:
        if not DOMAIN_ID_PATTERN.fullmatch(definition.domain_id):
            raise DomainRegistryError(
                "Domain id must use lower-case letters, digits, and underscores"
            )
        if not definition.version or not definition.schema_version:
            raise DomainRegistryError("Domain and schema versions are required")

        datasets = {dataset.name: dataset for dataset in definition.datasets}
        if len(datasets) != len(definition.datasets):
            raise DomainRegistryError(f"Duplicate dataset in domain '{definition.domain_id}'")
        for required_layer in (DataLayer.SILVER, DataLayer.GOLD):
            if not any(dataset.layer is required_layer for dataset in definition.datasets):
                raise DomainRegistryError(
                    f"Domain '{definition.domain_id}' must declare at least one "
                    f"{required_layer.value.title()} dataset"
                )
        if not definition.mappings:
            raise DomainRegistryError(
                f"Domain '{definition.domain_id}' must declare at least one source mapping"
            )

        mapping_keys: set[str] = set()
        for mapping in definition.mappings:
            if not mapping.version:
                raise DomainRegistryError(
                    f"Source mapping '{mapping.source_type}' must declare a version"
                )
            if mapping.source_type in mapping_keys:
                raise DomainRegistryError(
                    f"Duplicate source mapping '{mapping.source_type}' in '{definition.domain_id}'"
                )
            mapping_keys.add(mapping.source_type)
            target = datasets.get(mapping.target_dataset)
            if target is None or target.layer is not DataLayer.SILVER:
                raise DomainRegistryError(
                    f"Mapping target '{mapping.target_dataset}' must be a declared Silver dataset"
                )

        for rule in definition.quality_rules:
            if rule.dataset not in datasets:
                raise DomainRegistryError(
                    f"Quality rule '{rule.rule_id}' targets an unknown dataset '{rule.dataset}'"
                )

        for metric in definition.semantic_model.metrics:
            dataset = datasets.get(metric.dataset)
            if dataset is None or dataset.layer is not DataLayer.GOLD:
                raise DomainRegistryError(
                    f"Metric '{metric.name}' must target a declared Gold dataset"
                )


def discover_domains(package_name: str = "app.domains") -> DomainRegistry:
    """Phát hiện các gói miền con xuất ``DOMAIN_DEFINITION``."""
    package = importlib.import_module(package_name)
    registry = DomainRegistry()
    package_paths = getattr(package, "__path__", None)
    if package_paths is None:
        raise DomainRegistryError(f"Domain package has no package path: {package_name}")

    for module_info in pkgutil.iter_modules(package_paths):
        if not module_info.ispkg or module_info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{package_name}.{module_info.name}")
        definition = _read_definition(module)
        registry.register(definition)
    return registry


def _read_definition(module: ModuleType) -> DomainDefinition:
    definition = getattr(module, DOMAIN_EXPORT, None)
    if not isinstance(definition, DomainDefinition):
        raise DomainRegistryError(
            f"{module.__name__} must export a DomainDefinition as {DOMAIN_EXPORT}"
        )
    return definition


@lru_cache(maxsize=1)
def get_domain_registry() -> DomainRegistry:
    """Trả về bộ đăng ký miền dùng chung toàn tiến trình, đã kiểm tra khi khởi động."""
    return discover_domains()
