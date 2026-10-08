"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { useEditor } from "@/editor/use-editor";
import {
	AddTrackCommand,
	BatchCommand,
	DeleteElementsCommand,
	InsertElementCommand,
} from "@/commands";
import { buildDefaultMaskInstance, registerDefaultMasks } from "@/masks";
import { buildDefaultEffectInstance, registerDefaultEffects } from "@/effects";
import { buildSubtitleTextElement } from "@/subtitles/build-subtitle-text-element";
import { mediaTimeToSeconds } from "@/wasm";
import { usePropertiesStore } from "@/components/editor/panels/properties/stores/properties-store";
import { getVisibleElementsWithBounds } from "@/preview/element-bounds";
import { FONT_SIZE_SCALE_REFERENCE } from "@/text/typography";

const BLUR_LAYER = "Làm mờ sub gốc";

/** Controls operate on OpenCut's native tracks, effects and masks, including export and undo. */
export function SubtitleTools({ trackId }: { trackId?: string }) {
	const editor = useEditor();
	const scene = useEditor((e) => e.scenes.getActiveSceneOrNull());
	const [fontSize, setFontSize] = useState(48);
	const [color, setColor] = useState("#ffffff");
	const [box, setBox] = useState(false);
	const [opacity, setOpacity] = useState(60);
	const [position, setPosition] = useState(95);
	const [autoFit, setAutoFit] = useState(true);
	const [blurY, setBlurY] = useState(85);
	const [blurHeight, setBlurHeight] = useState(16);
	const [blurWidth, setBlurWidth] = useState(100);
	const [blurX, setBlurX] = useState(50);
	const [intensity, setIntensity] = useState(60);
	const [working, setWorking] = useState(false);
	const blurElements =
		scene?.tracks.overlay.flatMap((t) =>
			t.elements
				.filter((e) => e.name === BLUR_LAYER)
				.map((e) => ({ trackId: t.id, elementId: e.id })),
		) || [];
	function subtitleTrack() {
		return editor.scenes
			.getActiveScene()
			.tracks.overlay.find((t) => t.id === trackId && t.type === "text");
	}
	function selectSubtitle() {
		const track = subtitleTrack();
		if (!track || track.type !== "text" || !track.elements.length) return;
		editor.playback.pause();
		const time = editor.playback.getCurrentTime();
		const cue =
			track.elements.find(
				(e) => time >= e.startTime && time < e.startTime + e.duration,
			) ??
			track.elements.find((e) => e.startTime >= time) ??
			track.elements[0];
		if (time < cue.startTime || time >= cue.startTime + cue.duration)
			editor.playback.seek({ time: cue.startTime });
		editor.selection.setSelectedElements({
			elements: [{ trackId: track.id, elementId: cue.id }],
		});
		usePropertiesStore
			.getState()
			.setActiveTab({ elementType: "text", tabId: "text" });
	}
	function selectBlur() {
		const current = editor.scenes.getActiveScene();
		const time = editor.playback.getCurrentTime();
		const entries = current.tracks.overlay.flatMap((t) =>
			t.elements
				.filter((e) => e.name === BLUR_LAYER && e.type === "video")
				.map((e) => ({ trackId: t.id, element: e })),
		);
		const entry =
			entries.find(
				(e) =>
					time >= e.element.startTime &&
					time < e.element.startTime + e.element.duration,
			) ?? entries[0];
		if (!entry) return;
		editor.playback.pause();
		if (
			time < entry.element.startTime ||
			time >= entry.element.startTime + entry.element.duration
		)
			editor.playback.seek({ time: entry.element.startTime });
		editor.selection.setSelectedElements({
			elements: [{ trackId: entry.trackId, elementId: entry.element.id }],
		});
		usePropertiesStore
			.getState()
			.setActiveTab({ elementType: "video", tabId: "masks" });
	}
	async function moveAllSubtitles(positionX: number, positionY: number) {
		const track = subtitleTrack();
		if (!track || track.type !== "text") return;
		editor.timeline.updateElements({
			updates: track.elements.map((e) => ({
				trackId: track.id,
				elementId: e.id,
				patch: {
					params: {
						...e.params,
						"transform.positionX": positionX,
						"transform.positionY": positionY,
					},
				},
			})),
		});
		await editor.save.flush();
		selectSubtitle();
		toast.success("Đã đặt toàn bộ sub vào vị trí này. Ctrl+Z để hoàn tác.");
	}
	async function useSelectedPosition() {
		const selected = editor.selection.getSelectedElements();
		const track = subtitleTrack();
		const cue =
			selected.length === 1 && selected[0].trackId === track?.id
				? track.elements.find((e) => e.id === selected[0].elementId)
				: undefined;
		if (!cue || cue.type !== "text") {
			toast.info(
				"Bấm ‘Chọn chữ để kéo’, kéo chữ tới vị trí mong muốn rồi bấm nút này.",
			);
			return;
		}
		await moveAllSubtitles(
			Number(cue.params["transform.positionX"] ?? 0),
			Number(cue.params["transform.positionY"] ?? 0),
		);
	}
	async function alignSubtitlesToBlur() {
		const canvas = editor.project.getActive().settings.canvasSize;
		const entry = getVisibleElementsWithBounds({
			tracks: editor.scenes.getActiveScene().tracks,
			currentTime: editor.playback.getCurrentTime(),
			canvasSize: canvas,
			mediaAssets: editor.media.getAssets(),
		}).find((e) => e.element.name === BLUR_LAYER && e.element.type === "video");
		if (!entry || entry.element.type !== "video") {
			toast.info(
				"Áp dụng vùng làm mờ và đặt con trỏ vào đoạn video có vùng mờ trước.",
			);
			return;
		}
		const mask = entry.element.masks?.find((m) => m.type === "rectangle");
		if (!mask) return;
		// Convert the mask's source-local center to preview canvas coordinates,
		// accounting for the fitted video, scale and rotation (including letterboxing).
		const x = Number(mask.params.centerX ?? 0) * entry.bounds.width;
		const y = Number(mask.params.centerY ?? 0) * entry.bounds.height;
		const angle = (entry.bounds.rotation * Math.PI) / 180;
		await moveAllSubtitles(
			entry.bounds.cx +
				x * Math.cos(angle) -
				y * Math.sin(angle) -
				canvas.width / 2,
			entry.bounds.cy +
				x * Math.sin(angle) +
				y * Math.cos(angle) -
				canvas.height / 2,
		);
	}
	async function applyStyle(repair = false) {
		const track = editor.scenes
			.getActiveScene()
			.tracks.overlay.find((t) => t.id === trackId && t.type === "text");
		if (!track || track.type !== "text") {
			toast.info("Dịch hoặc nhập file SRT/ASS trước để có sub trên timeline.");
			return;
		}
		const canvas = editor.project.getActive().settings.canvasSize;
		editor.timeline.updateElements({
			updates: track.elements.map((e, index) => {
				const styled = buildSubtitleTextElement({
					index,
					canvasSize: canvas,
					fitToVideo: repair || autoFit,
					caption: {
						text: String(e.params.content || ""),
						startTime: mediaTimeToSeconds({ time: e.startTime }),
						duration: mediaTimeToSeconds({ time: e.duration }),
						style: {
							fontSize: repair
								? undefined
								: (fontSize * FONT_SIZE_SCALE_REFERENCE) / canvas.height,
							color: repair ? String(e.params.color || "#ffffff") : color,
							fontFamily: String(e.params.fontFamily || "Arial"),
							background: {
								enabled: repair ? Boolean(e.params["background.enabled"]) : box,
								color: repair
									? String(e.params["background.color"] || "#00000099")
									: `#000000${Math.round(opacity * 2.55)
											.toString(16)
											.padStart(2, "0")}`,
							},
							placement: {
								verticalAlign: "bottom",
								marginVerticalRatio: repair ? 0.05 : 1 - position / 100,
							},
						},
					},
				});
				return {
					trackId: track.id,
					elementId: e.id,
					patch: {
						params: {
							...e.params,
							content: styled.params.content,
							fontSize: styled.params.fontSize,
							color: styled.params.color,
							lineHeight: styled.params.lineHeight,
							"transform.positionX": styled.params["transform.positionX"],
							"background.enabled": styled.params["background.enabled"],
							"background.color": styled.params["background.color"],
							"transform.positionY": styled.params["transform.positionY"],
						},
					},
				};
			}),
		});
		await editor.save.flush();
		if (track.elements[0]) {
			editor.selection.setSelectedElements({
				elements: [{ trackId: track.id, elementId: track.elements[0].id }],
			});
		}
		toast.success(
			"Đã áp dụng cho toàn bộ sub. Kéo chữ trên preview để đặt vị trí riêng.",
		);
	}
	async function applyBlur() {
		setWorking(true);
		try {
			const current = editor.scenes.getActiveScene();
			const sources = current.tracks.main.elements.filter(
				(e) => e.type === "video",
			);
			if (!sources.length) {
				toast.info("Đưa video vào timeline trước.");
				return;
			}
			registerDefaultMasks();
			registerDefaultEffects();
			const add = new AddTrackCommand({
				type: "video",
				index: current.tracks.overlay.length,
			});
			const inserts = sources.map((source) => {
				const { id: _id, ...element } = source;
				const mask = buildDefaultMaskInstance({ maskType: "rectangle" });
				if (mask.type !== "rectangle")
					throw new Error("Không tạo được vùng làm mờ.");
				const blur = buildDefaultEffectInstance({ effectType: "blur" });
				return new InsertElementCommand({
					placement: { mode: "explicit", trackId: add.getTrackId() },
					element: {
						...element,
						name: BLUR_LAYER,
						isSourceAudioEnabled: false,
						effects: [
							...(source.effects || []),
							{ ...blur, params: { ...blur.params, intensity } },
						],
						masks: [
							{
								...mask,
								params: {
									...mask.params,
									width: blurWidth / 100,
									height: blurHeight / 100,
									centerX: blurX / 100 - 0.5,
									centerY: blurY / 100 - 0.5,
								},
							},
							...(source.masks || []),
						],
					},
				});
			});
			editor.command.execute({
				command: new BatchCommand([
					...(blurElements.length
						? [new DeleteElementsCommand({ elements: blurElements })]
						: []),
					add,
					...inserts,
				]),
			});
			await editor.save.flush();
			selectBlur();
			toast.success("Vùng làm mờ đã hiện trên preview và có trong video xuất.");
		} catch (e) {
			toast.error(
				e instanceof Error ? e.message : "Không tạo được vùng làm mờ.",
			);
		} finally {
			setWorking(false);
		}
	}
	const fieldClass = "w-full rounded-md border bg-background p-2";
	return (
		<div className="space-y-3">
			<details
				id="translate-subtitle-style"
				open
				className="rounded-lg border p-3 space-y-3"
			>
				<summary className="font-semibold text-sm cursor-pointer">
					Kiểu và vị trí phụ đề
				</summary>
				<Button
					className="w-full"
					variant="outline"
					disabled={!trackId}
					onClick={selectSubtitle}
				>
					Chọn chữ để kéo trên video
				</Button>
				<Button
					className="w-full"
					variant="outline"
					disabled={!trackId}
					onClick={() => void useSelectedPosition()}
				>
					Dùng vị trí chữ đang chọn cho tất cả sub
				</Button>
				<Button
					className="w-full"
					variant="outline"
					disabled={!trackId}
					onClick={() => void applyStyle(true)}
				>
					Tự căn sub theo video dọc / ngang
				</Button>
				<label className="flex items-center gap-2 text-xs">
					<input
						type="checkbox"
						checked={autoFit}
						onChange={(e) => setAutoFit(e.target.checked)}
					/>
					Tự vừa khung, ưu tiên 1–2 dòng
				</label>
				<div className="grid grid-cols-2 gap-3 text-xs mt-3">
					<label>
						Cỡ chữ tối đa (px)
						<input
							aria-label="Cỡ chữ sub"
							className={fieldClass}
							type="number"
							min={16}
							max={120}
							value={fontSize}
							onChange={(e) =>
								setFontSize(Math.max(16, Math.min(120, Number(e.target.value))))
							}
						/>
					</label>
					<label>
						Màu chữ
						<input
							aria-label="Màu chữ sub"
							type="color"
							className="w-full h-9 rounded border bg-background"
							value={color}
							onChange={(e) => setColor(e.target.value)}
						/>
					</label>
				</div>
				<label className="flex items-center gap-2 text-xs">
					<input
						type="checkbox"
						checked={box}
						onChange={(e) => setBox(e.target.checked)}
					/>
					Nền đen sau chữ
				</label>
				{box && (
					<label className="block text-xs">
						Độ đậm nền: {opacity}%
						<input
							aria-label="Độ đậm nền sub"
							className="w-full"
							type="range"
							min={0}
							max={100}
							value={opacity}
							onChange={(e) => setOpacity(Number(e.target.value))}
						/>
					</label>
				)}
				<label className="block text-xs">
					Vị trí dọc: {position}%
					<input
						aria-label="Vị trí dọc sub"
						className="w-full"
						type="range"
						min={8}
						max={95}
						value={position}
						onChange={(e) => setPosition(Number(e.target.value))}
					/>
				</label>
				<Button
					className="w-full"
					variant="outline"
					disabled={!trackId}
					onClick={() => void applyStyle()}
				>
					Áp dụng kiểu cho tất cả sub
				</Button>
				<p className="text-xs text-muted-foreground">
					Bấm đoạn chữ trên timeline rồi kéo trực tiếp chữ trong khung video để
					di chuyển. Font, màu và các thuộc tính của đoạn đang chọn nằm bên
					phải.
				</p>
			</details>
			<details
				id="translate-blur"
				open
				className="rounded-lg border p-3 space-y-3"
			>
				<summary className="font-semibold text-sm cursor-pointer">
					Làm mờ sub gốc / vùng trên video
				</summary>
				<div className="grid grid-cols-2 gap-3 text-xs mt-3">
					{[
						["Tâm ngang (%)", blurX, setBlurX, 0, 100],
						["Tâm dọc (%)", blurY, setBlurY, 0, 100],
						["Rộng (%)", blurWidth, setBlurWidth, 1, 100],
						["Cao (%)", blurHeight, setBlurHeight, 1, 100],
					].map(([label, value, set, min, max]) => (
						<label key={String(label)}>
							{String(label)}
							<input
								aria-label={String(label)}
								className={fieldClass}
								type="number"
								min={Number(min)}
								max={Number(max)}
								key={`${String(label)}:${Number(value)}`}
								step={0.1}
								defaultValue={Number(value)}
								onBlur={(e) => {
									const raw = e.target.value.trim();
									const parsed = Number(raw);
									const next = raw && Number.isFinite(parsed)
										? Math.max(Number(min), Math.min(Number(max), parsed))
										: Number(value);
									e.target.value = String(next);
									(set as (v: number) => void)(next);
								}}
								onKeyDown={(e) => {
									if (e.key === "Enter") e.currentTarget.blur();
								}}
							/>
						</label>
					))}
				</div>
				<label className="block text-xs">
					Mức làm mờ: {intensity}
					<input
						aria-label="Mức làm mờ"
						className="w-full"
						type="range"
						min={5}
						max={100}
						value={intensity}
						onChange={(e) => setIntensity(Number(e.target.value))}
					/>
				</label>
				<Button
					className="w-full"
					variant="outline"
					disabled={working || !scene?.tracks.main.elements.length}
					onClick={() => void applyBlur()}
				>
					Áp dụng vùng làm mờ
				</Button>
				<Button
					className="w-full"
					variant="outline"
					disabled={!blurElements.length}
					onClick={selectBlur}
				>
					Chọn vùng mờ để kéo / đổi kích thước
				</Button>
				<Button
					className="w-full"
					variant="outline"
					disabled={!trackId || !blurElements.length}
					onClick={() => void alignSubtitlesToBlur()}
				>
					Đặt tất cả sub vào vùng mờ
				</Button>
				{blurElements.length > 0 && (
					<Button
						variant="outline"
						className="w-full"
						onClick={async () => {
							editor.timeline.deleteElements({ elements: blurElements });
							await editor.save.flush();
						}}
					>
						Gỡ vùng làm mờ
					</Button>
				)}
				<p className="text-xs text-muted-foreground">
					Chọn vùng mờ: kéo khung tới chữ gốc, kéo các chấm ở cạnh để đổi kích
					thước. Sau đó bấm “Đặt tất cả sub vào vùng mờ”. Vị trí này do bạn
					chọn, chưa tự nhận diện chữ gốc. Cắt video xong, bấm áp dụng lại để
					vùng mờ khớp các đoạn đã cắt.
				</p>
			</details>
		</div>
	);
}
