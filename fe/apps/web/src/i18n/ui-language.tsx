"use client";

import { useEffect } from "react";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import { create } from "zustand";
import { persist } from "zustand/middleware";

export const vietnamese: Record<string, string> = {
 "Main scene":"Cảnh chính",
 "Drag and drop videos, photos, and audio files here":"Kéo thả video, ảnh và âm thanh vào đây", "Processing your files":"Đang xử lý tệp", "Light":"Giao diện sáng", "Dark":"Giao diện tối", "Thoughts, bugs, ideas...":"Góp ý, báo lỗi, ý tưởng...", "← Back":"← Quay lại", "Submit":"Gửi", "Sending...":"Đang gửi...",
 "Media":"Thư viện", "Sounds":"Âm thanh", "Text":"Văn bản", "Stickers":"Nhãn dán", "Effects":"Hiệu ứng", "Effect":"Hiệu ứng", "Transitions":"Chuyển cảnh", "Captions":"Phụ đề", "Adjustment":"Điều chỉnh", "Settings":"Cài đặt",
 "New project":"Dự án mới", "All projects":"Tất cả dự án", "Create your first project":"Tạo dự án đầu tiên", "No projects yet":"Chưa có dự án", "Project info":"Thông tin dự án", "Name":"Tên", "Frame rate":"Tốc độ khung hình", "Aspect ratio":"Tỷ lệ khung hình", "Background":"Nền", "Custom":"Tùy chỉnh", "Select a frame rate":"Chọn tốc độ khung hình", "Canvas width":"Chiều rộng khung", "Canvas height":"Chiều cao khung",
 "Export":"Xuất video", "Export clips":"Xuất các đoạn", "Export failed":"Xuất video thất bại", "Format":"Định dạng", "Quality":"Chất lượng", "Include audio in export":"Xuất cả âm thanh", "High - Recommended":"Cao - Khuyên dùng", "Medium - Balanced":"Vừa - Cân bằng", "Low - Smallest file size":"Thấp - Dung lượng nhỏ nhất", "Very high - Largest file size":"Rất cao - Dung lượng lớn nhất", "MP4 (H.264) - Better compatibility":"MP4 (H.264) - Tương thích tốt", "WebM (VP9) - Smaller file size":"WebM (VP9) - Dung lượng nhỏ hơn",
 "Import":"Nhập tệp", "Add to timeline":"Thêm vào timeline", "Assets":"Thư viện", "Search...":"Tìm kiếm...", "Search sound effects":"Tìm hiệu ứng âm thanh", "Searching...":"Đang tìm...", "No results found":"Không có kết quả", "Sound effects":"Hiệu ứng âm thanh", "Saved":"Đã lưu", "No saved sounds":"Chưa lưu âm thanh", "Click the heart icon on any sound to save it here":"Bấm trái tim trên âm thanh để lưu tại đây", "Loading sounds...":"Đang tải âm thanh...", "Loading saved sounds...":"Đang tải âm thanh đã lưu...", "Loading more sounds...":"Đang tải thêm âm thanh...", "Clear search":"Xóa tìm kiếm", "Clear all saved sounds?":"Xóa tất cả âm thanh đã lưu?", "Clear all sounds":"Xóa tất cả âm thanh", "Show only commercially licensed":"Chỉ hiện nội dung có giấy phép thương mại", "Sticker categories":"Loại nhãn dán", "No stickers found":"Không tìm thấy nhãn dán",
 "Cancel":"Hủy", "Save":"Lưu", "Delete":"Xóa", "Rename":"Đổi tên", "Rename project":"Đổi tên dự án", "New name":"Tên mới", "Enter a new name":"Nhập tên mới", "Delete project":"Xóa dự án", "Delete Scenes":"Xóa các cảnh", "Delete track":"Xóa hàng", "Warning":"Lưu ý", "Done":"Xong", "Next":"Tiếp tục", "Finish":"Hoàn tất", "Close":"Đóng", "Clear":"Xóa", "Clear all":"Xóa tất cả", "Retry":"Thử lại", "Go back":"Quay lại", "Home":"Trang chủ", "New":"Tạo mới", "Default text":"Văn bản mẫu", "Type":"Loại", "Duration":"Thời lượng", "Created":"Ngày tạo", "Modified":"Ngày sửa", "File size":"Dung lượng", "Project ID":"Mã dự án", "Info":"Thông tin",
 "Exit project":"Thoát dự án", "Shortcuts":"Phím tắt", "Send feedback":"Gửi góp ý", "Timeline":"Timeline", "Video":"Video", "Audio":"Âm thanh", "Fit":"Vừa khung", "Fit to screen":"Vừa màn hình", "Full screen":"Toàn màn hình", "Preview canvas":"Khung xem trước", "Guides":"Hướng dẫn", "Take a look anyway":"Xem thử", "Roadmap":"Kế hoạch phát triển", "Desktop only (for now)":"Dành cho máy tính",
 "Split":"Cắt", "Split element":"Cắt đoạn", "Split left":"Cắt phía trái", "Split right":"Cắt phía phải", "Duplicate":"Nhân bản", "Duplicate element":"Nhân bản đoạn", "Delete element":"Xóa đoạn", "Extract audio":"Tách âm thanh", "Restore source audio":"Khôi phục âm thanh gốc", "Auto snapping":"Tự bám mốc", "Ripple editing":"Tự dồn đoạn", "Add bookmark":"Thêm dấu mốc", "Remove bookmark":"Xóa dấu mốc", "Freeze frame":"Đóng băng khung hình", "Copy":"Sao chép", "Paste elements":"Dán các đoạn", "Replace media":"Thay tệp", "Reveal media":"Hiện trong thư viện", "Edit text":"Sửa văn bản", "Select all":"Chọn tất cả", "Drag to adjust clip volume":"Kéo để chỉnh âm lượng đoạn", "Timeline ruler":"Thước thời gian", "Timeline playhead":"Mốc phát", "Drag playhead":"Kéo mốc phát", "Select keyframe":"Chọn mốc chuyển động", "Save snapshot":"Lưu ảnh khung hình", "Copy snapshot":"Sao chép ảnh khung hình", "No scenes available":"Chưa có cảnh", "No Scene":"Chưa có cảnh",
 "No effects":"Chưa có hiệu ứng", "Open effects":"Mở hiệu ứng", "Add effects to this layer from the Assets panel.":"Thêm hiệu ứng cho lớp này từ bảng Thư viện.", "Click an element on the timeline to edit its properties":"Chọn một đoạn trên timeline để chỉnh thuộc tính", "It's empty here":"Chưa có nội dung", "Blur":"Làm mờ", "Intensity":"Mức độ", "Color adjustment":"Điều chỉnh màu", "Brightness":"Độ sáng", "Contrast":"Tương phản", "Saturation":"Bão hòa", "Temperature":"Nhiệt độ màu", "Tint":"Sắc màu", "Opacity":"Độ hiện", "Transform":"Biến đổi", "Position X":"Vị trí X", "Position Y":"Vị trí Y", "Scale X":"Tỷ lệ X", "Scale Y":"Tỷ lệ Y", "Rotate":"Xoay", "Volume":"Âm lượng", "Speed":"Tốc độ", "Normal":"Bình thường", "Reset":"Đặt lại", "Color":"Màu", "Font":"Phông chữ", "Font size":"Cỡ chữ", "Line height":"Giãn dòng", "Letter spacing":"Giãn chữ", "Alignment":"Căn chữ", "Animation":"Chuyển động", "Mask":"Mặt nạ", "Stroke":"Viền", "Padding X":"Lề X", "Padding Y":"Lề Y", "Corner radius":"Bo góc", "Offset X":"Dịch X", "Offset Y":"Dịch Y", "Presets":"Mẫu", "Language":"Ngôn ngữ", "Auto detect":"Tự nhận diện", "Select a language":"Chọn ngôn ngữ", "See all":"Xem tất cả", "Pick a custom background color":"Chọn màu nền tùy chỉnh", "Unknown":"Không rõ", "Bezier curve editor":"Chỉnh đường cong chuyển động", "Curve preset preview":"Xem trước đường cong",
 "Welcome to OpenCut":"Chào mừng đến OpenCut", "Edit, translate and review your video":"Chỉnh sửa, dịch và kiểm tra video", "Import a video, translate subtitles, then review each segment before exporting.":"Nhập video, dịch phụ đề rồi kiểm tra từng đoạn trước khi xuất.", "Your changes are saved in this browser.":"Các chỉnh sửa được lưu trong trình duyệt này.", "Get started":"Bắt đầu",
 "Blending":"Hòa trộn", "Masks":"Mặt nạ", "Graphic":"Hình vẽ", "Fill":"Tô màu", "Width":"Chiều rộng", "Height":"Chiều cao", "Points":"Số đỉnh", "Depth":"Độ sâu", "Sides":"Số cạnh", "Stroke align":"Căn viền", "Inside":"Bên trong", "Center":"Ở giữa", "Outside":"Bên ngoài", "Left":"Trái", "Right":"Phải", "Top":"Trên", "Bottom":"Dưới", "Bold":"Đậm", "Italic":"Nghiêng", "Underline":"Gạch chân", "Regular":"Thường", "Content":"Nội dung", "Blend mode":"Chế độ hòa trộn", "Preserve pitch":"Giữ cao độ", "Maintain pitch":"Giữ cao độ", "Feather":"Làm mềm mép", "Invert":"Đảo vùng", "Radius":"Bán kính", "Roundness":"Độ bo",
 "Selected elements":"đoạn đã chọn", "Move the playhead inside the selected clip":"Đưa mốc phát vào trong đoạn video đã chọn", "Could not capture this frame":"Không chụp được khung hình này", "Added a 3-second still frame above the video":"Đã thêm ảnh tĩnh 3 giây phía trên video",
 "Choose a clip on the timeline":"Chọn một đoạn trên timeline", "Apply":"Áp dụng", "Reset color":"Đặt lại màu", "Fade in":"Hiện dần", "Fade out":"Mờ dần", "Fade in and out":"Hiện rồi mờ dần", "Remove fades":"Gỡ chuyển cảnh", "Transition duration (seconds)":"Thời gian chuyển cảnh (giây)", "Adjustments are included in preview and exported video.":"Các điều chỉnh có trong bản xem trước và video xuất.", "Apply to selected clips":"Áp dụng cho các đoạn đã chọn", "Fades change the opacity at the clip edges. Audio is unchanged.":"Chuyển cảnh thay đổi độ hiện ở hai mép đoạn. Âm thanh giữ nguyên.", "Language / Ngôn ngữ":"Ngôn ngữ / Language",
};

