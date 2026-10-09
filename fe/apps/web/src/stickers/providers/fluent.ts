import catalog from "./fluent-catalog.json";
import { buildStickerId, parseStickerId } from "../sticker-id";
import type { StickerProvider, StickerItem } from "../types";
const items: StickerItem[] = catalog.map((entry) => ({
	id: buildStickerId({ providerId: "fluent", providerValue: entry.id }),
	provider: "fluent",
	name: entry.name,
	previewUrl: `/vendor/fluent-emoji/${entry.id}.svg`,
	metadata: { keywords: entry.keywords },
}));
export const fluentProvider: StickerProvider = {
	id: "fluent",
	async search({ query, options }) {
		const q = query.trim().toLowerCase();
		const matches = items.filter((item) =>
			`${item.name} ${item.metadata.keywords}`.toLowerCase().includes(q),
		);
		const limit = options?.limit ?? matches.length;
		return {
			items: matches.slice(0, limit),
			total: matches.length,
			hasMore: matches.length > limit,
		};
	},
	async browse({ options }) {
		const limit = options?.limit ?? items.length;
		return {
			sections: [
				{
					id: "fluent",
					title: `Fluent Emoji · ${items.length}`,
					items: items.slice(0, limit),
					layout: "grid",
					hasMore: items.length > limit,
				},
			],
		};
	},
	resolveUrl({ stickerId }) {
		const { providerValue } = parseStickerId({ stickerId });
		return `/vendor/fluent-emoji/${encodeURIComponent(providerValue)}.svg`;
	},
};
