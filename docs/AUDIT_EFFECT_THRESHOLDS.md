# Ngưỡng độ lớn hiệu ứng của bộ kiểm định công bằng

Tài liệu này khóa cách đọc `practical_effect_threshold` trong fairness audit. Một
p-value nhỏ chỉ nói dữ liệu khó xảy ra nếu mô hình nền đúng. Ngưỡng độ lớn hiệu
ứng hỏi câu khác: sai lệch có đủ lớn để đáng theo dõi trong thực tế hay không.

Các ngưỡng dưới đây là ngưỡng sàng lọc, không phải bằng chứng gian lận hay kết
luận vận hành. Một tín hiệu chỉ được xem là mạnh khi vừa đạt ngưỡng độ lớn, vừa
vượt hiệu chỉnh nhiều kiểm định và còn tái lập được ngoài mẫu.

## Bảng ngưỡng đã khóa

| Mã | Đơn vị | Ngưỡng | Phạm vi áp dụng | Lập luận hoặc tham khảo |
| --- | --- | ---: | --- | --- |
| `cohen_w_0_05` | `w = sqrt(chi_square / pooled category observations)` | 0,05 | Kiểm định phân bố biên cho tập số và chuỗi chữ số | Cohen thường xem `w = 0,10` là hiệu ứng nhỏ. Dự án dùng `0,05` như ngưỡng sàng lọc nhạy hơn vì sai lệch xổ số, nếu có, được kỳ vọng rất nhỏ và vẫn phải vượt hiệu chỉnh nhiều kiểm định. |
| `likelihood_w_0_05` | `w = sqrt(g_statistic / pooled category observations)` | 0,05 | G-test cho cùng câu hỏi phân bố biên với chi-square | Dùng cùng mốc với Cohen's w để hai phép kiểm cùng họ không tạo hai tiêu chuẩn thực dụng khác nhau cho cùng một sai lệch phân bố. |
| `absolute_z_per_sqrt_n_0_10` | `abs(z) / sqrt(n)` | 0,10 | Runs test trên chuỗi tổng bộ số hoặc giá trị chuỗi | Chuẩn hóa z theo căn cỡ mẫu để mẫu rất lớn không tự biến sai lệch nhỏ thành tín hiệu thực dụng. |
| `absolute_correlation_0_05` | `abs(r)` | 0,05 | Tự tương quan lag-1 của tổng bộ số hoặc giá trị chuỗi | Tương quan `0,05` là hiệu ứng rất nhỏ theo thang r, nhưng vẫn đáng theo dõi nếu ổn định ngoài mẫu và vượt hiệu chỉnh. |
| `standardized_mean_difference_0_15` | `abs(mean_2 - mean_1) / pooled_sd` | 0,15 | Quét change-point trên các điểm cắt lịch sử đã đăng ký trước | Mốc này thấp hơn quy ước small-effect `0,20` để cảnh báo sớm, nhưng p-value chính phải hiệu chỉnh việc tìm nhiều điểm ứng viên. |
| `cramers_style_w_0_05` | `w = sqrt(chi_square / stratified observations)` | 0,05 | Kiểm định dị biệt theo tháng cho số hoặc chữ số | Dùng cùng mốc sàng lọc với kiểm định chi-square phân bố vì đây vẫn là độ lệch chuẩn hóa từ bảng phân loại. |
| `gap_ratio_4_0` | `current_gap_draws / expected_gap_draws` | 4,00 | Số đang vắng lâu nhất trong sản phẩm chọn tập số | Trong không gian nhiều số luôn có một số đang vắng lâu, nên khoảng vắng phải đạt ít nhất bốn lần kỳ vọng mới được xem là lớn. |
| `pair_co_occurrence_w_0_05` | `w = sqrt(chi_square / pair observations)` | 0,05 | Kiểm định đồng xuất hiện cặp số khi workflow đủ bộ nhớ để chạy | Giữ cùng mốc với kiểm định phân bố, nhưng diễn giải thận trọng vì các cặp trong cùng một kỳ không độc lập hoàn toàn. |
| `odd_count_w_0_10` | `w = sqrt(chi_square / draws)` | 0,10 | Phân bố số lượng số lẻ trong một bộ chọn không lặp | Chẵn-lẻ là đặc trưng tổng hợp thô nên yêu cầu mốc cao hơn để tránh báo tín hiệu từ dao động nhỏ. |
| `position_digit_w_0_05` | `w = sqrt(chi_square / position-digit observations)` | 0,05 | Kiểm định chữ số theo vị trí cho Max 3D, Max 3D Pro, Max 4D và Bingo18 | Đây là tín hiệu đang cần tái kiểm tra ngoài mẫu nên giữ ngưỡng nhạy, nhưng không tách từng ô thành kiểm định mới trên cùng dữ liệu. |
| `digit_sum_w_0_10` | `w = sqrt(chi_square / outcomes)` | 0,10 | Phân bố tổng chữ số của sản phẩm chuỗi chữ số | Tổng chữ số gộp nhiều cấu hình khác nhau, vì vậy chỉ đánh dấu khi sai lệch tổng hợp đủ lớn. |
| `repeat_pairs_ratio_1_25` | `observed duplicate pairs / expected duplicate pairs` | 1,25 | Tỷ lệ chuỗi kết quả lặp trong không gian hữu hạn | Chuỗi lặp là bình thường trong không gian hữu hạn; chỉ khi số cặp lặp cao hơn kỳ vọng ít nhất 25% mới theo dõi. |

## Phân tích độ nhạy

Mỗi lần tạo báo cáo, `site/data/audit-summary.json` lưu
`threshold_sensitivity` với các hệ số `0,5`, `1,0`, `1,5` và `2,0` so với ngưỡng
đã khóa. Bảng này trả lời hai câu hỏi:

- Nếu nới ngưỡng xuống một nửa, có bao nhiêu phép kiểm sẽ trở thành
  `practically_large`?
- Nếu siết ngưỡng lên `1,5x` hoặc `2,0x`, còn bao nhiêu phép kiểm vẫn đủ lớn?

Phân tích độ nhạy được tính cho toàn hệ thống và cho từng loại ngưỡng. Cách này
giúp đọc được mức mong manh của kết luận mà không đổi luật sau khi đã nhìn dữ liệu.

## Điều kiện đổi ngưỡng

Chỉ đổi ngưỡng khi có một trong các lý do sau:

- Phát hiện công thức hiệu ứng đang sai hoặc dùng sai mẫu số.
- Có mô phỏng đã đăng ký trước cho thấy ngưỡng hiện tại quá nhạy hoặc quá trơ.
- Có thay đổi cấu trúc dữ liệu làm đơn vị hiệu ứng không còn cùng nghĩa.
- Có tài liệu phương pháp tốt hơn và được ghi vào changelog.

Mọi lần đổi phải cập nhật `EFFECT_THRESHOLD_REGISTRY`, tài liệu này,
`docs/METHODOLOGY_CHANGELOG.md`, test liên quan và artifact website.
