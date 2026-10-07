"use client";

import { useEditor } from "@/editor/use-editor";
import { useElementSelection } from "@/timeline/hooks/element/use-element-selection";
import { PanelView } from "./base-panel";
import { Button } from "@/components/ui/button";
import { useUiLanguage } from "@/i18n/ui-language";
import { colorAdjustmentDefinition } from "@/effects/definitions/color-adjustment";
import { isVisualElement } from "@/timeline/element-utils";

export function AdjustmentView() {
	const editor = useEditor();
	const t = useUiLanguage();
	const { selectedElements } = useElementSelection();
	useEditor((e) => e.scenes.getActiveScene());
	const selected = editor.timeline
		.getElementsWithTracks({ elements: selectedElements })
		.filter(
			({ element }) => element.type === "video" || element.type === "image",
		);
	const first = selected[0];
	const effect =
		first && isVisualElement(first.element)
			? first.element.effects?.find((e) => e.type === "color-adjustment")
			: undefined;
	function update(key: string, value: number) {
		for (const { track, element } of selected) {
			if (!isVisualElement(element)) continue;
			const existing = element.effects?.find(
				(e) => e.type === "color-adjustment",
			);
			const effectId =
				existing?.id ||
				editor.timeline.addClipEffect({
					trackId: track.id,
					elementId: element.id,
					effectType: "color-adjustment",
				});
			if (existing && !existing.enabled)
				editor.timeline.toggleClipEffect({
					trackId: track.id,
					elementId: element.id,
					effectId,
				});
			editor.timeline.updateClipEffectParams({
				trackId: track.id,
				elementId: element.id,
				effectId,
				params: { [key]: value },
			});
		}
		void editor.save.flush();
	}
	return (
		<PanelView title={t("Adjustment")}>
			<div className="space-y-4 p-2">
				<p className="text-sm text-muted-foreground">
					{first
						? `${t("Apply to selected clips")} (${selected.length}): ${first.element.name}`
						: t("Choose a clip on the timeline")}
				</p>
				{colorAdjustmentDefinition.params.map((param) => (
					<label key={param.key} className="block space-y-2 text-sm">
						<div className="flex justify-between">
							<span>{t(param.label)}</span>
							<output>
								{Number(effect?.params[param.key] ?? param.default).toFixed(2)}
							</output>
						</div>
						<input
							aria-label={t(param.label)}
							type="range"
							className="w-full accent-blue-500"
							min={"min" in param ? param.min : 0}
							max={"max" in param ? param.max : 1}
							step={"step" in param ? param.step : 0.01}
							disabled={!first}
							value={Number(effect?.params[param.key] ?? param.default)}
							onChange={(e) => update(param.key, Number(e.target.value))}
						/>
					</label>
				))}
				<Button
					variant="outline"
					disabled={!first}
					className="w-full"
					onClick={() => {
						for (const { track, element } of selected) {
							if (!isVisualElement(element)) continue;
							const color = element.effects?.find(
								(e) => e.type === "color-adjustment",
							);
							if (color)
								editor.timeline.removeClipEffect({
									trackId: track.id,
									elementId: element.id,
									effectId: color.id,
								});
						}
						void editor.save.flush();
					}}
				>
					{t("Reset color")}
				</Button>
				<p className="text-xs text-muted-foreground">
					{t("Adjustments are included in preview and exported video.")}
				</p>
			</div>
		</PanelView>
	);
}
