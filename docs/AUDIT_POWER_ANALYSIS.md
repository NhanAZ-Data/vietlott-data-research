# Phân tích công suất của fairness audit

Tài liệu này khóa cách đọc `power_analysis` trong từng phép kiểm fairness audit.
Mục tiêu là trả lời hai câu hỏi trước khi diễn giải kết quả âm:

- Với mẫu hiện tại, hiệu ứng nhỏ nhất có thể phát hiện ở công suất đã khóa là bao nhiêu?
- Ngưỡng độ lớn thực dụng đã khóa trước có nằm trong vùng đủ công suất hay không?

## Phương pháp

`power_analysis` dùng xấp xỉ chuẩn hai phía với `alpha = 0,05`. Các mức công suất
được công bố là `0,8` và `0,9`. Với thang hiệu ứng chuẩn hóa quanh null, hiệu ứng
tối thiểu xấp xỉ là:

```text
MDE = (z_(1 - alpha / 2) + z_power) / sqrt(n_hiệu_dụng)
```

`n_hiệu_dụng` không luôn bằng số kỳ quay hiển thị. Ví dụ:

- kiểm định tần suất pooled dùng số lượt số hoặc chữ số quan sát được;
- kiểm định vị trí chữ số dùng `outcomes × số vị trí`;
- change-point scan dùng cỡ mẫu hiệu dụng của hai đoạn tại ứng viên mạnh nhất;
- repeat-rate dùng số cặp lặp kỳ vọng.

## Trường dữ liệu

Mỗi test active có `power_analysis` gồm:

- `status`: `available`, `insufficient_sample` hoặc `unsupported_scale`.
- `method`: hiện là `normal_approximation`.
- `alpha`, `tail`, `primary_power`.
- `effective_sample_size`: mẫu hiệu dụng dùng để tính công suất.
- `null_effect_size`: hiệu ứng tại giả thuyết null, thường là `0`; riêng tỷ lệ
  lặp chuỗi dùng null `1`.
- `observed_effect_delta`: khoảng cách từ hiệu ứng quan sát đến null.
- `observed_power`: công suất xấp xỉ tại hiệu ứng quan sát.
- `practical_threshold_delta`: khoảng cách từ ngưỡng thực dụng đến null.
- `practical_threshold_detectable_at_primary_power`: ngưỡng thực dụng có phát
  hiện được ở công suất 80% với mẫu hiện tại hay không.
- `target_powers`: các hàng MDE cho công suất 80% và 90%, gồm
  `minimum_detectable_effect_delta`, `minimum_detectable_effect` và
  `sample_size_needed_for_practical_threshold`.

Audit cấp sản phẩm và toàn hệ thống có `power_summary`, đếm số phép kiểm có thang
hỗ trợ và số phép kiểm mà mẫu hiện tại đủ để phát hiện ngưỡng thực dụng ở công
suất 80%.

## Giới hạn

Đây là phân tích công suất mô tả, không tạo p-value mới và không thay đổi quyết
định thống kê. Các thang cực trị như `gap divided by expected gap` được đánh dấu
`unsupported_scale`, vì khoảng vắng lớn nhất cần mô hình phân phối cực trị riêng
thay vì công thức chuẩn theo căn cỡ mẫu.

Nếu một test không nổi bật nhưng `practical_threshold_detectable_at_primary_power`
là `false`, kết luận đúng là mẫu hiện tại chưa đủ nhạy để bác bỏ hiệu ứng nhỏ ở
ngưỡng đã khóa. Nếu trường này là `true`, kết quả âm mạnh hơn, nhưng vẫn phụ thuộc
giả định kiểm định, chất lượng nguồn và hiệu chỉnh nhiều phép thử.