type Language = "vi" | "en";
export const useLanguageStore = create<{ language: Language; setLanguage: (language: Language) => void }>()(persist((set) => ({ language: "vi", setLanguage: (language) => set({ language }) }), { name: "opencut-ui-language", skipHydration: true }));
export function useUiLanguage() {
 const language = useLanguageStore((s) => s.language);
 return (text: string) => language === "vi" ? vietnamese[text] ?? text : text;
}
export function UiText({ text }: { text: string }) { const t = useUiLanguage(); return <>{t(text)}</>; }
function LanguageFlag({ language }: { language: Language }) {
 return (
  <svg aria-hidden="true" viewBox="0 0 30 20" className="h-4 w-6 shrink-0 rounded-sm overflow-hidden">
   {language === "vi" ? <>
    <path fill="#da251d" d="M0 0h30v20H0z" />
    <path fill="#ffdf00" d="m15 3 1.57 4.84h5.09l-4.12 2.99 1.57 4.84L15 12.68l-4.11 2.99 1.57-4.84-4.12-2.99h5.09z" />
   </> : <>
    <path fill="#012169" d="M0 0h30v20H0z" />
    <path stroke="#fff" strokeWidth="5" d="m0 0 30 20M30 0 0 20" />
    <path stroke="#c8102e" strokeWidth="2" d="m0 0 30 20M30 0 0 20" />
    <path stroke="#fff" strokeWidth="7" d="M15 0v20M0 10h30" />
    <path stroke="#c8102e" strokeWidth="4" d="M15 0v20M0 10h30" />
   </>}
  </svg>
 );
}

export function LanguageSelect() {
 const { language, setLanguage } = useLanguageStore();
 useEffect(() => { void useLanguageStore.persist.rehydrate(); }, []);
 useEffect(() => { document.documentElement.lang = language; }, [language]);
 return (
  <Select value={language} onValueChange={(value) => setLanguage(value === "en" ? "en" : "vi")}>
   <SelectTrigger aria-label="Language / Ngôn ngữ" variant="outline" className="h-10 gap-2">
    <SelectValue>
     <span className="flex items-center gap-2"><LanguageFlag language={language} />{language === "vi" ? "Tiếng Việt" : "English"}</span>
    </SelectValue>
   </SelectTrigger>
   <SelectContent>
    <SelectItem value="vi" textValue="Tiếng Việt"><span className="flex items-center gap-2"><LanguageFlag language="vi" />Tiếng Việt</span></SelectItem>
    <SelectItem value="en" textValue="English"><span className="flex items-center gap-2"><LanguageFlag language="en" />English</span></SelectItem>
   </SelectContent>
  </Select>
 );
}
