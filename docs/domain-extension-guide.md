# Domain Extension Guide

UniLake domains are business-specific implementations loaded by generic ingestion, pipeline,
governance, and NLQ modules. A domain owns its data meaning; generic modules own orchestration,
storage boundaries, execution state, and infrastructure.

## Module boundaries

```text
Data source
  -> ingestion (Source -> Bronze)
  -> pipeline (Bronze -> Silver -> Gold)
  -> governance events and catalog
  -> NLQ Gold schema context
```

- `app/modules/datasources`: source connection metadata and configuration.
- `app/modules/ingestion`: raw capture and the immutable Bronze batch hand-off.
- `app/modules/pipeline`: generic transformation orchestration and quality gates.
- `app/domains`: schemas, mappings, transformations, quality rules, and semantic metadata.
- `app/modules/governance`: generic catalog plus runtime lineage and quality evidence.
- `app/modules/nlq`: dynamic retrieval of published Gold metadata.

Domain implementations must not access MinIO, databases, schedulers, FastAPI routers, or generic
module internals. Infrastructure is supplied through ports owned by the generic modules.

## Add a domain

1. Create `app/domains/<domain_id>/` with an `__init__.py`.
2. Define Silver and Gold datasets with stable names and field metadata.
3. Add one versioned mapping per supported source type.
4. Implement the `DomainTransformer` contract for Silver and Gold outputs.
5. Add quality rules bound to declared datasets.
6. Add Gold metrics and synonyms required by NLQ.
7. Export one `DOMAIN_DEFINITION` from the package.
8. Run the shared registry, pipeline, Governance, and NLQ contract tests.

The registry discovers child packages automatically. Adding a domain must not require edits to
ingestion, pipeline, governance, or NLQ logic. Deployment configuration and database migrations
may still be required for production storage.

## Versioning rules

- `version` identifies the domain implementation contract.
- `schema_version` identifies the dataset schema exposed to consumers.
- Each source mapping has its own `version` because source systems evolve independently.
- Breaking schema changes require a new schema version and an explicit migration/backfill plan.
- Existing identifiers must not silently change meaning within the same version.

## Validation and publication

Registry discovery fails at startup for duplicate domain ids, duplicate source mappings, mapping
targets that are not Silver datasets, missing Silver/Gold layers, unknown quality targets, or
metrics that do not reference a Gold dataset. Pipeline quality failures stop publication before
the next layer. Governance exposes static quality-rule metadata and records runtime lineage only
after Silver and Gold outputs have been published successfully.

`app/domains/demo` is the reference implementation and end-to-end proof of concept.
