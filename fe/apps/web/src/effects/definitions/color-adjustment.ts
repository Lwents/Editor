import type { EffectDefinition } from "@/effects/types";

export const colorAdjustmentDefinition: EffectDefinition = {
	type: "color-adjustment",
	name: "Color adjustment",
	category: "Color presets",
	keywords: ["color", "brightness", "contrast", "saturation"],
	params: [
		{
			key: "brightness",
			label: "Brightness",
			type: "number",
			default: 0,
			min: -1,
			max: 1,
			step: 0.01,
		},
		{
			key: "contrast",
			label: "Contrast",
			type: "number",
			default: 1,
			min: 0,
			max: 2,
			step: 0.01,
		},
		{
			key: "saturation",
			label: "Saturation",
			type: "number",
			default: 1,
			min: 0,
			max: 2,
			step: 0.01,
		},
		{
			key: "temperature",
			label: "Temperature",
			type: "number",
			default: 0,
			min: -0.3,
			max: 0.3,
			step: 0.01,
		},
		{
			key: "tint",
			label: "Tint",
			type: "number",
			default: 0,
			min: -0.3,
			max: 0.3,
			step: 0.01,
		},
	],
	renderer: {
		passes: [
			{
				shader: "color-adjustment",
				uniforms: ({ effectParams: p }) => ({
					u_brightness: Number(p.brightness ?? 0),
					u_contrast: Number(p.contrast ?? 1),
					u_saturation: Number(p.saturation ?? 1),
					u_temperature: Number(p.temperature ?? 0),
					u_tint: Number(p.tint ?? 0),
				}),
			},
		],
	},
};
