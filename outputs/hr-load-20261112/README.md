# Bộ dữ liệu HR giả lập theo schema PostgreSQL hiện tại

Tạo ngày 12/11/2026. Chỉ tạo file; không insert DB, không sửa bốn file đầu vào của người dùng. Tất cả tên người/đơn vị được ghi rõ giả lập; email dùng `example.invalid`, phone để trống đúng nullable. Dữ liệu này phục vụ test và chưa phải policy hoặc source profile đã được owner duyệt.

## 1. Chọn bộ để chạy

| Thư mục/file | Records | Ghi chú |
|---|---:|---|
| smoke/csv/employee.csv | 5.000 | Mã NV0001–NV5000, phù hợp pattern mẫu NV + 4 chữ số |
| smoke/xlsx/employee.xlsx | 5.000 | Cùng dữ liệu với CSV smoke, sheet `employee` |
| smoke/csv/assignment.csv | 10.000 | Hai giai đoạn nối tiếp cho mỗi nhân viên |
| smoke/csv/employee_qualification.csv | 10.000 | Hai qualification khác nhau cho mỗi nhân viên |
| load/csv/employee.csv | 100.000 | NV000001–NV100000; cần profile duyệt pattern riêng |
| load/csv/assignment.csv | 200.000 | FK về 100.000 employee trong cùng bundle |
| load/csv/employee_qualification.csv | 200.000 | PK ghép không trùng |
| load/xlsx/employee.xlsx | 20.000 | Subset đầu của employee CSV lớn; sheet `employee` |
| load/xlsx/assignment.xlsx | 50.000 | Subset đầu của assignment CSV lớn; sheet `assignment` |
| load/csv-equivalent-xlsx/*.csv | 20.000 / 50.000 | Bản CSV tương ứng toàn bộ từng XLSX để so sánh parser |

Mỗi bundle smoke/load có thêm `department.csv` 21 dòng, `position.csv` 2 dòng và `qualification.csv` 3 dòng.

**Assignment XLSX 50.000 dòng tham chiếu 25.000 employee.** Vì vậy hãy dùng parent `load/csv/employee.csv` 100.000 dòng; file employee XLSX 20.000 dòng một mình chưa đủ parent. Các CSV đầy đủ tạo thành bộ FK nội bộ đầy đủ.

Hai bundle và các format là những kịch bản thay thế, không phải các phần cần cộng/gộp. PK/email được tái dùng giữa smoke và load, hoặc giữa CSV và XLSX tương ứng. Dùng scope/dataset test riêng để không vô tình tạo duplicate khi append.

## 2. Vì sao không nhân bản trực tiếp file đã có?

Đã đọc header và số dòng của bốn file được cung cấp. File employee cũ có 16 cột dạng phẳng, gồm department/position, salary và manager; bảng `app.employee` thực tế có 9 cột. Các cột như `employment_status`/`work_email` không cùng tên với `employee_status`/`email` của DB. Quan hệ đơn vị/chức vụ được thể hiện bằng bảng `assignment`.

Trong schema DB được truy vấn không có bảng `attendance` và `payroll`. Do đó bộ này không tự tạo thêm bảng hoặc bịa schema payroll/attendance. Các file đó vẫn có thể test upload/Bronze, nhưng để đi tiếp Silver cần định nghĩa entity, contract, mapping và policy phù hợp.

## 3. Schema và dữ liệu

Header giữ đúng tên/thứ tự cột của các bảng HR đã đối chiếu với `information_schema.columns` và `pg_constraint` trên PostgreSQL local:

- employee: employee_id, employee_code, full_name, date_of_birth, gender, email, phone, hire_date, employee_status.
- assignment: assignment_id, employee_id, department_id, position_id, start_date, end_date, is_primary, assignment_status.
- employee_qualification: employee_id, qualification_id, institution, year_obtained.
- department, position, qualification: file parent theo các bảng cùng tên.

DB hiện không áp regex NV + 4 chữ số bằng CHECK constraint. Schema nguồn minh họa `docs/data-schema/hr_v1.schema.json` có pattern đó. Có tối đa 10.000 chuỗi NV0000–NV9999, nên không thể tạo 100.000 mã duy nhất vừa giữ nguyên pattern. Bộ load dùng 6 chữ số và **không được gọi là valid với profile mẫu hiện tại**. Chưa sửa rule ứng dụng hoặc tự approve pattern mới. Bộ smoke chọn 5.000 mã để thử profile pattern hiện tại trước.

Các enum/format trong dữ liệu được chọn theo fixture HR hiện có để thuận tiện thử nghiệm; cần domain owner xác nhận trước sử dụng production. Mỗi nhân viên có hire_date 2015-01-01, assignment cũ kết thúc 2020-12-31, assignment mới bắt đầu 2021-01-01. Không có chồng lấn phân công chính trong dữ liệu được tạo.

ID là **ID nguồn giả lập**, không phải ID đang tồn tại trong DB. Không import trực tiếp vào bảng identity production. FK được đảm bảo giữa các file trong cùng bundle, không tự bảo đảm FK tới dữ liệu đang có trên DB của bạn.

## 4. Định dạng

- CSV: UTF-8, dấu phẩy, có header, newline CRLF, null là ô trống, ngày ISO `YYYY-MM-DD`, boolean `true/false`.
- XLSX: mỗi file một sheet, header dòng 1, ngày là kiểu ngày Excel hiển thị ISO, ID là số, boolean là boolean, null để trống; không có formulas hoặc cột expected thêm vào data.
- Các file đều được kiểm tra dưới 50 MiB. Kích thước thực và SHA-256 nằm trong `manifest.json`.
- Không đổi basename khi đang dùng profile đã bind tên file. CSV và XLSX có source_type/source_table khác nhau, cần profile riêng hoặc revision đã duyệt phù hợp.

## 5. Kiểm chứng và giới hạn

`manifest.json` lưu số records, bytes, SHA-256, parent bundle và kết quả kiểm tra. Kiểm tra toàn CSV: header, required/null, kiểu, độ dài, enum/range, pattern áp dụng (ngoại trừ pattern employee_code bộ load đã nêu), PK/UNIQUE, FK và ngày tháng. Mỗi XLSX được mở lại và đối chiếu tất cả dòng với CSV tương ứng, bao gồm ngày/boolean; kiểm tra không có formula. Các sheet đã được render kiểm tra vùng header và dữ liệu đầu.

Đây là kiểm chứng file, chưa phải ingestion/Silver live acceptance. Chưa tạo data source/dataset, chưa đăng ký crosswalk, chưa approve source profile. Cần ánh xạ ID nguồn và parent theo namespace/crosswalk thực tế. Không suy luận `records_published` từ số records trong file.

## 6. Thứ tự bắt đầu

1. Hoàn tất migration/worker healthy như hướng dẫn BE live.
2. Dùng smoke, bắt đầu `department`, `position`, `qualification`, `employee`, rồi `assignment` và `employee_qualification`. Chuẩn bị crosswalk cho các ID nguồn; với department tự tham chiếu, mapping phải có cả root và các parent cần thiết.
3. Chọn scope test riêng; dựng profile đúng source IDs, parser options, header, contracts và entity. Profile schema không được tự suy từ tên file.
4. Upload một file, chờ ingestion SUCCESS, preview rồi chạy strict. Owner duyệt profile trước publication khi bật staging/production gate.
5. Chỉ khi smoke chạy đúng mới dùng load và pattern employee_code đã được duyệt. Chạy file lớn tuần tự trước khi thử đồng thời; đo memory của BE, worker và thời gian từng bước.
6. Chạy CSV và XLSX tương ứng như hai lần kiểm thử riêng để so sánh kết quả parser. Không gộp chúng thành cùng một batch.

Chi tiết API: `D:/Caps1/unilake-be/docs/testing/cleaning-acceptance/live-backend-guide.md`.
