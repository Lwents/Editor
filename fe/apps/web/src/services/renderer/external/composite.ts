import type {
	FrameDescriptor,
	FrameItemDescriptor,
	TextureUploadDescriptor,
} from "../compositor/types";
import { wasmCompositor } from "../compositor/wasm-compositor";
import { applyExternalPass, isExternalPass, snapshot } from "./effects";
import type { EffectPass } from "@/effects/types";

export async function renderExternalItems(
	frame: FrameDescriptor,
	textures: TextureUploadDescriptor[],
	time: number,
): Promise<{ frame: FrameDescriptor; textures: TextureUploadDescriptor[] }> {
	if (
		!frame.items.some((item) =>
			item.effectPassGroups.some((group) => group.some(isExternalPass)),
		)
	)
		return { frame, textures };
	const uploads = [...textures];
	const items: FrameItemDescriptor[] = [];
	const full = {
		centerX: frame.width / 2,
		centerY: frame.height / 2,
		width: frame.width,
		height: frame.height,
		rotationDegrees: 0,
		flipX: false,
		flipY: false,
	};
	function rasterize(
		parts: FrameItemDescriptor[],
		clear: number[] = [0, 0, 0, 0],
	) {
		wasmCompositor.ensureInitialized(frame);
		wasmCompositor.syncTextures(uploads);
		wasmCompositor.render({
			...frame,
			clear: { color: clear as [number, number, number, number] },
			items: parts,
		});
		return snapshot(wasmCompositor.getCanvas(), frame.width, frame.height);
	}
	function layer(
		source: OffscreenCanvas,
		id: string,
	): Extract<FrameItemDescriptor, { type: "layer" }> {
		uploads.push({
			kind: "external",
			id,
			source,
			width: frame.width,
			height: frame.height,
		});
		return {
			type: "layer",
			textureId: id,
			transform: full,
			opacity: 1,
			blendMode: "normal",
			effectPassGroups: [],
			mask: null,
		};
	}
	async function process(
		source: OffscreenCanvas,
		groups: EffectPass[][],
		id: string,
	) {
		let index = 0;
		for (const group of groups)
			for (const pass of group) {
				if (isExternalPass(pass))
					source = await applyExternalPass(
						source,
						pass,
						time,
						`${id}:${index}`,
					);
				else
					source = rasterize([
						{
							...layer(source, `${id}:native:${index}`),
							effectPassGroups: [[pass]],
						},
					]);
				index++;
			}
		return source;
	}
	for (let i = 0; i < frame.items.length; i++) {
		const item = frame.items[i];
		if (!item.effectPassGroups.some((group) => group.some(isExternalPass))) {
			items.push(item);
			continue;
		}
		const id = `external:${i}`;
		if (item.type === "sceneEffect") {
			const source = await process(
				rasterize(items, frame.clear.color),
				item.effectPassGroups,
				id,
			);
			items.splice(0, items.length, layer(source, id));
		} else {
			const source = await process(
				rasterize([
					{
						...item,
						opacity: 1,
						blendMode: "normal",
						mask: null,
						effectPassGroups: [],
					},
				]),
				item.effectPassGroups,
				id,
			);
			items.push({
				...layer(source, id),
				opacity: item.opacity,
				blendMode: item.blendMode,
				mask: item.mask,
			});
		}
	}
	return { frame: { ...frame, items }, textures: uploads };
}

export async function rasterizeDescriptor(
	frame: FrameDescriptor,
	textures: TextureUploadDescriptor[],
	time: number,
) {
	const processed = await renderExternalItems(frame, textures, time);
	wasmCompositor.ensureInitialized(frame);
	wasmCompositor.syncTextures(processed.textures);
	wasmCompositor.render(processed.frame);
	return snapshot(wasmCompositor.getCanvas(), frame.width, frame.height);
}
