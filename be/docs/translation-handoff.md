# Bàn giao ngữ cảnh khi đổi chat

## Cơ sở thiết kế

- [DeepSeek: Multi-round Conversation](https://api-docs.deepseek.com/guides/multi_round_chat/): API cần phía gọi cung cấp lịch sử/ngữ cảnh. Đây là tài liệu API; không dùng để suy đoán giới hạn của giao diện web.
- [Anthropic: Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents): lưu ghi chú có cấu trúc ngoài cửa sổ chat và chọn phần liên quan khi tiếp tục.

Ứng dụng dùng một hồ sơ trên máy cho cả Gemini, DeepSeek và API. Mỗi yêu cầu dịch tự mang ngữ cảnh cần thiết, kể cả yêu cầu đầu tiên sau khi mở chat mới. Không có lượt gọi AI riêng để tóm tắt chat.

## Dữ liệu được giữ

`translation_memory.sqlite3` trong thư mục tác vụ chứa các cặp nguồn/bản dịch đã qua kiểm tra cấu trúc và các ghi chú có ID, trích dẫn nguồn. Namespace gắn với nguồn, timestamp, ngữ cảnh, dịch vụ, model và chế độ dịch. Bản rà soát kế thừa hồ sơ nguồn và ưu tiên các câu đã rà soát ở đoạn trước.

`translation_handoff.json` và `review_handoff.json` là bản chụp gói ngữ cảnh gửi gần nhất, có digest để kiểm tra. SQLite là kho đầy đủ; bản chụp chỉ chứa phần được chọn. Sao lưu tác vụ hiện có tự đưa các tệp này vào bản sao, với SQLite backup API để giữ dữ liệu WAL nhất quán.

## Chọn và bảo vệ ngữ cảnh

- Mang theo hồ sơ nhân vật/cách viết tên hiện có, nguồn và bản dịch lân cận, trích đoạn cũ liên quan nhân vật, ghi chú quan hệ và thuật ngữ.
- Trích dẫn phải khớp nguyên văn một câu hợp lệ trong nhóm đã trả. Loại ID tương lai và câu chưa dịch hợp lệ.
- Quote khớp chỉ chứng minh xuất xứ, không chứng minh diễn giải đúng. Không xác nhận người nói chỉ từ tên được gọi. Nếu subject/target không được nêu trong quote thì giữ ghi chú ở trạng thái chưa chắc.
- Giữ các lựa chọn mâu thuẫn cùng trích dẫn; không ghi đè âm thầm. Ghi chú xưng hô theo cảnh không trở thành quy tắc toàn phim.
- Không áp quan hệ của một cặp nhân vật sang cặp khác chỉ vì cùng chức danh. Khoảng thời gian dài là gợi ý đổi cảnh, không phải xác nhận đã cắt cảnh.
- Các câu còn thiếu và thông tin ở các nhóm sau chưa được dùng làm bằng chứng cho nhóm trước.
- Bản dịch cũ và các ghi nhớ cũ không có trích dẫn là dữ liệu tham khảo. Câu nguồn hiện tại có ưu tiên cao hơn.

Gói bàn giao giới hạn 7.000/11.000/15.000 ký tự theo chế độ nhanh/cân bằng/sâu; phần còn lại vẫn nằm trên máy để truy hồi khi liên quan. Giới hạn này không bao gồm prompt dịch và hồ sơ nhân vật riêng.

## Giới hạn

Kiểm tra cơ học bảo vệ xuất xứ, phạm vi, tính bền vững và định dạng. Nó không tự chứng minh nghĩa dịch, danh tính người nói, quan hệ hoặc chất lượng OCR. Phần không có bằng chứng vẫn cần được đánh dấu hoặc người dùng duyệt; không có bảo đảm dịch đúng 100%.
