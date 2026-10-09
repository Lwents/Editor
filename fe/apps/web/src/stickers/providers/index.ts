import { stickersRegistry } from "../registry";
import type { StickerProvider } from "@/stickers/types";
import { flagsProvider } from "./flags";
import { logosProvider } from "./logos";
import { shapesProvider } from "./shapes";

import { fluentProvider } from "./fluent";

const defaultProviders: StickerProvider[] = [
	fluentProvider,
	logosProvider,
	flagsProvider,
	shapesProvider,
];

export function registerDefaultStickerProviders({
	providersToRegister = defaultProviders,
}: {
	providersToRegister?: StickerProvider[];
} = {}): void {
	for (const provider of providersToRegister) {
		if (stickersRegistry.has(provider.id)) {
			continue;
		}
		stickersRegistry.register({ key: provider.id, definition: provider });
	}
}
