"use client";
import { useUiLanguage } from "@/i18n/ui-language";

import { useEffect, useRef, useCallback, useState } from "react";
import { PanelView } from "@/components/editor/panels/assets/views/base-panel";
import { DraggableItem } from "@/components/editor/panels/assets/draggable-item";
import { effectsRegistry, EFFECT_TARGET_ELEMENT_TYPES } from "@/effects";
import { initializeGpuRenderer } from "@/services/renderer/gpu-renderer";
import { effectPreviewService } from "@/services/renderer/effect-preview";
import { useEditor } from "@/editor/use-editor";
import { buildEffectElement } from "@/timeline/element-utils";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import { BatchCommand, AddClipEffectCommand } from "@/commands";
import { usePropertiesStore } from "@/components/editor/panels/properties/stores/properties-store";
import type { EffectDefinition } from "@/effects/types";

export function EffectsView() {
	const uiTranslate = useUiLanguage();

	const [search, setSearch] = useState("");
	const [category, setCategory] = useState("All effects");
	const effects = effectsRegistry
		.getAll()
		.filter(
			(effect) =>
				(category === "All effects" || effect.category === category) &&
				[uiTranslate(effect.name), effect.name, ...effect.keywords]
					.join(" ")
					.toLocaleLowerCase()
					.includes(search.toLocaleLowerCase().trim()),
		);
	return (
		<PanelView
			title={uiTranslate("Effects")}
			actions={
				<span className="rounded-md bg-accent px-2 py-1 text-xs text-muted-foreground">
					{effects.length} / {effectsRegistry.getAll().length}
				</span>
			}
		>
			<div className="space-y-3 pb-3">
				<input
					aria-label={uiTranslate("Search effects")}
					placeholder={uiTranslate("Search effects")}
					value={search}
					onChange={(e) => setSearch(e.target.value)}
					className="w-full rounded-lg border bg-background px-3 py-2 text-sm"
				/>
				<div
					className="flex flex-wrap gap-1"
					aria-label={uiTranslate("Effects")}
				>
					{[
						"All effects",
						"PixiJS",
						"frei0r",
						"Color presets",
						"Lighting",
						"Retro",
						"Stylize",
						"Distort",
					].map((group) => (
						<Button
							key={group}
							size="sm"
							variant={category === group ? "secondary" : "ghost"}
							aria-pressed={category === group}
							onClick={() => setCategory(group)}
							className="h-7 px-2 text-xs"
						>
							{uiTranslate(group)}
						</Button>
					))}
				</div>
				<p className="text-xs leading-relaxed text-muted-foreground">
					{uiTranslate(
						"Drag onto a clip, or select a clip and press Apply. Adjust strength on the right.",
					)}
				</p>
			</div>
			{effects.length ? (
				<EffectsGrid effects={effects} />
			) : (
				<p className="py-8 text-center text-sm text-muted-foreground">
					{uiTranslate("No matching effects")}
				</p>
			)}
		</PanelView>
	);
}

function EffectsGrid({ effects }: { effects: EffectDefinition[] }) {
	return (
		<div
			className="grid gap-2"
			style={{ gridTemplateColumns: "repeat(auto-fill, minmax(112px, 1fr))" }}
		>
			{effects.map((effect) => (
				<EffectItem key={effect.type} effect={effect} />
			))}
		</div>
	);
}

function EffectPreviewCanvas({ effectType }: { effectType: string }) {
	const canvasRef = useRef<HTMLCanvasElement>(null);

	useEffect(() => {
		let mounted = true;
		const render = () => {
			if (mounted && canvasRef.current) {
				effectPreviewService.renderPreview({
					effectType,
					params: {},
					targetCanvas: canvasRef.current,
				});
			}
		};

		const canvas = canvasRef.current;
		let unsubscribe = () => {};
		const observer = new IntersectionObserver(
			(entries) => {
				if (!entries.some((entry) => entry.isIntersecting)) return;
				observer.disconnect();
				void initializeGpuRenderer().then(render);
				unsubscribe = effectPreviewService.onPreviewImageReady({
					callback: render,
				});
			},
			{ rootMargin: "100px" },
		);
		if (canvas) observer.observe(canvas);
		return () => {
			mounted = false;
			observer.disconnect();
			unsubscribe();
		};
	}, [effectType]);

	return <canvas ref={canvasRef} className="size-full" />;
}

function EffectItem({ effect }: { effect: EffectDefinition }) {
	const t = useUiLanguage();
	const editor = useEditor();

	const handleAddToTimeline = useCallback(() => {
		const element = buildEffectElement({
			effectType: effect.type,
			startTime: editor.playback.getCurrentTime(),
		});
		editor.timeline.insertElement({
			placement: { mode: "auto", trackType: "effect" },
			element,
		});
	}, [editor, effect.type]);

	const applyToSelected = () => {
		const selected = editor.selection.getSelectedElements().filter((ref) => {
			const element = editor.timeline
				.getTrackById({ trackId: ref.trackId })
				?.elements.find((e) => e.id === ref.elementId);
			return (
				element &&
				EFFECT_TARGET_ELEMENT_TYPES.includes(
					element.type as (typeof EFFECT_TARGET_ELEMENT_TYPES)[number],
				)
			);
		});
		if (!selected.length) {
			toast.info(t("Select a video or image clip first"));
			return;
		}
		editor.command.execute({
			command: new BatchCommand(
				selected.map(
					(ref) =>
						new AddClipEffectCommand({ ...ref, effectType: effect.type }),
				),
			),
		});
		const element = editor.timeline
			.getTrackById({ trackId: selected[0].trackId })
			?.elements.find((e) => e.id === selected[0].elementId);
		if (element)
			usePropertiesStore
				.getState()
				.setActiveTab({ elementType: element.type, tabId: "effects" });
		toast.success(t("Effect added to selected clips"));
	};

	const preview = <EffectPreviewCanvas effectType={effect.type} />;

	return (
		<div
			className="rounded-lg border p-1.5 space-y-1"
			data-effect-type={effect.type}
		>
			<DraggableItem
				name={t(effect.name)}
				preview={preview}
				dragData={{
					id: effect.type,
					name: effect.name,
					type: "effect",
					effectType: effect.type,
					targetElementTypes: EFFECT_TARGET_ELEMENT_TYPES,
				}}
				onAddToTimeline={handleAddToTimeline}
				shouldShowLabel={false}
				aspectRatio={1}
				isRounded
				variant="card"
				containerClassName="w-full"
			/>
			<p
				className="text-xs font-medium leading-snug min-h-8 px-1"
				title={t(effect.name)}
			>
				{t(effect.name)}
			</p>
			<Button
				size="sm"
				variant="secondary"
				className="w-full h-7 text-xs"
				aria-label={`${t("Apply")} ${t(effect.name)}`}
				onClick={applyToSelected}
			>
				{t("Apply to clip")}
			</Button>
			<Button
				size="sm"
				variant="ghost"
				className="w-full h-6 text-[10px]"
				aria-label={`${t("Add effect layer")} ${t(effect.name)}`}
				onClick={handleAddToTimeline}
			>
				{t("Add effect layer")}
			</Button>
		</div>
	);
}
