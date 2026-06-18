# Phạm vi kỳ mục tiêu chung của backtest

Tài liệu này khóa cách đọc `backtest.target_scope` trong từng báo cáo sản phẩm.
Mục tiêu của `BACKTEST-002` là đảm bảo mọi chiến lược trong cùng một sản phẩm
được chấm trên đúng cùng tập kỳ mục tiêu, cùng baseline theo kỳ và cùng phép so
sánh ghép cặp.

## Quy tắc

Backtest dùng walk-forward. Tại kỳ mục tiêu `t`, thuật toán chỉ nhìn các kỳ
trước `t`; sau khi chấm xong, kỳ `t` mới được đưa vào lịch sử cho bước tiếp
theo.

Mỗi báo cáo có `target_scope` gồm:

- `scope_id`: 24 ký tự đầu của SHA-256 danh sách kỳ mục tiêu.
- `target_draw_count`: số kỳ mục tiêu trong cửa sổ backtest.
- `first_target_draw_id` và `latest_target_draw_id`: biên đầu cuối của cửa sổ.
- `target_draw_ids_sha256`: SHA-256 đầy đủ của chuỗi mã kỳ và ngày quay.
- `sample_target_draw_ids`: vài mã kỳ đầu và cuối để đọc nhanh.
- `shared_by`: các chiến lược và baseline bắt buộc dùng chung scope này.
- `no_strategy_specific_filtering`: luôn là `true`.

Các khối `model`, `recent_model`, `audit_model`, `baseline`, `comparison`,
`recent_comparison` và `audit_comparison` đều phải có cùng `target_scope_id` và
`target_draw_count` với `target_scope`.

## Kiểm soát tự động

`finalize_backtests` gọi kiểm tra target scope trước khi hiệu chỉnh
Benjamini-Hochberg. Nếu một chiến lược hoặc comparison bị thiếu, lệch
`target_scope_id` hoặc lệch `target_draw_count`, lệnh tạo báo cáo sẽ dừng bằng
lỗi thay vì công bố kết quả không cùng mẫu.

`manifest.backtest_summary.target_scope_validation` tóm tắt trạng thái đã kiểm
tra cho toàn bộ sản phẩm. Trường này không đổi công thức điểm, không đổi
p-value, q-value hoặc kết luận; nó chỉ khóa mẫu so sánh để người đọc biết mọi
chiến lược được đánh giá trên cùng kỳ mục tiêu.
