# S1 — Quan hệ và thứ tự xử lý

Danh sách machine-readable đủ 28 FK: `silver-cross-domain-links.csv`. Ownership giữ theo schema nguồn, không tạo department/program bản sao cho từng domain.

## Nhóm quan hệ cần giữ

- HR department → Admissions major → program → Academic student/curriculum.
- Admissions applicant → admission → Academic student. applicant_id/admission_id trên student có thể null cho nguồn legacy. Khi có admission_id, phải cùng applicant/program/admission_year và hồ sơ Accepted (luật nghiệp vụ cần thực thi).
- HR employee → lecturer (shared PK employee_id) → Academic class_lecturer. lecturer_id trên class_lecturer tham chiếu employee_id của lecturer, không phải một lecturer_id giả lập mới.
- Academic student + class → enrollment → enrollment_grade. class liên kết course/semester; curriculum_course liên kết curriculum/course/prerequisite_course.
- HR employee + department + position → assignment; employee + qualification → employee_qualification (composite PK).

## Thứ tự snapshot đề xuất

1. department (cha trước con), position, qualification, admission_method, admission_year, applicant, course, semester, employee.
2. major, lecturer, employee_qualification, assignment.
3. program.
4. admission, curriculum, class.
5. student, curriculum_course, class_lecturer.
6. enrollment, rồi enrollment_grade.

Department tự tham chiếu: kiểm tra cycle và resolve cha theo tree, không chỉ theo thứ tự file. Các entity snapshot cha không cập nhật trong run phải pin phiên bản đã published. Không chỉ kiểm tra FK trong từng domain độc lập. Trạng thái FK pending chặn snapshot strict; có thể replay sau khi cha/crosswalk sẵn sàng.

## Crosswalk và source authority

Khóa crosswalk: (namespace ổn định, entity, source-key tuple). Source-key lưu string hoặc tuple có cấu trúc, giữ 0 đầu. Một source identity không được đổi canonical ID giữa retry; nhiều identity nguồn chỉ gộp khi có bằng chứng được duyệt. Không dùng data_source_id làm namespace hệ thống vì cấu hình có thể thay đổi.

D07 về ADM_SYS/ACA_SYS/HR_SYS còn PROPOSED; không tự gán thẩm quyền vì entity owner. Timestamp nguồn không đủ để lựa chọn record xung đột nếu chưa có quy tắc hiệu lực. Bằng chứng mapping phải gồm người/quy tắc xác nhận và effective interval. S1 chưa có dữ liệu thật nên không phát minh crosswalk production.
