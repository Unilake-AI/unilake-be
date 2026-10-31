# Fixture P0

Dữ liệu hoàn toàn giả lập và tách khỏi `data/samples/`. `cases.json` gồm ba ca hợp lệ (mỗi domain một entity), bốn ca lỗi và bốn tình huống identity/FK. ID 1001/2001/3001 được cấp sẵn chỉ cho fixture; không phải thuật toán cấp ID production.

Expected outputs được viết tường minh, không tạo bằng converter cần kiểm thử. JSON biểu diễn Decimal/date bằng chuỗi; `decimal_fields` / `date_fields` chỉ rõ kiểu mà engine S3 phải trả và writer P5 phải lưu. Không đổi Decimal thành float.

Ca lỗi kế thừa input/rules của base_case, áp dụng input_patch hoặc remove_input_fields. Các record ACCEPT trong batch có lỗi vẫn chưa được publish theo strict_snapshot. Trường description được bỏ theo đề xuất PII/free-text; các field audit được kiểm tra riêng tại S1/P5, chưa nằm trong ví dụ projection này.

Đây là starter acceptance set cho P0, không phải kiểm thử runtime đã đạt. S1 bổ sung mọi field/entity, snapshot liên domain; S2 bổ sung hai cấu trúc nguồn/domain; S3/S4 bổ sung ranh giới kiểu, collision và concurrency; P6 thực thi toàn bộ trên storage thật.
