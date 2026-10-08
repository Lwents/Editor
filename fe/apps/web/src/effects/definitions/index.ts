import { effectsRegistry } from "../registry";
import { blurEffectDefinition } from "./blur";

import { colorAdjustmentDefinition } from "./color-adjustment";

import { stylizeDefinitions } from "./stylize";

const defaultEffects = [
	blurEffectDefinition,
	colorAdjustmentDefinition,
	...stylizeDefinitions,
];

export function registerDefaultEffects(): void {
	for (const definition of defaultEffects) {
		if (effectsRegistry.has(definition.type)) {
			continue;
		}
		effectsRegistry.register({
			key: definition.type,
			definition,
		});
	}
}
