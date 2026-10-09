import type { FontAtlas } from "@/fonts/types";
import { SYSTEM_FONTS } from "@/fonts/system-fonts";

import localFonts from "./local-fonts.json";

const GOOGLE_FONTS_CSS = "https://fonts.googleapis.com/css2";
const FONT_ATLAS_PATH = "/fonts/font-atlas.json";
const FONT_CHUNK_PATH_PREFIX = "/fonts/font-chunk-";

const fullLoaded = new Set<string>();

let cachedAtlas: FontAtlas | null = null;
let atlasFetchPromise: Promise<FontAtlas | null> | null = null;

function encodeGoogleFontsFamily(family: string): string {
	return family.replace(/ /g, "+");
}

export function getCachedFontAtlas(): FontAtlas | null {
	return cachedAtlas;
}

export function clearFontAtlasCache(): void {
	cachedAtlas = null;
	atlasFetchPromise = null;
	fullLoaded.clear();
}

export function loadFontAtlas(): Promise<FontAtlas | null> {
	if (cachedAtlas) return Promise.resolve(cachedAtlas);
	if (atlasFetchPromise) return atlasFetchPromise;

	atlasFetchPromise = fetch(FONT_ATLAS_PATH)
		.then(async (response) => {
			if (!response.ok) return null;
			const data: FontAtlas = await response.json();
			cachedAtlas = data;
			preloadChunkImages({ atlas: data });
			return data;
		})
		.catch(() => null);

	return atlasFetchPromise;
}

function preloadChunkImages({ atlas }: { atlas: FontAtlas }): void {
	const maxChunk = Math.max(
		...Object.values(atlas.fonts).map((entry) => entry.ch),
	);
	for (let i = 0; i <= maxChunk; i++) {
		// hint browser to preload chunk images without blocking
		const img = new Image();
		img.src = `${FONT_CHUNK_PATH_PREFIX}${i}.avif`;
	}
}

export async function loadFullFont({
	family,
	weights = [400, 700],
}: {
	family: string;
	weights?: number[];
}): Promise<void> {
	if (fullLoaded.has(family)) return;
	const local = (
		localFonts as Record<
			string,
			{ url: string; weight: string; boldUrl?: string }
		>
	)[family];
	if (local) {
		const faces = [
			new FontFace(family, `url("${encodeURI(local.url)}")`, {
				weight: local.weight,
			}),
		];
		if (local.boldUrl)
			faces.push(
				new FontFace(family, `url("${encodeURI(local.boldUrl)}")`, {
					weight: "700",
				}),
			);
		await Promise.all(
			faces.map(async (face) => {
				await face.load();
				document.fonts.add(face);
			}),
		);
		fullLoaded.add(family);
		return;
	}

	const url = `${GOOGLE_FONTS_CSS}?family=${encodeGoogleFontsFamily(family)}:wght@${weights.join(";")}&display=swap`;
	const link = document.createElement("link");
	link.rel = "stylesheet";
	link.href = url;
	document.head.appendChild(link);
	await new Promise<void>((resolve) => {
		link.addEventListener("load", () => resolve(), { once: true });
		link.addEventListener("error", () => resolve(), { once: true });
	});
	await Promise.all(
		weights.map((weight) =>
			document.fonts.load(`${weight} 16px "${family.replace(/"/g, '\\"')}"`),
		),
	);
	fullLoaded.add(family);
}

export async function loadFonts({
	families,
}: {
	families: string[];
}): Promise<void> {
	const googleFonts = [...new Set(families)].filter(
		(family) => !SYSTEM_FONTS.has(family),
	);
	await Promise.all(googleFonts.map((family) => loadFullFont({ family })));
}
