# Đề xuất nghiệp vụ kết quả xét tuyển và xác nhận nhập học

Trạng thái: **đã được chủ dự án chấp thuận triển khai ngày 18/11/2026**.
Hai nghiệp vụ được triển khai ở BE. Hướng dẫn sử dụng, API, migration và thông tin
cần có để nghiệm thu thực tế nằm tại [admissions-workflows.md](admissions-workflows.md).

Hai thao tác này khác hồ sơ đăng ký ban đầu. Cùng một đăng ký có thể được xét tuyển,
sau đó thí sinh mới quyết định nhập học. Đậu không có nghĩa đã nhập học.

## 1. Dữ liệu và khóa đang có

`app.admission` có `admission_score`, `admission_status` và khóa duy nhất
`(applicant_id, program_id, admission_year_id, admission_method_id)`.
Schema nguồn khai báo Applied, Accepted, Rejected, Waitlisted; bộ ghi hiện chuẩn hóa
trạng thái thành chữ hoa trong PostgreSQL. Chưa có trường riêng lưu quyết định nhập học.

Đề xuất giữ nguyên khóa này, phù hợp xác nhận của chủ dự án. Cả hai luồng chỉ tìm
đăng ký đã có, không tạo thêm applicant, program, phương thức, năm tuyển sinh hay student.
Không xác định thí sinh bằng họ tên.

File dùng mã thí sinh ổn định trong đúng nguồn, mã chương trình, năm tuyển sinh và mã
phương thức để tra đăng ký. Đổi nguồn phải có quy trình đối soát định danh; không tự
ghép thí sinh theo tên hoặc CCCD.

## 2. File cập nhật kết quả xét tuyển

Đề xuất chỉ được sửa `admission.admission_score` và `admission.admission_status`.
Các mã định danh và thông tin hồ sơ dùng để đối soát, không được sửa qua nghiệp vụ này.

| Trạng thái hiện tại | Trạng thái cho phép của STAFF |
| --- | --- |
| APPLIED — đã đăng ký | WAITLISTED, ACCEPTED, REJECTED |
| WAITLISTED — chờ xét | ACCEPTED, REJECTED |
| ACCEPTED — trúng tuyển | Giữ nguyên |
| REJECTED — không trúng tuyển | Giữ nguyên |

Nhập lại cùng điểm và trạng thái là giữ nguyên. Khi đã có kết quả cuối cùng, STAFF
không được tự đổi điểm hoặc đổi kết quả. Đề xuất cho ADMISSIONS_MANAGER mở yêu cầu
đính chính có lý do, phiên bản bản ghi đang sửa và lịch sử trước/sau; ADMIN có quyền
xử lý tương đương. Đính chính không đi qua upload đăng ký ban đầu.

Điểm phải là số thập phân tối đa hai chữ số sau dấu phẩy và nằm trong
`[0, admission_method.max_score]`. Nếu phương thức không dùng điểm (`max_score = 0`),
điểm để trống. Với phương thức dùng điểm, phải có điểm trước khi công bố kết quả cuối.
Không tự tính đậu/rớt từ điểm nếu chưa có quy định ngưỡng và ưu tiên được duyệt.

## 3. File xác nhận nhập học

Đề xuất bổ sung hai cột vào `app.admission`, giữ kết quả xét tuyển độc lập:

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| enrollment_status | varchar(30), nullable | PENDING, CONFIRMED, DECLINED, WITHDRAWN |
| enrollment_date | date, nullable | Ngày xác nhận nhập học do file cung cấp |

Dữ liệu cũ để NULL nghĩa là chưa xác định, không tự suy ra đã hoặc chưa nhập học.
Khi kết quả được chuyển sang ACCEPTED theo luồng mới, hệ thống đặt PENDING theo
quy tắc đã duyệt. Chỉ đăng ký ACCEPTED mới được xác nhận nhập học.

| Trạng thái hiện tại | Thao tác đề xuất |
| --- | --- |
| PENDING | STAFF được đổi sang CONFIRMED hoặc DECLINED |
| CONFIRMED | Giữ nguyên khi nhập lại; Manager có thể chuyển WITHDRAWN kèm lý do |
| DECLINED | Manager có thể đính chính sang CONFIRMED kèm lý do |
| WITHDRAWN | Manager phải mở yêu cầu khôi phục riêng, không tự nhận lại từ file STAFF |
| NULL của dữ liệu cũ | Đối soát hồ sơ trước, không tự coi là PENDING |

CONFIRMED bắt buộc có enrollment_date hợp lệ. Khi rút hồ sơ, giữ ngày xác nhận cũ
trong bản ghi/lịch sử; không thay bằng ngày upload. Tạo student và cấp mã sinh viên
vẫn là nghiệp vụ riêng, cần kiểm tra chương trình và hồ sơ đã nhập học.

## 4. Thay đổi DB và kiểm soát đồng thời đề xuất

Migration sau khi duyệt sẽ thêm hai cột trên, CHECK danh sách enrollment_status,
CHECK yêu cầu enrollment_date cho CONFIRMED và chỉ mục phù hợp truy vấn theo
trạng thái nhập học. Không backfill một trạng thái nghiệp vụ khi chưa đối soát.

Đề xuất thêm `revision bigint NOT NULL DEFAULT 1` vào admission để kiểm soát sửa
đồng thời. File/cập nhật mang expected_revision; DB chỉ ghi khi phiên bản còn khớp.
Mỗi thay đổi tăng revision. Việc thêm revision phải tích hợp đồng bộ tất cả bộ ghi
admission đang được phép hoạt động, không chỉ hai endpoint mới.

Khóa hàng được giữ trong transaction, kiểm tra lại kết quả/điều kiện nhập học trước
khi cập nhật. Audit lưu người thao tác, lý do, nguồn file, dòng/cột, phiên bản và
giá trị trước/sau. Tái sử dụng lịch sử import, bổ sung bằng chứng phê duyệt cho
đính chính của Manager. Một file có lỗi thì rollback toàn bộ thao tác nghiệp vụ.

## 5. Các tình huống nghiệm thu sau khi duyệt

1. Đăng ký APPLIED có thể được cập nhật kết quả hợp lệ và nhập lại không đổi số dòng.
2. STAFF không đổi kết quả đã công bố; yêu cầu đính chính của Manager có đủ lịch sử.
3. Thiếu đăng ký, sai mã phương thức, điểm vượt thang hoặc chuyển trạng thái sai
   đều báo đúng dòng/cột và không ghi một phần file.
4. Chỉ ACCEPTED mới được xác nhận nhập học; thiếu ngày xác nhận thì bị chặn.
5. Hai lượt sửa cùng phiên bản: một lượt thành công, lượt còn lại báo xung đột.
6. Đậu hoặc xác nhận nhập học không tự tạo student.
7. PNS/PDT không sử dụng được các thao tác thuộc PTS.

## 6. Phần đã được chủ dự án chấp thuận

Đã chấp thuận ba nhóm: bảng chuyển trạng thái và quyền đính chính; hai trường nhập
học riêng cùng cách xử lý dữ liệu cũ; kiểm soát revision và yêu cầu điểm/ngày xác nhận.
BE đã có migration và hai nghiệp vụ `admissions_results`, `admissions_enrollment`.
