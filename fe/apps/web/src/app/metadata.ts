import type { Metadata } from "next";
import { SITE_INFO, SITE_URL } from "@/site/brand";
export const baseMetaData: Metadata = {
 metadataBase: new URL(SITE_URL),
 title: SITE_INFO.title,
 description: SITE_INFO.description,
 openGraph: {title: SITE_INFO.title, description: SITE_INFO.description, siteName: SITE_INFO.title, locale: "vi_VN", type: "website"},
 icons: {icon: [{url: "/editor-logo.png", type: "image/png"}], apple: "/editor-logo.png"},
 appleWebApp: {capable: true, title: SITE_INFO.title},
 manifest: "/manifest.json",
};
