"use client";
import { useEffect, useRef, useState } from "react";
import { transitionDuration } from "opencut-wasm";
import {
	glTransitions,
	renderGlTransition,
} from "@/services/renderer/external/transitions";
import { useEditor } from "@/editor/use-editor";
import { useElementSelection } from "@/timeline/hooks/element/use-element-selection";
import { useUiLanguage } from "@/i18n/ui-language";
import { mediaTimeFromSeconds, roundMediaTime } from "@/wasm";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";

function Thumbnail({ name }: { name: string }) {
	const ref = useRef<HTMLCanvasElement>(null);
	const hovering = useRef(false);
	const draw = useRef<(progress: number) => void>(() => {});
	const frame = useRef(0);
	useEffect(() => {
		const canvas = ref.current;
		if (!canvas) return;
		const observer = new IntersectionObserver(
			(entries) => {
				if (!entries.some((e) => e.isIntersecting)) return;
				observer.disconnect();
				try {
					const a = new OffscreenCanvas(160, 100),
						b = new OffscreenCanvas(160, 100);
					const x = a.getContext("2d")!,
						y = b.getContext("2d")!;
					const gradient = x.createLinearGradient(0, 0, 160, 100);
					gradient.addColorStop(0, "#fdba74");
					gradient.addColorStop(1, "#e11d48");
					x.fillStyle = gradient;
					x.fillRect(0, 0, 160, 100);
					x.fillStyle = "white";
					x.font = "bold 48px sans-serif";
					x.fillText("A", 60, 66);
					y.fillStyle = "#2563eb";
					y.fillRect(0, 0, 160, 100);
					y.fillStyle = "white";
					y.font = "bold 48px sans-serif";
					y.fillText("B", 60, 66);
					canvas.width = 160;
					canvas.height = 100;
					draw.current = (progress) =>
						canvas
							.getContext("2d")!
							.drawImage(
								renderGlTransition(a, b, name, progress, 160, 100),
								0,
								0,
							);
					draw.current(0.5);
				} catch (e) {
					console.error(`Transition ${name}`, e);
				}
			},
			{ rootMargin: "100px" },
		);
		observer.observe(canvas);
		return () => {
			observer.disconnect();
			cancelAnimationFrame(frame.current);
		};
	}, [name]);
	return (
		<canvas
			ref={ref}
			className="w-full rounded-md"
			onMouseEnter={() => {
				hovering.current = true;
				const start = performance.now();
				const animate = (now: number) => {
					if (!hovering.current) return;
					draw.current(((now - start) % 1800) / 1800);
					frame.current = requestAnimationFrame(animate);
				};
				frame.current = requestAnimationFrame(animate);
			}}
			onMouseLeave={() => {
				hovering.current = false;
				cancelAnimationFrame(frame.current);
				draw.current(0.5);
			}}
		/>
	);
}
export function GlTransitionsPanel({ seconds }: { seconds: number }) {
	const editor = useEditor(),
		t = useUiLanguage();
	const scene = useEditor((e) => e.scenes.getActiveScene());
	const { selectedElements } = useElementSelection();
	const [choice, setChoice] = useState("");
	const [search, setSearch] = useState("");
	const [page, setPage] = useState(0);
	const tracks = [
		scene.tracks.main,
		...scene.tracks.overlay.filter((track) => track.type === "video"),
	];
	const pairs = tracks.flatMap((track) => {
		const sorted = track.elements
			.filter((e) => e.type === "video" || e.type === "image")
			.slice()
			.sort((a, b) => a.startTime - b.startTime);
		return sorted.slice(1).map((to, i) => ({ track, from: sorted[i], to }));
	});
	const selectedPair =
		pairs.find((pair) =>
			selectedElements.some((ref) => ref.elementId === pair.to.id),
		) ??
		pairs.find((pair) =>
			selectedElements.some((ref) => ref.elementId === pair.from.id),
		);
	const pair =
		pairs.find((p) => p.to.id === choice) ?? selectedPair ?? pairs[0];
	const filtered = glTransitions.filter((entry) =>
		entry.name.toLowerCase().includes(search.toLowerCase()),
	);
	const displayed = filtered.slice(page * 24, (page + 1) * 24);
	function apply(name: string) {
		if (!pair) {
			toast.info(t("Add at least two video or image clips to the same track"));
			return;
		}
		const { track, from, to } = pair;
		const old = Number(to.params["transition.duration"] ?? 0);
		const expected = from.startTime + from.duration - old;
		if (Math.abs(to.startTime - expected) > 1) {
			toast.error(t("Place two adjacent clips on the same track first"));
			return;
		}
		const duration = name
			? Number(
					transitionDuration(
						BigInt(mediaTimeFromSeconds({ seconds })),
						BigInt(from.duration),
						BigInt(to.duration),
					),
				)
			: 0;
		const delta = old - duration;
		editor.timeline.updateElements({
			updates: track.elements
				.filter((e) => e.startTime >= to.startTime)
				.map((e) => ({
					trackId: track.id,
					elementId: e.id,
					patch: {
						startTime: roundMediaTime({ time: e.startTime + delta }),
						...(e.id === to.id
							? {
									params: {
										...e.params,
										"transition.name": name,
										"transition.fromId": name ? from.id : "",
										"transition.duration": duration,
									},
								}
							: {}),
					},
				})),
		});
		editor.playback.seek({
			time: roundMediaTime({ time: to.startTime + delta }),
		});
		void editor.save.flush();
		toast.success(t(name ? "Transition applied" : "Transition removed"));
	}
	return (
		<section className="space-y-3 border-t pt-4">
			<h3 className="font-semibold">GL Transitions · {glTransitions.length}</h3>
			<p className="text-xs text-muted-foreground">
				{t("Hover to preview. Click to apply between two adjacent clips.")}
			</p>
			<p className="text-xs text-muted-foreground">
				{t(
					"Choose the incoming clip. The two clips overlap for the transition duration; later clips on this track move with it.",
				)}
			</p>
			<label className="block text-sm">
				{t("Incoming clip")}
				<select
					aria-label={t("Incoming clip")}
					className="mt-1 w-full rounded-md border bg-background p-2"
					value={pair?.to.id ?? ""}
					onChange={(e) => setChoice(e.target.value)}
				>
					<option value="">{t("Select a clip")}</option>
					{pairs.map((p) => (
						<option key={p.to.id} value={p.to.id}>
							{p.from.name} → {p.to.name}
						</option>
					))}
				</select>
			</label>
			{!pairs.length && (
				<p className="text-sm text-muted-foreground">
					{t("Add at least two video or image clips to the same track")}
				</p>
			)}
			{pair?.to.params["transition.name"] && (
				<p className="text-xs">
					{t("Current transition")}: {String(pair.to.params["transition.name"])}
				</p>
			)}
			<Button
				variant="outline"
				className="w-full"
				disabled={!pair?.to.params["transition.name"]}
				onClick={() => apply("")}
			>
				{t("Remove transition")}
			</Button>
			<input
				aria-label={t("Search transitions")}
				placeholder={t("Search transitions")}
				className="w-full rounded-md border bg-background p-2 text-sm"
				value={search}
				onChange={(e) => {
					setSearch(e.target.value);
					setPage(0);
				}}
			/>
			<div className="grid grid-cols-2 gap-2">
				{displayed.map((entry) => (
					<button
						key={entry.name}
						className="rounded-lg border p-1.5 text-left disabled:opacity-50 hover:bg-accent"
						onClick={() => apply(entry.name)}
						aria-label={`${t("Apply transition")} ${entry.name}`}
					>
						<Thumbnail name={entry.name} />
						<span className="block truncate text-xs pt-1">{entry.name}</span>
					</button>
				))}
			</div>
			<div className="flex items-center justify-between">
				<Button
					variant="outline"
					size="sm"
					disabled={!page}
					onClick={() => setPage(page - 1)}
				>
					{t("Previous")}
				</Button>
				<span className="text-xs">
					{page + 1} / {Math.max(1, Math.ceil(filtered.length / 24))}
				</span>
				<Button
					variant="outline"
					size="sm"
					disabled={(page + 1) * 24 >= filtered.length}
					onClick={() => setPage(page + 1)}
				>
					{t("Next")}
				</Button>
			</div>
		</section>
	);
}
