# OpenCut + AutoTranslateAI

Chạy `../CHAY_DU_AN.bat`, mở http://127.0.0.1:5175/projects và tạo dự án.

1. Bấm **Dịch phim** trên thanh đầu trang.
2. Chọn video từ máy hoặc từ thư viện, chọn ngôn ngữ gốc.
3. Bấm **Dịch sang tiếng Việt**. Có thể bật thuyết minh nếu cần tiếng Việt đọc lời thoại.
4. Khi dịch xong, sub tự vào timeline và hiện trên video. Bấm **Xem lại bản dịch từ đầu** hoặc bấm từng đoạn sub để kiểm tra. Cảnh riêng giữ nguyên video và các cảnh đã chỉnh trước đó.
5. Bấm một đoạn sub để xem trên preview. Sửa nội dung rồi bấm ra ngoài để lưu. Font, màu, vị trí ở bảng bên phải; thời gian chỉnh bằng hai mép đoạn trên timeline.
6. Bấm **Export** để xuất video với sub đã sửa. Có thể nhập SRT/ASS có sẵn để chỉnh mà không cần chạy AI.

Video, cảnh và sub được OpenCut lưu trong dữ liệu trình duyệt của địa chỉ này. Giữ cùng trình duyệt và cổng; xóa dữ liệu trang sẽ xóa dự án.

## Kết nối hiện tại

- Editor Windows: `D:\OpenCut-classic`, cổng **5175**.
- Backend Windows: `D:\RVP\backend`, cổng **8100**.
- 9router hiện có: cổng **20128**. Cần chạy 9router để dịch bằng AI.
- Backend đọc key/model từ `D:\RVP\backend\.env` (được Git bỏ qua). Không đặt key trong mã frontend.
- `RVP_BACKEND_URL` là biến server của OpenCut, mặc định `http://127.0.0.1:8100`.

Bản này là tích hợp web để chỉnh sửa, chưa phải bản đóng gói portable mới.

Nếu dịch vụ thuyết minh không trả âm thanh, có thể bấm **Dùng sub đã dịch, giữ âm thanh gốc** để tiếp tục chỉnh video, không phải nhận diện và dịch lại.

## Chỉnh sub và làm mờ

- Bảng Dịch phim có nút **Sub** và **Làm mờ** ở đầu bảng để đến nhanh các tùy chọn.
- Kéo file SRT/ASS vào vùng nét đứt: phụ đề xuất hiện trên preview và hàng chữ trên timeline.
- Bấm **Áp dụng kiểu cho tất cả sub** để đổi cỡ chữ, màu, nền và vị trí. Muốn sửa riêng: chọn đoạn sub trên timeline rồi chỉnh bên phải hoặc kéo chữ trên preview.
- Sub dịch mới và SRT tự căn theo khung dọc/ngang: ưu tiên 1–2 dòng cân đối, giảm cỡ chữ khi câu dài và giữ lề dưới 5%. Khi sửa nội dung, mép dưới của đoạn sub được giữ nguyên. Câu quá dài vẫn có thể nhiều hơn 2 dòng để chữ không quá nhỏ.
- Với sub cũ bị to hoặc nằm quá cao: mở **Sub**, bấm **Tự căn sub theo video dọc / ngang**. Không cần dịch lại; nội dung và thời gian được giữ nguyên. Bỏ chọn **Tự vừa khung, ưu tiên 1–2 dòng** nếu muốn dùng cỡ chữ cố định. ASS có kiểu riêng giữ kiểu khi nhập, có thể dùng nút tự căn để đổi sang bố cục này.
- **Làm mờ**: chỉnh vị trí tâm, chiều rộng/cao và mức mờ, bấm **Áp dụng vùng làm mờ**. Hiệu ứng có trong video xuất. Bấm **Gỡ vùng làm mờ** để bỏ. Cắt video xong áp dụng lại để lớp mờ theo đúng các đoạn mới.

## Giao diện và chức năng dựng video

