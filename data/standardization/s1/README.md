# Fixtures S1

`expected-business-records.json` được viết tường minh: một bản ghi cho mỗi 22 entity, nối PK/FK liên domain. Chỉ dữ liệu giả lập. Đây là expected projection Silver, không phải dữ liệu nguồn hay output của engine. Không ghi đè data/samples hoặc Bronze.

Test bổ sung audit envelope giả lập, validate JSON Schema + FK/unique/Decimal bounds, rồi tạo các mutation thiếu field, sai kiểu, extra field, khóa trùng, FK thiếu, enum lạ và ngày sai. Birth_year được ghi sẵn độc lập converter. Fixtures không chứng minh 39 luật nghiệp vụ nguồn đã đạt; department root Faculty chỉ dùng kiểm tra contract/FK, quy tắc cây tổ chức vẫn phải review trước acceptance nghiệp vụ.

Ví dụ input/output normalization ba domain và lỗi parser có sẵn ở `../p0/cases.json`. S2 cần bổ sung hai layout nguồn mỗi domain và source snapshots đầy đủ; không lấy fixtures reader applicant_id/full_name/status làm schema chính thức.

Lệnh từ backend:

```powershell
..\.venv\Scripts\python.exe -m pytest tests/test_silver_contracts.py -o addopts='' -q
```

Validator cần pytest và jsonschema trong môi trường test. Không bật ETL, không ghi MinIO/PostgreSQL. Pass ở đây là chất lượng contract S1, không phải Definition of Done của toàn story.
