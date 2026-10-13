import Link from "next/link";
import Image from "next/image";
import { ArrowRight } from "lucide-react";
import { Button } from "./ui/button";
import { ThemeToggle } from "./theme-toggle";
import { DEFAULT_LOGO_URL } from "@/site/brand";

export function Header() {
 return <header className="bg-background border-b">
  <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-5">
   <Link href="/" className="flex items-center gap-3 font-semibold text-xl">
    <Image src={DEFAULT_LOGO_URL} alt="Lwent Editor" width={36} height={36} className="rounded-sm" />Lwent Editor
   </Link>
   <div className="flex items-center gap-3">
    <Button asChild><Link href="/projects">Dự án của tôi<ArrowRight className="size-4" /></Link></Button>
    <ThemeToggle />
   </div>
  </div>
 </header>;
}