- Chọn **Tiếng Việt / English** ở đầu editor hoặc trang danh sách dự án. Ngôn ngữ giao diện được lưu riêng với ngôn ngữ gốc của video.
- **Điều chỉnh**: chọn video/ảnh trên timeline, chỉnh độ sáng, tương phản, bão hòa, nhiệt độ và sắc màu. Dùng **Đặt lại màu** để bỏ bộ chỉnh màu. Có thể áp dụng cho nhiều đoạn đã chọn.
- **Chuyển cảnh**: chọn đoạn, nhập thời gian, dùng **Hiện dần**, **Mờ dần** hoặc **Hiện rồi mờ dần**. Đây là chuyển cảnh qua độ hiện ở mép đoạn, không phải hòa trộn chồng hai video. Ctrl+Z hoàn tác một lần áp dụng; **Gỡ chuyển cảnh** bỏ các mốc do bảng này tạo.
- **Đóng băng khung hình** (biểu tượng bông tuyết): chọn một video và đưa mốc phát vào trong đoạn rồi bấm. Ảnh tĩnh 3 giây được thêm ở lớp trên video, dưới phụ đề; có thể kéo mép để đổi thời lượng. Video gốc giữ nguyên.
- Bộ chỉnh màu và chuyển cảnh có cả trên preview và bản xuất MP4/WebM.

Renderer Rust đã biên dịch nằm tại `rust/wasm/pkg`, được dùng bằng dependency local. Cài dependency như README của OpenCut; không cần biên dịch Rust chỉ để chạy bản web. Khi sửa Rust, chạy `bun run build:wasm` rồi khởi động lại editor để nhận renderer mới.

### Kiểm tra bản nâng cấp

Đã kiểm tra trên Chromium Windows: chuyển Việt/Anh và tải lại trang, lưu chỉnh màu, áp dụng/hoàn tác/gỡ chuyển cảnh, xuất MP4 và tạo khung hình tĩnh. MP4 được kiểm tra bằng khung hình thực tế để xác nhận bão hòa, độ sáng và fade. Hai kiểm thử Rust về thời lượng fade và đoạn ngắn đều đạt.

Các module giao diện mới không có lỗi TypeScript. Kiểm tra toàn repo vẫn còn 41 lỗi có sẵn (chủ yếu khai báo `bun:test`, keybinding và API migration); đây chưa phải bản đạt kiểm tra TypeScript toàn bộ.

## Sub ngắn, mỗi đoạn một dòng

Bảng Dịch phim mặc định bật **Sub ngắn, mỗi đoạn 1 dòng**. Sub dịch mới hoặc SRT/ASS nhập tại bảng này được chia thành các cụm ngắn, tối đa khoảng 7 từ, có xét độ rộng chữ của khung video. Các ô sửa sub hiển thị gọn một hàng.

Với bản dịch đã nhập trước đó, bấm **Chia sub ngắn · 1 dòng** ngay trên danh sách đoạn sub. Nội dung, kiểu chữ/màu và khoảng thời gian tổng được giữ lại; Ctrl+Z hoàn tác cả lần chia. Thời gian các cụm nhỏ được chia tương đối theo độ dài lời trong đoạn gốc, chưa phải thời gian nhận diện từng từ. Có thể kéo mép trên timeline để khớp chính xác hơn.

### Đặt chữ dịch lên vùng che chữ gốc
Trong Dịch phim, mở **Kiểu và vị trí phụ đề** → **Chọn chữ để kéo trên video**. Kéo chữ ngay trong preview; dùng **Dùng vị trí chữ đang chọn cho tất cả sub** để áp dụng vị trí đó cho toàn bộ track phụ đề.

Trong **Làm mờ sub gốc / vùng trên video**, bấm **Áp dụng vùng làm mờ** rồi **Chọn vùng mờ để kéo / đổi kích thước**. Kéo khung tới chữ gốc và kéo các chấm ở cạnh để đổi kích thước. Bấm **Đặt tất cả sub vào vùng mờ** để đặt chữ dịch vào tâm vùng vừa chọn, kể cả video có viền đen. Đây là chọn vùng thủ công, chưa tự dò chữ gốc. Thao tác vị trí dùng timeline gốc, được lưu, hoàn tác và xuất cùng video.
