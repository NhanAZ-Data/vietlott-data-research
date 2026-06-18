# Permutation check giữ nguyên cấu trúc từng kỳ

`permutation_check` là lớp kiểm tra bền vững bổ sung cho các phép kiểm fairness audit
phụ thuộc thứ tự thời gian. Mục tiêu là so sánh thống kê quan sát với null model
tráo thứ tự các đơn vị quan sát đã có, nhưng không phá cấu trúc bên trong từng kỳ
hoặc từng kết quả.

## Phạm vi áp dụng

Permutation check hiện gắn với các phép kiểm:

- `number_sum_runs`
- `number_sum_lag1_autocorrelation`
- `number_sum_split_half_change`
- `digit_value_runs`
- `digit_value_lag1_autocorrelation`
- `digit_sum_split_half_change`

Hai test id có hậu tố `split_half_change` hiện dùng change-point scan nhiều điểm
ứng viên từ `AUDIT-013`; id cũ được giữ để ổn định schema và dashboard.

Với sản phẩm tập số, đơn vị hoán vị là `whole_draw_sum`: tổng của toàn bộ bộ số
trong một kỳ. Các số trong cùng kỳ không bị tráo lẫn với kỳ khác. Với sản phẩm chuỗi
chữ số, đơn vị hoán vị là `whole_digit_value` hoặc `whole_digit_sum`, tức mỗi kết quả
hoặc tổng chữ số của kết quả được giữ nguyên như một khối.

## Trường JSON

Mỗi test được hỗ trợ có `parameters.permutation_check` gồm:

- `status`: `available` khi đã chạy được.
- `method`: luôn là `whole_observation_label_permutation`.
- `permutations`: số lần hoán vị, hiện khóa ở `499`.
- `seed`: seed tái lập sinh từ test id và hash chuỗi giá trị.
- `statistic_name`: thống kê dùng để so sánh, ví dụ `z_score` hoặc `autocorrelation`.
- `observed_statistic`: thống kê quan sát trên cùng tập giá trị dùng cho hoán vị.
- `empirical_p_value`: p-value thực nghiệm hai phía, tính bằng `(extreme + 1) / (permutations + 1)`.
- `preserve_unit`: đơn vị được giữ nguyên khi tráo thứ tự.
- `full_value_count`: số đơn vị quan sát gốc.
- `permutation_value_count`: số đơn vị thực sự đưa vào vòng hoán vị.
- `sampling_method`: `full_sequence` hoặc `deterministic_even_spacing`.
- `no_multiple_testing_decision`: luôn `true`.

## Giới hạn và cách đọc

Permutation check không thay `p_value`, `q_value_bh`, `q_value_global_bh` hoặc `status`
của phép kiểm chính. Nó là kiểm tra chẩn đoán để xem kết luận xấp xỉ chuẩn có quá phụ
thuộc vào công thức hay không.

Với chuỗi rất dài, workflow dùng tối đa 5.000 đơn vị lấy theo khoảng đều quyết định
sẵn để giữ thời gian chạy ổn định cho lịch tự động hằng ngày. Trường
`permutation_value_count` và `sampling_method` công bố rõ khi điều này xảy ra.

Website hiển thị `Permutation p` trong chi tiết từng phép kiểm được hỗ trợ. Dòng này
đồng thời ghi rõ đây là hoán vị nguyên đơn vị và không đổi q/status chính.
