# Hướng dẫn xử lý Silver hiện hành

Luồng dữ liệu mẫu và các công cụ chạy minh họa trước đây đã được gỡ. Dữ liệu nguồn thật được xử lý theo cấu hình riêng trên máy chủ.

Xem [luồng nguồn thật → Silver](source-silver-flow.md) để cấu hình nguồn, đối chiếu mã, gọi API và tra cứu kết quả.

Kiểm tra các chức năng liên quan:

```sh
.venv/bin/python -m pytest --no-cov -q tests/test_source_cleaning.py tests/test_silver_publication.py tests/test_cleaning_pipeline.py tests/test_quarantine_records.py tests/test_lineage_api.py
```

Các kiểm thử dùng dữ liệu riêng, không kết nối hoặc sửa dữ liệu đang vận hành.
