"""Miền demo chứng minh các mô-đun chung không cần rẽ nhánh theo miền nghiệp vụ."""

from __future__ import annotations

from app.domains.base import DomainDefinition
from tests.fixtures.sample_domain.mapping import MAPPINGS
from tests.fixtures.sample_domain.quality import QUALITY_RULES
from tests.fixtures.sample_domain.schema import DATASETS, SEMANTIC_MODEL
from tests.fixtures.sample_domain.transformer import DemoTransformer

DOMAIN_DEFINITION = DomainDefinition(
    domain_id="demo",
    display_name="Demo Students",
    version="1.0",
    schema_version="1.0",
    datasets=DATASETS,
    mappings=MAPPINGS,
    transformer=DemoTransformer(),
    quality_rules=QUALITY_RULES,
    semantic_model=SEMANTIC_MODEL,
)

__all__ = ["DOMAIN_DEFINITION"]
