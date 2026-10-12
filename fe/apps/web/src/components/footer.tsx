import Link from "next/link";
export function Footer() {
 return <footer className="border-t bg-background">
  <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-6 text-sm text-muted-foreground">
   <span>Lwent Editor · Chỉnh sửa video, dịch phụ đề và thuyết minh</span>
   <Link href="/projects" className="hover:text-foreground">Dự án của tôi</Link>
  </div>
 </footer>;
}
