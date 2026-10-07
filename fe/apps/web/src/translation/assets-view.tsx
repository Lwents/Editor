"use client";

import { shortSubtitleCues } from "./short-subtitles";
import {
	BatchCommand,
	DeleteElementsCommand,
	InsertElementCommand,
} from "@/commands";
import { buildSubtitleTextElement } from "@/subtitles/build-subtitle-text-element";
import { FONT_SIZE_SCALE_REFERENCE } from "@/text/typography";
import { SubtitleTools } from "./subtitle-tools";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { PanelView } from "@/components/editor/panels/assets/views/base-panel";
import { useEditor } from "@/editor/use-editor";
import { processMediaAssets } from "@/media/processing";
import { buildElementFromMedia } from "@/timeline/element-utils";
import { parseSubtitleFile } from "@/subtitles/parse";
import { insertCaptionChunksAsTextTrack } from "@/subtitles/insert";
import {
	mediaTimeFromSeconds,
	mediaTimeToSeconds,
	ZERO_MEDIA_TIME,
} from "@/wasm";
import type { TextElement } from "@/timeline";

const API = "/api/auto-translate/api";
type Job = {
	job_id: string;
	status: string;
	progress: number;
	stage: string;
	error?: string;
};
type Binding = {
	jobId?: string;
	sceneId: string;
	trackId?: string;
	name: string;
	voice?: boolean;
};
async function api<T>(path: string, options?: RequestInit): Promise<T> {
	const response = await fetch(`${API}${path}`, options);
	if (!response.ok) {
		const data = await response.json().catch(() => null);
		throw new Error(
			typeof data?.detail === "string"
				? data.detail
				: `Yêu cầu thất bại (${response.status})`,
		);
	}
	return response.json();
}

