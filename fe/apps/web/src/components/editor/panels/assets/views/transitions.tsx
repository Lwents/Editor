"use client";

import {
	BatchCommand,
	RemoveKeyframeCommand,
	UpsertKeyframeCommand,
	type Command,
} from "@/commands";
import { useState } from "react";
import { buildFadeKeyframes } from "opencut-wasm";
import { useEditor } from "@/editor/use-editor";
import { useElementSelection } from "@/timeline/hooks/element/use-element-selection";
import { PanelView } from "./base-panel";
import { Button } from "@/components/ui/button";
import { useUiLanguage } from "@/i18n/ui-language";
import { isVisualElement } from "@/timeline/element-utils";
import { mediaTimeFromSeconds, mediaTime } from "@/wasm";
import type { ScalarAnimationChannel } from "@/animation/types";

export function TransitionsView() {
	const editor = useEditor();
	const t = useUiLanguage();
	const { selectedElements } = useElementSelection();
	useEditor((e) => e.scenes.getActiveScene());
	const [seconds, setSeconds] = useState(0.5);
	const selected = editor.timeline
		.getElementsWithTracks({ elements: selectedElements })
		.filter(({ element }) => isVisualElement(element));
	const prefix = "opencut-fade-";
	function removalCommands(): Command[] {
		return selected.flatMap(({ element, track }) => {
			const channel = element.animations?.opacity as
				| ScalarAnimationChannel
				| undefined;
			const keys = (channel?.keys ?? []).filter((k) => k.id.startsWith(prefix));
			const base = keys.length
				? Math.max(...keys.map((k) => k.value))
				: Number(element.params.opacity ?? 1);
			return keys.map(
				(k) =>
					new RemoveKeyframeCommand({
						trackId: track.id,
						elementId: element.id,
						propertyPath: "opacity",
						keyframeId: k.id,
						valueAtPlayhead: base,
					}),
			);
		});
	}
	function apply(fadeIn: boolean, fadeOut: boolean) {
		const commands = removalCommands();
		for (const { element, track } of selected) {
			const channel = element.animations?.opacity as
				| ScalarAnimationChannel
				| undefined;
			const oldFades = (channel?.keys ?? []).filter((k) =>
				k.id.startsWith(prefix),
			);
			const opacity = oldFades.length
				? Math.max(...oldFades.map((k) => k.value))
				: Number(element.params.opacity ?? 1);
			const points = buildFadeKeyframes(
				BigInt(element.duration),
				BigInt(mediaTimeFromSeconds({ seconds })),
				fadeIn,
				fadeOut,
				opacity,
			) as { time: number; value: number }[];
			commands.push(
				...points.map(
					(p, index) =>
						new UpsertKeyframeCommand({
							trackId: track.id,
							elementId: element.id,
							propertyPath: "opacity",
							time: mediaTime({ ticks: p.time }),
							value: p.value,
							interpolation: "linear",
							keyframeId: `${prefix}${element.id}-${index}`,
						}),
				),
			);
		}
		if (commands.length)
			editor.command.execute({ command: new BatchCommand(commands) });
		void editor.save.flush();
	}
	return (
		<PanelView title={t("Transitions")}>
			<div className="space-y-4 p-2">
				<p className="text-sm text-muted-foreground">
					{selected.length
						? `${t("Apply to selected clips")} (${selected.length})`
						: t("Choose a clip on the timeline")}
				</p>
				<label className="block space-y-2 text-sm">
					{t("Transition duration (seconds)")}
					<input
						aria-label={t("Transition duration (seconds)")}
						type="number"
						className="block w-full rounded-md border bg-background p-2"
						min={0.05}
						max={10}
						step={0.05}
						value={seconds}
						onChange={(e) => {
							const n = Number(e.target.value);
							if (Number.isFinite(n))
								setSeconds(Math.max(0.05, Math.min(10, n)));
						}}
					/>
				</label>
				{[
					["Fade in", true, false],
					["Fade out", false, true],
					["Fade in and out", true, true],
				].map(([label, fadeIn, fadeOut]) => (
					<Button
						key={String(label)}
						className="w-full"
						variant="outline"
						disabled={!selected.length}
						onClick={() => apply(Boolean(fadeIn), Boolean(fadeOut))}
					>
						{t(String(label))}
					</Button>
				))}
				<Button
					className="w-full"
					variant="outline"
					disabled={!selected.length}
					onClick={() => {
						const commands = removalCommands();
						if (commands.length)
							editor.command.execute({ command: new BatchCommand(commands) });
						void editor.save.flush();
					}}
				>
					{t("Remove fades")}
				</Button>
				<p className="text-xs text-muted-foreground">
					{t("Fades change the opacity at the clip edges. Audio is unchanged.")}
				</p>
			</div>
		</PanelView>
	);
}
