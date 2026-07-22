# Vietlott Data Collector

> **Dự án này đã được hợp nhất và lưu trữ.** Mã nguồn collector, snapshot dữ liệu,
> phân tích, website và các workflow hiện được duy trì tại
> [NhanAZ-Data/vietlott-research](https://github.com/NhanAZ-Data/vietlott-research).
> Vui lòng dùng repo mới cho mọi clone, issue và đóng góp tiếp theo.

Repo này là nguồn dữ liệu canonical cho các kết quả Vietlott công khai. Phạm vi
hiện tại chỉ giữ phần thu thập, chuẩn hóa, kiểm tra và xuất bản snapshot dữ liệu
tự động.

Website dự đoán và phân tích đã được tách sang
[NhanAZ-Data/vietlott-prediction-web](https://github.com/NhanAZ-Data/vietlott-prediction-web).
Repo web tự checkout dữ liệu từ repo này khi build, nên không cần nhân đôi
`datasets` ở phía website.

## Phạm Vi Dữ Liệu

- Mega 6/45
- Power 6/55
- Lotto 5/35
- Max 3D và Max 3D+
- Max 3D Pro
- Max 4D lịch sử
- Keno
- Bingo18

Khi máy chạy GitHub bị Vietlott chặn truy cập, workflow dùng nguồn công khai dự
phòng đã được gắn provenance. Các dòng này được đánh dấu để đối chiếu và thay
thế bằng nguồn chính thức khi nguồn Vietlott truy cập được.

## Cài Đặt

```powershell
git clone https://github.com/NhanAZ-Data/vietlott-data-research.git
cd vietlott-data-research
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## Dùng Dataset

Ghép snapshot phân vùng trong `datasets` thành CSV làm việc:

```powershell
vietlott-repository-data hydrate --source-dir datasets --destination-dir data
```

Các tệp chính:

- `data/draws.csv`: một dòng cho mỗi kỳ quay
- `data/prizes.csv`: thông tin giải thưởng theo kỳ
- `data/prize_rules.csv`: luật trả thưởng có cấu trúc ổn định
- `datasets/exclusions.csv`: các kỳ cần loại khỏi phân tích mặc định
- `datasets/metadata/dataset-summary.json`: thống kê bao phủ
- `datasets/metadata/quality-report.json`: chất lượng, nguồn và độ phủ
- `datasets/metadata/snapshot-manifest.json`: hash và phiên bản snapshot

Kiểm tra snapshot:

```powershell
vietlott-repository-data validate --source-dir datasets
vietlott-repository-data audit --source-dir datasets
```

## Cập Nhật

Keno và Bingo18 được kiểm tra thường xuyên trong khung phát hành. Các sản phẩm
quay theo mốc giờ được kiểm tra nhiều lần sau các mốc dự kiến. Workflow đọc trạng
thái thực tế từ nguồn thay vì tự tạo kỳ theo lịch.

Chạy cập nhật thủ công:

```powershell
vietlott-repository-data hydrate --source-dir datasets --destination-dir data
vietlott-auto-update --products keno bingo18 --output-dir data
vietlott-repository-data publish --source-dir data --destination-dir datasets
vietlott-repository-data validate --source-dir datasets
```

Nếu không có dữ liệu mới thì workflow không tạo commit. Khi một lần chạy bị lỡ,
lần sau tiếp tục đọc nhiều trang cho đến khi gặp vùng dữ liệu đã có.

## Tài Liệu

- [Nguồn và quy trình thu thập](docs/THU_THAP_DU_LIEU.md)
- [Chất lượng dữ liệu và provenance](docs/CHAT_LUONG_DU_LIEU.md)
- [Cơ chế tự động cập nhật](docs/TU_DONG_CAP_NHAT.md)
- [Khảo sát nguồn dữ liệu](docs/SOURCE_SURVEY.md)
- [Kiến trúc collector](docs/ARCHITECTURE.md)

## Pháp Lý Và Trách Nhiệm

Kết quả được lấy từ các trang công khai của Vietlott và một số nguồn đối chiếu
được ghi trong tài liệu kỹ thuật. Repo không liên kết, đại diện hoặc được bảo trợ
bởi Vietlott, MoMo hay đơn vị phát hành nào.

Dữ liệu lịch sử không bảo đảm khả năng dự đoán hoặc trúng thưởng. Nội dung trong
repo chỉ phục vụ mục đích cá nhân, học tập và nghiên cứu dữ liệu.

## Giấy Phép

Mã nguồn được phát hành theo giấy phép MIT. Dữ liệu gốc vẫn chịu các quyền và
điều khoản của nguồn công bố ban đầu.
