# G5 — P5 Silver Integration

15/10/2026: tích hợp kỹ thuật và rehearsal đã triển khai; nghiệm thu production còn mở.

| Hạng mục | Trạng thái / bằng chứng |
|---|---|
| S2/S3/S4 → Silver-only entrypoint | SilverPipeline + NormalizationTask; không gọi Gold legacy |
| Strict input gate | Chặn preview/reject/error/count mismatch/schema/provenance sai |
| Audit enrichment | Nguồn/run/profile versions và locator opaque; không nhận audit từ business row |
| Snapshot checks | Native physical types, enum/constraints, PK, nullable unique, FK, self-cycle |
| Parquet writer/read-back | Real PyArrow round-trip; Decimal/date/timestamp/nullable schema |
| Manifest/lineage | Object checksum/version/count, versions/fingerprints, pinned parent snapshot |
| Publish transaction | Unique request + active pointer CAS; old snapshot giữ nguyên khi lỗi |
| Recovery | FAILED report; resume staged artifact sau revalidation; không auto-delete orphan |
| Local rehearsal/CLI | SQLite + local Parquet; tách scope/prefix khỏi production |
| MinIO adapter | Có code và mock tests; chưa live |
| PostgreSQL metadata | Models + migration; test SQLite, chưa apply migration/live concurrency |
| Production approval/DQ | BLOCKED — G1 pending và evaluator đủ 39 luật chưa có |
| API/scheduler/P6 | OPEN — caller integration và nghiệm thu trên môi trường thật |

Chi tiết và lệnh chạy: [Silver integration P5](silver-integration-p5.md).

Kiểm chứng: **470 passed**, gồm 30 tests P5 và hồi quy S1–S4/readers/handoff.
Ruff đạt; mypy đạt trên 6 module P5. Không chạy PostgreSQL/MinIO live.
