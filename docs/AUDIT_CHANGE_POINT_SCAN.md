# Change-point scan nhiều điểm ứng viên

`change_point_scan` thay cách đọc split-half duy nhất trong fairness audit bằng một
lượt quét các điểm cắt đã đăng ký trước. Hai test id cũ vẫn được giữ để không làm
vỡ schema và dashboard:

- `number_sum_split_half_change`
- `digit_sum_split_half_change`

Từ `AUDIT-013`, hai test này không chỉ so nửa đầu với nửa sau. Chúng quét các điểm
cắt lịch sử ở 20%, 30%, 40%, 50%, 60%, 70% và 80% chuỗi quan sát, sau đó chọn ứng
viên có `max_abs_z_score` lớn nhất.

## Cách hiệu chỉnh nhiều điểm

Mỗi ứng viên chia chuỗi thành hai đoạn đủ dài, tính trung bình đoạn sau trừ đoạn
trước, chuẩn hóa bằng sai số chuẩn hai mẫu và lấy p-value hai phía theo xấp xỉ
chuẩn. Vì đã tìm điểm mạnh nhất trong nhiều ứng viên, p-value chính của test là
`adjusted_p_value` theo Bonferroni:

```text
adjusted_p_value = min(1, raw_p_value * candidate_count)
```

Trường `raw_p_value` chỉ giúp kiểm tra và giải thích ứng viên mạnh nhất. Nó không
được dùng làm quyết định chính, không thay q-value và không thay `status`.

## Trường JSON

Mỗi test change-point có `parameters.change_point_scan` gồm:

- `status`: `available` khi quét đủ điều kiện.
- `method`: luôn là `pre_registered_candidate_scan`.
- `candidate_fractions`: danh sách tỷ lệ điểm cắt đã khóa trước.
- `candidate_count`: số điểm cắt thực sự dùng sau khi áp điều kiện độ dài đoạn.
- `minimum_segment_values`: số quan sát tối thiểu của mỗi đoạn.
- `multiple_candidate_correction`: hiện là `bonferroni`.
- `statistic_name`: hiện là `max_abs_z_score`.
- `raw_p_value`: p-value thô của ứng viên mạnh nhất.
- `adjusted_p_value`: p-value đã hiệu chỉnh, dùng làm `p_value` chính của test.
- `strongest_candidate`: điểm cắt mạnh nhất và thống kê hai đoạn.
- `candidates`: toàn bộ ứng viên đã quét.
- `no_unadjusted_search_decision`: luôn `true`.

## Cách đọc trên website

Website hiển thị `Change-point scan` trong chi tiết từng phép kiểm change-point.
Dòng này cho biết số điểm ứng viên, tỷ lệ lịch sử của điểm mạnh nhất, `p thô` và
`p Bonferroni`. Kết luận màu trạng thái vẫn dựa trên p đã hiệu chỉnh và q-value
toàn cục, không dựa trên p thô.
