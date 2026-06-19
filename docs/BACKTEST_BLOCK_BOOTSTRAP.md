# Block bootstrap cho backtest walk-forward

Tài liệu này khóa cách đọc `block_bootstrap_check` trong các report backtest.
Mục tiêu là so sánh khoảng bất định từ xấp xỉ chuẩn hiện tại với khoảng bootstrap
giữ cấu trúc thời gian cục bộ.

## Phạm vi áp dụng

Mỗi report complete gắn `block_bootstrap_check` vào:

- `comparison`;
- `recent_comparison`;
- `audit_comparison`;
- từng dòng trong `multiple_testing_trials.trials`.

Trường này là chẩn đoán bổ sung. Nó không thay `approximate_p_value`,
`q_value_global_bh`, `beats_baseline` hoặc trạng thái trong
`trial_disposition_log`.

## Cách bootstrap

Backtest tạo chuỗi chênh lệch theo kỳ:

```text
d_t = điểm_chiến_lược_t - điểm_kỳ_vọng_đồng_đều_t
```

`block_bootstrap_check` resample các block kỳ liên tiếp từ chuỗi `d_t`, tính lại
trung bình chênh lệch trên từng mẫu bootstrap, rồi công bố khoảng 95%.

Các giá trị đã khóa:

- `method = moving_block_bootstrap`.
- `resamples = 199`.
- `confidence_level = 0.95`.
- `preserve_time_structure = contiguous_observation_blocks`.
- `block_length = max(4, min(50, round(sqrt(n))))`.
- chuỗi dài hơn 2.500 kỳ dùng `sampling_method = deterministic_even_spacing`.
- `no_multiple_testing_decision = true`.

## So với xấp xỉ chuẩn

Mỗi `block_bootstrap_check` có `normal_approximation` gồm:

- `method = paired_normal_mean_interval`;
- `standard_error`;
- `confidence_interval_lower`;
- `confidence_interval_upper`.

Trường `interval_overlap_with_normal_approximation` cho biết khoảng bootstrap có
giao với khoảng chuẩn trên cùng chuỗi chẩn đoán hay không. Đây chỉ là tín hiệu
đọc độ bền, không phải tiêu chí thắng/thua.

## Validation

`build_backtest_report` validate mọi comparison và mọi trial registry có
`block_bootstrap_check`. `finalize_backtests` công bố tổng quan tại
`manifest.backtest_summary.block_bootstrap_validation`, gồm số check comparison,
số check trial, số check khả dụng và số khoảng bootstrap giao với khoảng chuẩn.
