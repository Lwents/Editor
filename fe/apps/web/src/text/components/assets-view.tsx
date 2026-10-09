"use client";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { textAnimationPreset } from "opencut-wasm";
import { useUiLanguage } from "@/i18n/ui-language";
import { DraggableItem } from "@/components/editor/panels/assets/draggable-item";
import { PanelView } from "@/components/editor/panels/assets/views/base-panel";
import { useEditor } from "@/editor/use-editor";
import { buildTextElement } from "@/timeline/element-utils";
import { DEFAULT_NEW_ELEMENT_DURATION } from "@/timeline/creation";
import { loadFonts } from "@/fonts/google-fonts";
import type { ElementAnimations } from "@/animation/types";
import templates from "../templates.json";
const motions = {
	none: "Không chuyển động",
	fade: "Hiện / ẩn dần",
	pop: "Bật lên",
	left: "Trượt từ trái",
	right: "Trượt từ phải",
	up: "Trượt từ dưới",
	down: "Trượt từ trên",
	spin: "Xoay vào",
	pulse: "Nhịp phóng to",
};
export function TextView() {
	const t = useUiLanguage(),
		editor = useEditor();
	const [content, setContent] = useState(t("Default text"));
	const [search, setSearch] = useState("");
	const [motion, setMotion] = useState("none");
	const [ready, setReady] = useState(false);
	useEffect(() => {
		let active = true;
		void loadFonts({
			families: [...new Set(templates.map((x) => x.params.fontFamily))],
		})
			.then(() => {
				if (active) setReady(true);
			})
			.catch(() => toast.error(t("Could not load fonts")));
		return () => {
			active = false;
		};
	}, [t]);
	const animations = textAnimationPreset(
		motion,
		BigInt(DEFAULT_NEW_ELEMENT_DURATION),
	) as ElementAnimations;
	return (
		<PanelView title={t("Text")}>
			<div className="space-y-3 pb-3">
				<p className="text-xs text-muted-foreground">
					{t(
						"Drag a template onto the timeline or press +. Edit text and style on the right.",
					)}
				</p>
				<input
					aria-label={t("Text content")}
					className="w-full rounded-md border bg-background p-2"
					value={content}
					onChange={(e) => setContent(e.target.value)}
				/>
				<label className="block text-sm">
					{t("Text animation")}
					<select
						aria-label={t("Text animation")}
						value={motion}
						onChange={(e) => setMotion(e.target.value)}
						className="mt-1 w-full rounded-md border bg-background p-2"
					>
						{Object.entries(motions).map(([id, name]) => (
							<option key={id} value={id}>
								{name}
							</option>
						))}
					</select>
				</label>
				<input
					aria-label={t("Search text templates")}
					placeholder={t("Search text templates")}
					value={search}
					onChange={(e) => setSearch(e.target.value)}
					className="w-full rounded-md border bg-background p-2 text-sm"
				/>
				{!ready && <p className="text-xs">{t("Loading fonts...")}</p>}
			</div>
			<div className="grid grid-cols-2 gap-2">
				{templates
					.filter((x) =>
						x.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()),
					)
					.map((preset) => {
						const params = { ...preset.params, content };
						return (
							<DraggableItem
								key={preset.id}
								name={preset.name}
								containerClassName="w-full"
								aspectRatio={1.6}
								isDraggable={ready}
								dragData={{
									id: `text-template-${preset.id}`,
									type: "text",
									name: preset.name,
									content,
									params,
									animations,
								}}
								onAddToTimeline={({ currentTime }) => {
									if (!ready) return;
									editor.timeline.insertElement({
										element: buildTextElement({
											raw: { name: preset.name, params, animations },
											startTime: currentTime,
										}),
										placement: { mode: "auto" },
									});
								}}
								preview={
									<div className="flex h-full items-center justify-center bg-slate-800 p-2">
										<span
											className="max-w-full truncate px-2 py-1 text-center text-lg"
											style={{
												fontFamily: preset.params.fontFamily,
												fontWeight: preset.params.fontWeight,
												color: preset.params.color,
												background: preset.params["background.enabled"]
													? preset.params["background.color"]
													: undefined,
												borderRadius: 8,
											}}
										>
											{content || preset.name}
										</span>
									</div>
								}
							/>
						);
					})}
			</div>
		</PanelView>
	);
}
