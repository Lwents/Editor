import { splitShortSubtitles } from "opencut-wasm";
import { measureShortSubtitleWords } from "@/subtitles/build-subtitle-text-element";
import type { SubtitleCue } from "@/subtitles/types";

// The browser provides font metrics; Rust owns splitting and timing.
export function shortSubtitleCues(
	caption: SubtitleCue,
	canvasSize: { width: number; height: number },
): SubtitleCue[] {
	const measures = measureShortSubtitleWords({
		text: caption.text,
		canvasSize,
		style: caption.style,
	});
	const chunks = splitShortSubtitles({
		text: caption.text,
		startTime: caption.startTime,
		duration: caption.duration,
		...measures,
		maxWords: 7,
	}) as { text: string; startTime: number; duration: number }[];
	return chunks.map((chunk) => ({ ...caption, ...chunk }));
}
