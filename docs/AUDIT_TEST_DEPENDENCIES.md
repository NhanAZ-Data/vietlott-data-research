# Ma trận phụ thuộc giữa các phép kiểm

Tài liệu này khóa cách đọc `dependency_matrix` và
`q_value_dependency_family_bh` trong fairness audit. Mục tiêu là làm rõ rằng
nhiều phép kiểm không độc lập hoàn toàn, nhất là khi chúng dùng cùng dữ liệu kỳ
quay hoặc cùng câu hỏi thống kê.

## Vì sao cần công bố

Chi-square và G-test cho tần suất biên thường cùng hỏi một câu: các số hoặc chữ
số có lệch khỏi phân bố đều hay không. Runs test, tự tương quan lag-1 và
change-point scan cũng cùng đọc một chuỗi theo thứ tự thời gian. Nếu xem mọi p-value như
những bằng chứng độc lập, người đọc có thể đánh giá quá mạnh một tín hiệu lặp lại
từ cùng lát dữ liệu.

## Các trường trong JSON

Mỗi phép kiểm trong `site/data/audit-summary.json`, JSON sản phẩm và
`site/data/audit-log.jsonl` có thêm các trường

- `dependency_family`: họ dữ liệu dùng để hiệu chỉnh bổ trợ
- `dependency_family_label`: nhãn tiếng Việt của họ phụ thuộc
- `dependency_cluster`: cụm câu hỏi gần như trùng nhau
- `dependency_tags`: các lát dữ liệu dùng chung
- `dependency_data_view`: mô tả ngắn cách nhìn dữ liệu
- `q_value_dependency_family_bh`: q-value Benjamini-Hochberg trong riêng họ phụ
  thuộc của sản phẩm

`dependency_matrix` liệt kê từng cặp phép kiểm trong cùng sản phẩm. Mỗi cặp có
`dependency_strength`:

- `high`: cùng lát dữ liệu và cùng câu hỏi gần như trùng nhau
- `medium`: cùng họ phụ thuộc nhưng câu hỏi khác nhau
- `low`: cùng miền sản phẩm nhưng nhìn đặc trưng khác nhau

## Quy tắc hiệu chỉnh

Fairness audit công bố ba mức q-value:

- `q_value_bh`: hiệu chỉnh trong toàn bộ phép kiểm của một sản phẩm
- `q_value_dependency_family_bh`: hiệu chỉnh trong riêng họ phụ thuộc của sản
  phẩm, dùng để đọc chẩn đoán
- `q_value_global_bh`: hiệu chỉnh toàn hệ thống sau khi gom mọi sản phẩm, là q
  chính cho trạng thái thống kê khi báo cáo đã được finalize

Trạng thái `statistically_notable`, `both` và kết luận toàn hệ thống vẫn ưu tiên
`q_value_global_bh`. q theo họ phụ thuộc không làm một phép kiểm tự động đổi
trạng thái; nó chỉ cho thấy tín hiệu đó có còn nổi bật khi so với các phép kiểm
dùng chung lát dữ liệu hay không.

## Diễn giải an toàn

Một cặp `high` như `number_marginal_chi_square` và `number_marginal_g_test` không
nên được kể như hai phát hiện độc lập. Nếu cả hai cùng nổi bật, cách đọc đúng là
cùng một câu hỏi tần suất biên có hai phép đo thống nhất với nhau. Ngược lại, một
cặp `low` chỉ nhắc rằng mọi phép kiểm của cùng sản phẩm đều chia sẻ lịch sử kỳ
quay, nhưng không nhất thiết phản ánh cùng loại sai lệch.

Ma trận này là metadata phương pháp, không phải ma trận tương quan ước lượng từ
dữ liệu. Muốn kết luận tương quan thực nghiệm giữa thống kê kiểm định cần mô
phỏng hoặc bootstrap riêng, được để lại cho các mục nghiên cứu bền vững hơn.
