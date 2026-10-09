/** Browser adapters. Filter algorithms are supplied by PixiJS/frei0r, not reimplemented here. */
import type { EffectPass } from "@/effects/types";
import freiCatalog from "@/effects/definitions/frei0r-catalog.json";
import pixiCatalog from "@/effects/definitions/pixi-catalog.json";
import type { Filter, Renderer } from "pixi.js";

export function isExternalPass(pass: EffectPass) {
	return pass.shader.startsWith("pixi/") || pass.shader.startsWith("frei0r/");
}
export function snapshot(
	source: CanvasImageSource,
	width: number,
	height: number,
) {
	const canvas = new OffscreenCanvas(width, height);
	const ctx = canvas.getContext("2d");
	if (!ctx) throw Error("Canvas 2D unavailable");
	ctx.drawImage(source, 0, 0, width, height);
	return canvas;
}
let pixiPromise:
	| Promise<{
			pixi: typeof import("pixi.js");
			filters: typeof import("pixi-filters");
			renderer: Renderer;
	  }>
	| undefined;
function loadPixi() {
	return (pixiPromise ??= (async () => {
		const [pixi, filters] = await Promise.all([
			import("pixi.js"),
			import("pixi-filters"),
		]);
		const renderer = await pixi.autoDetectRenderer({
			preference: "webgl",
			width: 160,
			height: 160,
			backgroundAlpha: 0,
			antialias: false,
			resolution: 1,
			preserveDrawingBuffer: true,
		});
		return { pixi, filters, renderer };
	})());
}
const filterCache = new Map<string, Filter>();
async function renderPixi(
	source: OffscreenCanvas,
	pass: EffectPass,
	time: number,
) {
	const { pixi, filters, renderer } = await loadPixi();
	const id = pass.shader.slice(5),
		width = source.width,
		height = source.height;
	const entry = pixiCatalog.find((x) => x.id === id);
	if (!entry) throw Error(`Unknown Pixi filter: ${id}`);
	const options: Record<string, unknown> = { ...entry.options };
	for (const [key, value] of Object.entries(pass.uniforms)) {
		const [name, index] = key.split(".");
		if (index !== undefined) {
			const array = Array.isArray(options[name])
				? [...(options[name] as number[])]
				: [];
			array[Number(index)] = Number(value);
			options[name] = array;
		} else
			options[key] = typeof options[key] === "boolean" ? Boolean(value) : value;
	}
	if (typeof options.kernelSize === "number")
		options.kernelSize = Math.max(3, Math.round(options.kernelSize) | 1);
	// Supply the render surface geometry required by spatial filters.
	if (
		[
			"BulgePinchFilter",
			"RadialBlurFilter",
			"ZoomBlurFilter",
			"GodrayFilter",
			"ShockwaveFilter",
		].includes(id)
	)
		options.center = { x: width / 2, y: height / 2 };
	if (id === "BulgePinchFilter") options.center = { x: 0.5, y: 0.5 };
	if (id === "TwistFilter") options.offset = { x: width / 2, y: height / 2 };
	if (id.startsWith("TiltShift")) {
		options.start = { x: 0, y: height / 2 };
		options.end = { x: width, y: height / 2 };
		options.axis = "X";
	}
	if (id === "ColorGradientFilter")
		options.stops = [
			{ offset: 0, color: Number(options.colorA) },
			{ offset: 1, color: Number(options.colorB) },
		];
	if (id === "MultiColorReplaceFilter")
		options.replacements = [[0xff0000, 0x0000ff]];
	if (id === "SimpleLightmapFilter") options.lightMap = pixi.Texture.WHITE;
	if (id === "ColorMapFilter") options.colorMap = await getColorMap(pixi);
	const hash = `${id}:${width}:${height}:${JSON.stringify(pass.uniforms)}`;
	let filter = filterCache.get(hash);
	if (!filter) {
		const C = (
			filters as unknown as Record<
				string,
				new (options: Record<string, unknown>) => Filter
			>
		)[id];
		filter = new C(options);
		filterCache.set(hash, filter);
		if (filterCache.size > 48) {
			const oldest = filterCache.keys().next().value!;
			filterCache.get(oldest)!.destroy();
			filterCache.delete(oldest);
		}
	}
	// Time-dependent effects follow the playhead, including offline exports.
	if ("time" in filter) (filter as Filter & { time: number }).time = time;
	if ("seed" in filter)
		(filter as Filter & { seed: number }).seed =
			(Math.floor(time * 30) % 1000) / 1000;
	renderer.resize(width, height);
	const texture = pixi.Texture.from(source);
	const sprite = new pixi.Sprite(texture);
	sprite.filters = [filter];
	sprite.filterArea = new pixi.Rectangle(0, 0, width, height);
	renderer.render({ container: sprite, clear: true });
	const output = snapshot(renderer.canvas as HTMLCanvasElement, width, height);
	sprite.destroy({ texture: true, textureSource: true });
	return output;
}
let colorMap: import("pixi.js").Texture | undefined;
async function getColorMap(pixi: typeof import("pixi.js")) {
	if (colorMap) return colorMap;
	// 16³ LUT, arranged as 16 blue slices horizontally (256 × 16).
	const c = new OffscreenCanvas(256, 16),
		ctx = c.getContext("2d")!;
	const pixels = ctx.createImageData(256, 16);
	for (let b = 0; b < 16; b++)
		for (let g = 0; g < 16; g++)
			for (let r = 0; r < 16; r++) {
				const i = (g * 256 + b * 16 + r) * 4;
				pixels.data.set(
					[
						Math.min(255, r * 17 * 1.08),
						g * 17,
						Math.min(255, b * 17 * 0.92),
						255,
					],
					i,
				);
			}
	ctx.putImageData(pixels, 0, 0);
	colorMap = pixi.Texture.from(c);
	return colorMap;
}

