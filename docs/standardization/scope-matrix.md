# Scope matrix P0

Nguồn được pin bằng SHA-256 tại `evidence/schema-inventory.json`. Đây là inventory nguồn, chưa phải Silver schema được duyệt.

| Domain | Entity (một dòng theo PK) | Version nguồn | PK | Sample legacy |
|---|---|---|---|---|
| admissions | major | 1.3.0 | major_id | Lệch header |
| admissions | program | 1.3.0 | program_id | Lệch header |
| admissions | admission_method | 1.3.0 | admission_method_id | Lệch header |
| admissions | admission_year | 1.3.0 | admission_year_id | Lệch header |
| admissions | applicant | 1.3.0 | applicant_id | Lệch header |
| admissions | admission | 1.3.0 | admission_id | Lệch header |
| academic | student | 1.2.0 | student_id | Lệch header |
| academic | course | 1.2.0 | course_id | Lệch header |
| academic | curriculum | 1.2.0 | curriculum_id | Thiếu file |
| academic | curriculum_course | 1.2.0 | curriculum_course_id | Thiếu file |
| academic | semester | 1.2.0 | semester_id | Lệch header |
| academic | class | 1.2.0 | class_id | Lệch header |
| academic | class_lecturer | 1.2.0 | class_lecturer_id | Thiếu file |
| academic | enrollment | 1.2.0 | enrollment_id | Thiếu file |
| academic | enrollment_grade | 1.2.0 | enrollment_grade_id | Lệch header |
| hr | department | 1.2.0 | department_id | Lệch header |
| hr | position | 1.2.0 | position_id | Khớp header |
| hr | qualification | 1.2.0 | qualification_id | Khớp header |
| hr | employee | 1.2.0 | employee_id | Khớp header |
| hr | lecturer | 1.2.0 | employee_id | Lệch header |
| hr | employee_qualification | 1.2.0 | employee_id, qualification_id | Khớp header |
| hr | assignment | 1.2.0 | assignment_id | Lệch header |

Ownership theo schema hiện tại: Admissions 6, Academic 9, HR 7. Entity tham chiếu không được tính thêm hoặc tạo bản sao owner. `cross-domain-links.csv` liệt kê toàn bộ FK, gồm cả quan hệ nội domain; đây là structural mapping, không phải crosswalk mã giữa nguồn.

Trong phạm vi: 22 entity, cấu trúc/kiểu/ID/enum, lỗi và lineage, công bố Silver. Ngoài phạm vi story: Gold/KPI/NLQ, đồng bộ hai chiều về hệ nguồn và fuzzy matching tự động.

S1 phải chốt physical type, PII projection, enum/precision/timezone, nullable/unique và mapping từng field. Không coi số dòng hoặc header đúng là bằng chứng dữ liệu hợp lệ.
