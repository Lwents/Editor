import type { EffectDefinition, EffectUniformValue } from "../types";
import type { ParamDefinition, ParamValues } from "@/params";
import pixiCatalog from "./pixi-catalog.json";
import frei0rCatalog from "./frei0r-catalog.json";

function numberParam(
	key: string,
	label: string,
	value: number,
	max = 1,
	min = 0,
): ParamDefinition {
	return {
		key,
		label,
		type: "number",
		default: value,
		min,
		max,
		step: max <= 2 ? 0.01 : 1,
	};
}
const colors = /color|original|replacement/i;
function pixiParams(options: Record<string, unknown>): ParamDefinition[] {
	return Object.entries(options).flatMap(([key, value]): ParamDefinition[] => {
		if (["time", "seed"].includes(key)) return [];
		const label = key.replace(/([a-z])([A-Z])/g, "$1 $2");
		if (typeof value === "number") {
			if (colors.test(key))
				return [
					{
						key,
						label,
						type: "color",
						default: `#${Math.round(value).toString(16).padStart(6, "0")}`,
					},
				];
			const max = /angle|rotation/.test(key)
				? 360
				: /quality/.test(key)
					? 20
					: /radius|blur|size|length|speed|padding/i.test(key)
						? 1000
						: Math.max(2, value * 4);
			const min = /quality|kernelSize|maxKernelSize|noiseSize/.test(key)
				? 1
				: key === "gamma"
					? 0.01
					: value < 0
						? -max
						: 0;
			return [numberParam(key, label, value, max, min)];
		}
		if (typeof value === "boolean")
			return [{ key, label, type: "boolean", default: value }];
		if (Array.isArray(value) && value.every((x) => typeof x === "number"))
			return value.map((v, i) =>
				numberParam(
					`${key}.${i}`,
					`${label} ${i + 1}`,
					v,
					/matrix/.test(key) ? 10 : 1000,
					-1000,
				),
			);
		return [];
	});
}
function encode(params: ParamValues): Record<string, EffectUniformValue> {
	return Object.fromEntries(
		Object.entries(params).map(([key, value]) => [
			key,
			typeof value === "string"
				? parseInt(value.replace("#", ""), 16)
				: typeof value === "boolean"
					? Number(value)
					: value,
		]),
	);
}
export const externalDefinitions: EffectDefinition[] = [
	...pixiCatalog.map((entry) => ({
		type: `pixi/${entry.id}`,
		name: `Pixi · ${entry.name}`,
		category: "PixiJS",
		keywords: [entry.name, "pixi", "gpu"],
		params: pixiParams(entry.options),
		renderer: {
			passes: [
				{
					shader: `pixi/${entry.id}`,
					uniforms: ({ effectParams }: { effectParams: ParamValues }) =>
						encode(effectParams),
				},
			],
		},
	})),
	...frei0rCatalog.map((entry) => ({
		type: `frei0r/${entry.id}`,
		name: `frei0r · ${entry.name}`,
		category: "frei0r",
		keywords: [entry.id, entry.description, "frei0r"],
		params: entry.params.flatMap((p): ParamDefinition[] => {
			if (p.type === 0)
				return [
					{
						key: `p${p.index}`,
						label: p.name,
						type: "boolean",
						default: Boolean(p.value),
					},
				];
			if (p.type === 2) {
				const rgb = p.value as number[];
				return [
					{
						key: `p${p.index}`,
						label: p.name,
						type: "color",
						default: `#${rgb
							.map((v) =>
								Math.round(v * 255)
									.toString(16)
									.padStart(2, "0"),
							)
							.join("")}`,
					},
				];
			}
			if (p.type === 3)
				return (p.value as number[]).map((v, i) =>
					numberParam(
						`p${p.index}.${i}`,
						`${p.name} ${i === 0 ? "X" : "Y"}`,
						v,
					),
				);
			return [numberParam(`p${p.index}`, p.name, Number(p.value))];
		}),
		renderer: {
			passes: [
				{
					shader: `frei0r/${entry.id}`,
					uniforms: ({ effectParams }: { effectParams: ParamValues }) =>
						encode(effectParams),
				},
			],
		},
	})),
];