type FreiRuntime = { HEAPU8: Uint8Array } & Record<string, unknown>;
type FreiSlot = {
	runtime: FreiRuntime;
	index: number;
	width: number;
	height: number;
	time: number;
};
const freiSlots = new Map<string, FreiSlot>();
let factoryPromise:
	| Promise<(options: Record<string, unknown>) => Promise<FreiRuntime>>
	| undefined;
function loadFreiFactory() {
	return (factoryPromise ??= (async () => {
		const url = "/vendor/frei0r/frei0r-demo-runtime.mjs";
		const module = await import(/* webpackIgnore: true */ url);
		return module.default;
	})());
}
function call(runtime: FreiRuntime, name: string, ...args: number[]): number {
	return (runtime[`_frei0r_demo_${name}`] as (...values: number[]) => number)(
		...args,
	);
}
function swapRB(data: Uint8Array | Uint8ClampedArray) {
	for (let i = 0; i < data.length; i += 4) {
		const red = data[i];
		data[i] = data[i + 2];
		data[i + 2] = red;
	}
}
async function renderFrei(
	source: OffscreenCanvas,
	pass: EffectPass,
	time: number,
	key: string,
) {
	const entry = freiCatalog.find((x) => `frei0r/${x.id}` === pass.shader);
	if (!entry) throw Error(`Unknown frei0r filter: ${pass.shader}`);
	let slot = freiSlots.get(key);
	if (!slot) {
		const factory = await loadFreiFactory();
		const runtime = await factory({
			locateFile: (file: string) => `/vendor/frei0r/${file}`,
		});
		slot = { runtime, index: -1, width: 0, height: 0, time: -1 };
		freiSlots.set(key, slot);
		if (freiSlots.size > 4) {
			const oldest = freiSlots.keys().next().value!;
			call(freiSlots.get(oldest)!.runtime, "shutdown");
			freiSlots.delete(oldest);
		}
	}
	// frei0r requires dimensions divisible by eight. Pad rather than rescale/crop.
	const width = Math.ceil(source.width / 8) * 8,
		height = Math.ceil(source.height / 8) * 8;
	const r = slot.runtime;
	if (
		slot.index !== entry.index ||
		slot.width !== width ||
		slot.height !== height ||
		time < slot.time
	) {
		if (call(r, "select", entry.index, width, height) !== 0)
			throw Error(`frei0r ${entry.name}: cannot allocate ${width}×${height}`);
		Object.assign(slot, { index: entry.index, width, height });
	}
	slot.time = time;
	for (const param of entry.params) {
		const fallback =
			param.type === 2
				? (param.value as number[]).reduce(
						(packed, c) => (packed << 8) | Math.round(c * 255),
						0,
					)
				: param.value;
		const value = Number(pass.uniforms[`p${param.index}`] ?? fallback);
		if (param.type === 2)
			call(
				r,
				"set_parameter_color",
				param.index,
				((value >> 16) & 255) / 255,
				((value >> 8) & 255) / 255,
				(value & 255) / 255,
			);
		else if (param.type === 3)
			call(
				r,
				"set_parameter_position",
				param.index,
				Number(
					pass.uniforms[`p${param.index}.0`] ?? (param.value as number[])[0],
				),
				Number(
					pass.uniforms[`p${param.index}.1`] ?? (param.value as number[])[1],
				),
			);
		else call(r, "set_parameter_scalar", param.index, value);
	}
	const canvas = new OffscreenCanvas(width, height),
		ctx = canvas.getContext("2d", { willReadFrequently: true })!;
	ctx.drawImage(source, 0, 0);
	const input = ctx.getImageData(0, 0, width, height).data;
	if (entry.colorModel === 0) swapRB(input);
	r.HEAPU8.set(input, call(r, "input_pointer"));
	if (call(r, "update", time) !== 0)
		throw Error(`frei0r ${entry.name}: frame processing failed`);
	const bytes = new Uint8ClampedArray(
		r.HEAPU8.slice(
			call(r, "output_pointer"),
			call(r, "output_pointer") + width * height * 4,
		),
	);
	if (entry.colorModel === 0) swapRB(bytes);
	ctx.putImageData(new ImageData(bytes, width, height), 0, 0);
	return snapshot(canvas, source.width, source.height);
}
// Both libraries use reusable render contexts. Serialize access across thumbnails and preview/export.
let queue: Promise<unknown> = Promise.resolve();
export function applyExternalPass(
	source: OffscreenCanvas,
	pass: EffectPass,
	time = 0,
	key = "thumbnail",
): Promise<OffscreenCanvas> {
	const task = queue.then(() =>
		pass.shader.startsWith("pixi/")
			? renderPixi(source, pass, time)
			: renderFrei(source, pass, time, key),
	);
	queue = task.catch(() => undefined);
	return task;
}
