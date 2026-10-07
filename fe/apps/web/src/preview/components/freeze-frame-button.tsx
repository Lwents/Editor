"use client";

import { useState } from "react";
import { Snowflake } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { useEditor } from "@/editor/use-editor";
import { useUiLanguage } from "@/i18n/ui-language";
import { useElementSelection } from "@/timeline/hooks/element/use-element-selection";
import { videoCache } from "@/services/video-cache/service";
import { processMediaAssets } from "@/media/processing";
import { buildElementFromMedia } from "@/timeline/element-utils";
import { getSourceTimeAtClipTime } from "@/retime";
import {
	mediaTimeFromSeconds,
	mediaTimeToSeconds,
	roundMediaTime,
} from "@/wasm";

export function FreezeFrameButton() {
	const editor = useEditor();
	const t = useUiLanguage();
	const { selectedElements } = useElementSelection();
	const [busy, setBusy] = useState(false);
	const selected = editor.timeline.getElementsWithTracks({
		elements: selectedElements,
	});
	const clip =
		selected.length === 1 && selected[0].element.type === "video"
			? selected[0].element
			: null;
	async function capture() {
		if (!clip || busy) return;
		const projectId = editor.project.getActive().metadata.id;
		const sceneId = editor.scenes.getActiveScene().id;
		const playhead = editor.playback.getCurrentTime();
		const relative = playhead - clip.startTime;
		if (relative < 0 || relative >= clip.duration) {
			toast.error(t("Move the playhead inside the selected clip"));
			return;
		}
		const asset = editor.media.getAssets().find((a) => a.id === clip.mediaId);
		if (!asset) return;
		setBusy(true);
		editor.playback.pause();
		try {
			const sourceTime = roundMediaTime({
				time:
					clip.trimStart +
					getSourceTimeAtClipTime({ clipTime: relative, retime: clip.retime }),
			});
			const frame = await videoCache.getFrameAt({
				mediaId: asset.id,
				file: asset.file,
				time: mediaTimeToSeconds({ time: sourceTime }),
			});
			if (!frame) throw new Error(t("Could not capture this frame"));
			const canvas = new OffscreenCanvas(
				frame.canvas.width,
				frame.canvas.height,
			);
			const ctx = canvas.getContext("2d");
			if (!ctx) throw new Error(t("Could not capture this frame"));
			ctx.drawImage(frame.canvas, 0, 0);
			const blob = await canvas.convertToBlob({ type: "image/png" });
			const [image] = await processMediaAssets({
				files: [
					new File([blob], `freeze-${Date.now()}.png`, { type: "image/png" }),
				],
			});
			if (
				editor.project.getActiveOrNull()?.metadata.id !== projectId ||
				editor.scenes.getActiveScene().id !== sceneId
			)
				return;
			const saved = await editor.media.addMediaAsset({
				projectId,
				asset: image,
			});
			if (!saved) throw new Error(t("Could not capture this frame"));
			const element = buildElementFromMedia({
				mediaId: saved.id,
				mediaType: "image",
				name: t("Freeze frame"),
				duration: mediaTimeFromSeconds({ seconds: 3 }),
				startTime: playhead,
			});
			if (element.type !== "image") return;
			element.params = { ...element.params, ...clip.params };
			element.effects = clip.effects?.map((e) => ({
				...e,
				params: { ...e.params },
			}));
			editor.timeline.insertElement({
				element,
				placement: {
					mode: "auto",
					trackType: "video",
					insertIndex: editor.scenes.getActiveScene().tracks.overlay.length,
				},
			});
			await editor.save.flush();
			toast.success(t("Added a 3-second still frame above the video"));
		} catch (e) {
			toast.error(
				e instanceof Error ? e.message : t("Could not capture this frame"),
			);
		} finally {
			setBusy(false);
		}
	}
	return (
		<Button
			size="icon"
			variant="text"
			aria-label={t("Freeze frame")}
			title={t("Freeze frame")}
			disabled={!clip || busy}
			onClick={() => void capture()}
		>
			<Snowflake className="size-4" />
		</Button>
	);
}