export function TranslationView() {
	const editor = useEditor();
	const projectId = useEditor((e) => e.project.getActive().metadata.id);
	const scene = useEditor((e) => e.scenes.getActiveSceneOrNull());
	const media = useEditor((e) => e.media.getAssets());
	const [binding, setBinding] = useState<Binding | null>(null);
	const [ready, setReady] = useState(false);
	const [job, setJob] = useState<Job | null>(null);
	const [busy, setBusy] = useState(false);
	const [stage, setStage] = useState("");
	const [error, setError] = useState("");
	const [language, setLanguage] = useState("auto");
	const [voice, setVoice] = useState(false);
	const [shortLines, setShortLines] = useState(true);
	const [selectedMedia, setSelectedMedia] = useState("");
	const mounted = useRef(false);
	const importing = useRef(false);
	const autoImportAttempt = useRef<string | null>(null);
	useEffect(() => {
		mounted.current = true;
		return () => {
			mounted.current = false;
		};
	}, []);
	function isCurrentProject() {
		return (
			mounted.current &&
			editor.project.getActiveOrNull()?.metadata.id === projectId
		);
	}

	useEffect(() => {
		if (!media.some((a) => a.id === selectedMedia && a.type === "video")) {
			const sourceId = scene?.tracks.main.elements.find(
				(e) => e.type === "video",
			)?.mediaId;
			setSelectedMedia(
				media.find((a) => a.id === sourceId && a.type === "video")?.id ||
					media.find((a) => a.type === "video")?.id ||
					"",
			);
		}
	}, [media, scene, selectedMedia]);
	const videoInput = useRef<HTMLInputElement>(null);
	const subInput = useRef<HTMLInputElement>(null);
	const key = `auto-translate-editor:${projectId}`;
	useEffect(() => {
		setReady(false);
		setJob(null);
		setError("");
		try {
			setBinding(JSON.parse(localStorage.getItem(key) || "null"));
		} catch {
			setBinding(null);
		}
		setReady(true);
	}, [key]);
	useEffect(() => {
		if (ready) {
			if (binding) localStorage.setItem(key, JSON.stringify(binding));
			else localStorage.removeItem(key);
		}
	}, [key, ready, binding]);
	useEffect(() => {
		if (!binding?.jobId) return;
		let stopped = false;
		let timer: ReturnType<typeof setTimeout>;
		const poll = async () => {
			try {
				const next = await api<Job>(`/jobs/${binding.jobId}`);
				if (stopped) return;
				setJob(next);
				setError(
					next.status === "failed" ? next.error || "Dịch thất bại." : "",
				);
				if (!["completed", "failed", "cancelled"].includes(next.status))
					timer = setTimeout(poll, 2500);
			} catch (e) {
				if (!stopped) {
					setError(e instanceof Error ? e.message : "Mất kết nối.");
					timer = setTimeout(poll, 5000);
				}
			}
		};
		void poll();
		return () => {
			stopped = true;
			clearTimeout(timer);
		};
	}, [binding?.jobId]);
	const running =
		job && !["completed", "failed", "cancelled"].includes(job.status);
	const locked = busy || Boolean(running) || Boolean(binding?.jobId && !job);
	const track = scene?.tracks.overlay.find(
		(t) => t.type === "text" && (!binding?.trackId || t.id === binding.trackId),
	);
	const cues = track?.type === "text" ? track.elements : [];

	async function addVideo(file: File) {
		const [asset] = await processMediaAssets({ files: [file] });
		if (!asset || asset.type !== "video")
			throw new Error("Video không được trình duyệt hỗ trợ.");
		const saved = await editor.media.addMediaAsset({ projectId, asset });
		if (!saved) throw new Error("Không đủ dung lượng để lưu video.");
		setSelectedMedia(saved.id);
		const current = editor.scenes.getActiveScene();
		if (
			!current.tracks.main.elements.length &&
			!current.tracks.overlay.length &&
			!current.tracks.audio.length
		) {
			editor.timeline.insertElement({
				element: buildElementFromMedia({
					mediaId: saved.id,
					mediaType: "video",
					name: saved.name,
					duration: mediaTimeFromSeconds({ seconds: saved.duration || 0 }),
					startTime: ZERO_MEDIA_TIME,
				}),
				placement: { mode: "auto" },
			});
		}
		return saved;
	}
	async function startTranslation() {
		const asset = media.find(
			(a) => a.id === selectedMedia && a.type === "video",
		);
		if (!asset) return;
		setBusy(true);
		setError("");
		setJob(null);
		try {
			setStage("Đang tải video lên AutoTranslateAI…");
			const form = new FormData();
			form.append("file", asset.file, asset.name);
			const uploaded = await api<{ local_file_path: string }>(
				"/uploads/video",
				{ method: "POST", body: form },
			);
			const created = await api<Job>("/jobs", {
				method: "POST",
				headers: { "content-type": "application/json" },
				body: JSON.stringify({
					local_file_path: uploaded.local_file_path,
					source_language: language,
					editor_subtitles_only: !voice,
					editable_subtitles: true,
					hard_subtitles: true,
					video_speed: 1,
					processing_mode: "balanced",
					bgm_mode: voice ? "ducking" : "none",
					use_demucs: false,
					logo_enabled: false,
				}),
			});
			// A separate scene keeps previous edits intact and subtitle timestamps aligned to the source.
			const sceneId = await editor.scenes.createScene({
				name: `Dịch · ${asset.name}`,
				isMain: false,
			});
			await editor.scenes.switchToScene({ sceneId });
			editor.timeline.insertElement({
				element: buildElementFromMedia({
					mediaId: asset.id,
					mediaType: "video",
					name: asset.name,
					duration: mediaTimeFromSeconds({ seconds: asset.duration || 0 }),
					startTime: ZERO_MEDIA_TIME,
				}),
				placement: { mode: "auto" },
			});
			setBinding({ jobId: created.job_id, sceneId, name: asset.name, voice });
			setJob(created);
		} catch (e) {
			setError(e instanceof Error ? e.message : "Không thể bắt đầu dịch.");
		} finally {
			setBusy(false);
			setStage("");
		}
	}
	async function importSubtitles(
		input: string,
		fileName: string,
		target?: Binding,
	) {
		const parsed = parseSubtitleFile({ input, fileName });
		if (!parsed.captions.length)
			throw new Error("File không có phụ đề hợp lệ.");
		if (target) await editor.scenes.switchToScene({ sceneId: target.sceneId });
		if (!isCurrentProject())
			throw new Error("Đã chuyển dự án. Mở lại dự án này để xem bản dịch.");

		const canvasSize = editor.project.getActive().settings.canvasSize;
		const captions = shortLines
			? parsed.captions.flatMap((c) => shortSubtitleCues(c, canvasSize))
			: parsed.captions;
		const trackId = insertCaptionChunksAsTextTrack({
			editor,
			captions,
			fitToVideo: shortLines ? true : undefined,
		});
		if (!trackId) throw new Error("Không thể thêm phụ đề.");
		const next = {
			...(target || {
				name: fileName,
				sceneId: editor.scenes.getActiveScene().id,
			}),
			trackId,
		};
		localStorage.setItem(key, JSON.stringify(next));
		setBinding(next);
		await editor.save.flush();
		const first = editor.scenes
			.getActiveScene()
			.tracks.overlay.find((t) => t.id === trackId);
		if (first?.elements[0]) {
			editor.selection.setSelectedElements({
				elements: [{ trackId, elementId: first.elements[0].id }],
			});
			editor.playback.seek({
				time: mediaTimeFromSeconds({
					seconds:
						parsed.captions[0].startTime +
						Math.min(0.15, parsed.captions[0].duration / 2),
				}),
			});
		}
		if (parsed.warnings.length) toast.warning(parsed.warnings.join(" · "));
		toast.success(`Đã thêm ${captions.length} đoạn sub vào timeline.`);
	}
	async function splitExistingSubtitles() {
		if (!track || track.type !== "text" || !cues.length) return;
		const canvasSize = editor.project.getActive().settings.canvasSize;
		const insertions = cues.flatMap((cue) => {
			const style = {
				fontSize: Number(cue.params.fontSize),
				fontFamily: String(cue.params.fontFamily),
				fontWeight: cue.params.fontWeight as "normal" | "bold",
				fontStyle: cue.params.fontStyle as "normal" | "italic",
				lineHeight: Number(cue.params.lineHeight),
			};
			const oldHeight =
				(String(cue.params.content).split("\n").length *
					Number(cue.params.fontSize) *
					Number(cue.params.lineHeight) *
					canvasSize.height) /
				FONT_SIZE_SCALE_REFERENCE;
			const caption = {
				text: String(cue.params.content),
				startTime: mediaTimeToSeconds({ time: cue.startTime }),
				duration: mediaTimeToSeconds({ time: cue.duration }),
				style,
			};
			return shortSubtitleCues(caption, canvasSize).map((chunk, index) => {
				const fresh = buildSubtitleTextElement({
					index,
					caption: chunk,
					canvasSize,
					fitToVideo: true,
				});
				const height =
					(Number(fresh.params.fontSize) *
						Number(fresh.params.lineHeight) *
						canvasSize.height) /
					FONT_SIZE_SCALE_REFERENCE;
				return new InsertElementCommand({
					placement: { mode: "explicit", trackId: track.id },
					element: {
						...fresh,
						params: {
							...cue.params,
							fontSize: fresh.params.fontSize,
							lineHeight: fresh.params.lineHeight,
							content: chunk.text,
							"transform.positionX": cue.params["transform.positionX"],
							"transform.positionY":
								Number(cue.params["transform.positionY"]) +
								(oldHeight - height) / 2,
						},
						effects: cue.effects,
					},
				});
			});
		});
		if (!insertions.length) return;
		editor.command.execute({
			command: new BatchCommand([
				new DeleteElementsCommand({
					elements: cues.map((c) => ({ trackId: track.id, elementId: c.id })),
				}),
				...insertions,
			]),
		});
		setShortLines(true);
		editor.selection.setSelectedElements({
			elements: [
				{ trackId: track.id, elementId: insertions[0].getElementId() },
			],
		});
		await editor.save.flush();
		toast.success(
			`Đã chia thành ${insertions.length} đoạn sub ngắn. Ctrl+Z để hoàn tác.`,
		);
	}
	async function importResult(withVoice = binding?.voice) {
		if (!binding?.jobId || importing.current || binding.trackId) return;
		importing.current = true;
		setBusy(true);
		setError("");
		try {
			const response = await fetch(`${API}/jobs/${binding.jobId}/subtitles`);
			if (!response.ok) throw new Error("Chưa tải được phụ đề dịch.");
			const input = await response.text();
			if (!isCurrentProject()) return;
			let target = binding;
			if (withVoice) {
				setStage("Đang nhập video thuyết minh…");
				const output = await fetch(`${API}/jobs/${binding.jobId}/download`);
				if (!output.ok) throw new Error("Không tải được video thuyết minh.");
				const outputBlob = await output.blob();
				if (!isCurrentProject()) return;
				const asset = await addVideo(
					new File([outputBlob], `Tiếng Việt · ${binding.name}.mp4`, {
						type: "video/mp4",
					}),
				);
				const sceneId = await editor.scenes.createScene({
					name: `Thuyết minh · ${binding.name}`,
					isMain: false,
				});
				await editor.scenes.switchToScene({ sceneId });
				editor.timeline.insertElement({
					element: buildElementFromMedia({
						mediaId: asset.id,
						mediaType: "video",
						name: asset.name,
						duration: mediaTimeFromSeconds({ seconds: asset.duration || 0 }),
						startTime: ZERO_MEDIA_TIME,
					}),
					placement: { mode: "auto" },
				});
				target = { ...binding, sceneId };
			}
			await importSubtitles(input, "translation.srt", target);
		} catch (e) {
			setError(e instanceof Error ? e.message : "Không thể nhập sub.");
		} finally {
			importing.current = false;
			setBusy(false);
			setStage("");
		}
	}
	useEffect(() => {
		if (
			!ready ||
			busy ||
			job?.status !== "completed" ||
			!binding?.jobId ||
			binding.trackId ||
			scene?.id !== binding.sceneId ||
			autoImportAttempt.current === binding.jobId
		)
			return;
		autoImportAttempt.current = binding.jobId;
		void importResult();
	}, [ready, busy, job?.status, binding?.jobId, binding?.trackId, scene?.id]);
	useEffect(() => {
		if (
			binding?.trackId &&
			scene?.id === binding.sceneId &&
			job?.status === "completed"
		) {
			document
				.getElementById("translation-review")
				?.scrollIntoView({ behavior: "smooth", block: "start" });
		}
	}, [binding?.trackId, binding?.sceneId, scene?.id, job?.status]);
	function focusCue(cue: TextElement) {
		if (!track) return;
		editor.selection.setSelectedElements({
			elements: [{ trackId: track.id, elementId: cue.id }],
		});
		editor.playback.seek({
			time: mediaTimeFromSeconds({
				seconds:
					mediaTimeToSeconds({ time: cue.startTime }) +
					Math.min(0.15, mediaTimeToSeconds({ time: cue.duration }) / 2),
			}),
		});
	}
	async function updateCue(cue: TextElement, content: string) {
		if (!track || content === String(cue.params.content || "")) return;
		const canvas = editor.project.getActive().settings.canvasSize;
		const styled = buildSubtitleTextElement({
			index: 0,
			canvasSize: canvas,
			fitToVideo: true,
			caption: {
				text: content,
				startTime: mediaTimeToSeconds({ time: cue.startTime }),
				duration: mediaTimeToSeconds({ time: cue.duration }),
				style: {
					fontSize: Number(cue.params.fontSize || 5),
					fontFamily: String(cue.params.fontFamily || "Arial"),
					fontWeight: cue.params.fontWeight === "normal" ? "normal" : "bold",
					fontStyle: cue.params.fontStyle === "italic" ? "italic" : "normal",
					lineHeight: Number(cue.params.lineHeight || 1.2),
				},
			},
		});
		const oldHeight =
			(String(cue.params.content || "").split("\n").length *
				Number(cue.params.fontSize || 5) *
				Number(cue.params.lineHeight || 1.2) *
				canvas.height) /
			FONT_SIZE_SCALE_REFERENCE;
		const newHeight =
			(String(styled.params.content || "").split("\n").length *
				Number(styled.params.fontSize) *
				Number(styled.params.lineHeight) *
				canvas.height) /
			FONT_SIZE_SCALE_REFERENCE;
		editor.timeline.updateElements({
			updates: [
				{
					trackId: track.id,
					elementId: cue.id,
					patch: {
						params: {
							...cue.params,
							content: styled.params.content,
							fontSize: styled.params.fontSize,
							lineHeight: styled.params.lineHeight,
							"transform.positionY":
								Number(cue.params["transform.positionY"] || 0) +
								(oldHeight - newHeight) / 2,
						},
					},
				},
			],
		});
		try {
			await editor.save.flush();
		} catch {
			setError(
				"Chưa lưu được thay đổi phụ đề. Hãy kiểm tra dung lượng trình duyệt.",
			);
		}
	}
	return (
		<PanelView
			title="Dịch phim"
			actions={
				<div className="flex gap-1">
					<Button
						variant="outline"
						size="sm"
						onClick={() =>
							document
								.getElementById("translate-subtitle-style")
								?.scrollIntoView({ behavior: "smooth", block: "start" })
						}
					>
						Sub
					</Button>
					<Button
						variant="outline"
						size="sm"
						onClick={() =>
							document
								.getElementById("translate-blur")
								?.scrollIntoView({ behavior: "smooth", block: "start" })
						}
					>
						Làm mờ
					</Button>
				</div>
			}
			contentClassName="space-y-4 p-3 pb-6"
		>
			<div className="rounded-lg border bg-accent/40 p-3 space-y-2">
				<h3 className="font-semibold">Dịch, xem và sửa ngay trên video</h3>
				<p className="text-xs text-muted-foreground leading-relaxed">
					Video trên timeline được chọn sẵn. Dịch xong, từng đoạn sub tự hiện
					trên video và timeline để bạn xem và sửa. Bấm một đoạn sub để xem và
					chỉnh kiểu chữ bên phải. Nút Export xuất video cùng các chỉnh sửa.
				</p>
			</div>
			<input
				ref={videoInput}
				type="file"
				accept="video/*"
				hidden
				onChange={async (e) => {
					const file = e.target.files?.[0];
					e.target.value = "";
					if (!file) return;
					setBusy(true);
					setError("");
					setStage("Đang nhập video…");
					try {
						await addVideo(file);
					} catch (e) {
						setError(e instanceof Error ? e.message : "Không nhập được video.");
					} finally {
						setBusy(false);
						setStage("");
					}
				}}
			/>
			<Button
				className="w-full"
				variant="outline"
				disabled={locked}
				onClick={() => videoInput.current?.click()}
			>
				＋ Chọn video từ máy
			</Button>
			<label className="block text-xs space-y-2">
				Video cần dịch
				<select
					aria-label="Video cần dịch"
					className="w-full bg-background rounded-md border p-2 text-sm"
					value={selectedMedia}
					disabled={locked}
					onChange={(e) => setSelectedMedia(e.target.value)}
				>
					<option value="">Chọn video trong thư viện</option>
					{media
						.filter((a) => a.type === "video")
						.map((a) => (
							<option key={a.id} value={a.id}>
								{a.name}
							</option>
						))}
				</select>
			</label>
			<label className="block text-xs space-y-2">
				Ngôn ngữ gốc
				<select
					aria-label="Ngôn ngữ gốc"
					className="w-full bg-background rounded-md border p-2 text-sm"
					value={language}
					disabled={locked}
					onChange={(e) => setLanguage(e.target.value)}
				>
					<option value="auto">Tự nhận diện</option>
					<option value="en">Tiếng Anh</option>
					<option value="zh">Tiếng Trung</option>
					<option value="vi">Tiếng Việt</option>
				</select>
			</label>
			<label className="flex items-start gap-2 text-xs leading-relaxed">
				<input
					aria-label="Sub ngắn, mỗi đoạn 1 dòng"
					type="checkbox"
					checked={shortLines}
					disabled={locked}
					onChange={(e) => setShortLines(e.target.checked)}
				/>
				Sub ngắn, mỗi đoạn 1 dòng
			</label>
			<label className="flex items-start gap-2 text-xs leading-relaxed">
				<input
					type="checkbox"
					className="mt-0.5"
					checked={voice}
					disabled={locked}
					onChange={(e) => setVoice(e.target.checked)}
				/>
				Thêm giọng thuyết minh tiếng Việt (xử lý lâu hơn)
			</label>
			<Button
				className="w-full"
				disabled={locked || !selectedMedia}
				onClick={() => void startTranslation()}
			>
				Dịch sang tiếng Việt
			</Button>
			<p className="text-xs text-muted-foreground">
				{voice
					? "Tạo bản thuyết minh, giữ sub riêng để sửa."
					: "Giữ âm thanh gốc."}{" "}
				AI dùng cấu hình 9router của backend. Mỗi lần dịch tạo một cảnh riêng để
				bạn tiếp tục cắt và sửa video.
			</p>
			{(busy || job) && (
				<div className="rounded-md border p-3 space-y-2" aria-live="polite">
					<p className="text-xs">{stage || job?.stage}</p>
					<progress
						className="w-full accent-blue-500"
						max={100}
						value={job?.progress || 0}
					/>
					{running && (
						<Button
							variant="outline"
							size="sm"
							onClick={async () => {
								try {
									await api(`/jobs/${binding?.jobId}/cancel`, {
										method: "POST",
									});
									setJob(null);
									setBinding(null);
								} catch (e) {
									setError(e instanceof Error ? e.message : "Không hủy được.");
								}
							}}
						>
							Hủy dịch
						</Button>
					)}
					{job?.status === "completed" && !binding?.trackId && (
						<Button
							className="w-full"
							disabled={busy}
							onClick={() => void importResult()}
						>
							Mở bản dịch để kiểm tra
						</Button>
					)}
				</div>
			)}
			{job?.status === "failed" && binding?.voice && !binding.trackId && (
				<Button
					variant="outline"
					className="w-full"
					disabled={busy}
					onClick={() => void importResult(false)}
				>
					Dùng sub đã dịch, giữ âm thanh gốc
				</Button>
			)}
			{error && (
				<div
					className="text-sm text-destructive rounded-md border p-3"
					role="alert"
				>
					{error}
					<Button
						className="mt-2"
						variant="outline"
						size="sm"
						onClick={() => {
							setJob(null);
							setBinding(null);
							setError("");
						}}
					>
						Bắt đầu lại
					</Button>
				</div>
			)}
			{binding?.trackId && scene?.id !== binding.sceneId && (
				<Button
					variant="outline"
					className="w-full"
					onClick={() =>
						void editor.scenes.switchToScene({ sceneId: binding.sceneId })
					}
				>
					Mở cảnh có phụ đề
				</Button>
			)}
			<input
				ref={subInput}
				type="file"
				accept=".srt,.ass"
				hidden
				onChange={async (e) => {
					const file = e.target.files?.[0];
					e.target.value = "";
					if (!file) return;
					setBusy(true);
					setError("");
					try {
						await importSubtitles(await file.text(), file.name);
					} catch (e) {
						setError(e instanceof Error ? e.message : "Không nhập được sub.");
					} finally {
						setBusy(false);
					}
				}}
			/>
			<Button
				variant="outline"
				className="w-full"
				disabled={locked}
				onClick={() => subInput.current?.click()}
			>
				Nhập sub SRT / ASS có sẵn
			</Button>
			<div
				className="rounded-lg border-2 border-dashed p-4 text-center text-xs space-y-2"
				onDragOver={(e) => {
					e.preventDefault();
					e.dataTransfer.dropEffect = "copy";
				}}
				onDrop={async (e) => {
					e.preventDefault();
					const file = e.dataTransfer.files[0];
					if (!file) return;
					if (!/\.(srt|ass)$/i.test(file.name)) {
						setError(
							"Vùng này nhận file phụ đề .srt hoặc .ass. Video nhập bằng nút Chọn video hoặc tab Media.",
						);
						return;
					}
					setBusy(true);
					setError("");
					try {
						await importSubtitles(await file.text(), file.name);
					} catch (e) {
						setError(e instanceof Error ? e.message : "Không nhập được sub.");
					} finally {
						setBusy(false);
					}
				}}
			>
				<strong>Kéo file SRT / ASS vào đây</strong>
				<p className="text-muted-foreground">
					Sub tự hiện trên video và hàng chữ trên timeline. Kéo hai mép đoạn sub
					để đổi thời gian; bấm đoạn sub để sửa chữ.
				</p>
			</div>
			<SubtitleTools trackId={track?.id} />
			{cues.length > 0 && (
				<section id="translation-review" className="space-y-3">
					<Button
						className="w-full"
						variant="outline"
						disabled={locked}
						onClick={() => void splitExistingSubtitles()}
					>
						Chia sub ngắn · 1 dòng
					</Button>
					<h3 className="font-semibold text-sm">
						Kiểm tra bản dịch · {cues.length} đoạn
					</h3>
					<div className="rounded-lg border bg-accent/40 p-3 space-y-2">
						<p className="text-xs">
							Bản dịch đã nằm trên video. Bấm từng đoạn bên dưới để xem, sửa chữ
							rồi bấm ra ngoài để lưu. Bạn có thể xem và sửa nhiều lần; khi hài
							lòng mới bấm Export.
						</p>
						<Button
							className="w-full"
							variant="outline"
							onClick={() => {
								editor.playback.seek({ time: ZERO_MEDIA_TIME });
								editor.playback.play();
							}}
						>
							Xem lại bản dịch từ đầu
						</Button>
					</div>
					{cues.map((cue, index) => (
						<div key={cue.id} className="rounded-lg border p-2 space-y-2">
							<button
								type="button"
								className="text-xs text-blue-500 text-left w-full"
								onClick={() => focusCue(cue)}
							>
								▶ {index + 1} ·{" "}
								{mediaTimeToSeconds({ time: cue.startTime }).toFixed(2)}s –{" "}
								{mediaTimeToSeconds({
									time: (cue.startTime + cue.duration) as typeof cue.startTime,
								}).toFixed(2)}
								s
							</button>
							<Textarea
								key={`${cue.id}:${String(cue.params.content)}`}
								aria-label={`Nội dung sub ${index + 1}`}
								rows={shortLines ? 1 : 2}
								wrap={shortLines ? "off" : "soft"}
								className={
									shortLines ? "min-h-10 h-10 overflow-x-auto" : undefined
								}
								defaultValue={String(cue.params.content || "")}
								onFocus={() => focusCue(cue)}
								onBlur={(e) => updateCue(cue, e.target.value)}
							/>
						</div>
					))}
					<p className="text-xs text-muted-foreground">
						Sửa chữ rồi bấm ra ngoài để lưu. Kéo hai mép đoạn chữ trên timeline
						để chỉnh thời gian. Chọn đoạn chữ để chỉnh font, màu, vị trí ở bảng
						bên phải.
					</p>
				</section>
			)}
		</PanelView>
	);
}
