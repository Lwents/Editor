import { effectsRegistry } from "../registry";
import { blurEffectDefinition } from "./blur";

import { colorAdjustmentDefinition } from "./color-adjustment";

const defaultEffects = [blurEffectDefinition, colorAdjustmentDefinition];

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
