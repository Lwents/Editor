import { mediaTimeToSeconds, roundMediaTime } from "@/wasm";
import type { FrameRate } from "opencut-wasm";
import type { AnyBaseNode } from "./nodes/base-node";
import { createCanvasSurface } from "./canvas-utils";
import { buildFrameDescriptor } from "./compositor/frame-descriptor";
import { wasmCompositor } from "./compositor/wasm-compositor";
import { resolveRenderTree } from "./resolve";
import {
	measureSpanAsync,
	measureSpanSync,
	onRenderPerfFrameComplete,
} from "@/diagnostics/render-perf";

import { renderExternalItems } from "./external/composite";

let renderQueue: Promise<unknown> = Promise.resolve();

export type CanvasRendererParams = {
	width: number;
	height: number;
	fps: FrameRate;
	captureOutput?: boolean;
};

export class CanvasRenderer {
	canvas: OffscreenCanvas;
	context: OffscreenCanvasRenderingContext2D;
	width: number;
	height: number;
	fps: FrameRate;
	private captureOutput: boolean;
	private outputCanvas: HTMLCanvasElement | null = null;

	constructor({ width, height, fps, captureOutput = false }: CanvasRendererParams) {
		this.width = width;
		this.height = height;
		this.fps = fps;
		this.captureOutput = captureOutput;

		const surface = createCanvasSurface({ width, height });
		this.canvas = surface.canvas;
		this.context = surface.context;
	}

	getOutputCanvas(): HTMLCanvasElement {
		wasmCompositor.ensureInitialized({
			width: this.width,
			height: this.height,
		});
		const shared = wasmCompositor.getCanvas();
		// WebGL effect previews also draw into the compositor's backing canvas.
		// Keep their pixels out of the visible preview, and isolate every export.
		if (this.captureOutput || shared.getContext("webgl2")) {
			this.captureOutput = true;
			return this.getCapturedCanvas();
		}
		return shared;
	}

	private getCapturedCanvas(): HTMLCanvasElement {
		this.outputCanvas ??= document.createElement("canvas");
		if (this.outputCanvas.width !== this.width) this.outputCanvas.width = this.width;
		if (this.outputCanvas.height !== this.height) this.outputCanvas.height = this.height;
		return this.outputCanvas;
	}

	setSize({ width, height }: { width: number; height: number }) {
		this.width = width;
		this.height = height;

		const surface = createCanvasSurface({ width, height });
		this.canvas = surface.canvas;
		this.context = surface.context;
	}

	render({ node, time }: { node: AnyBaseNode; time: number }): Promise<void> {
		const task = renderQueue.then(() => this.renderFrame({ node, time }));
		renderQueue = task.catch(() => undefined);
		return task;
	}

	private async renderFrame({
		node,
		time,
	}: {
		node: AnyBaseNode;
		time: number;
	}) {
		await measureSpanAsync({
			name: "resolve",
			fn: () => resolveRenderTree({ node, renderer: this, time }),
		});
		let { frame, textures } = await measureSpanAsync({
			name: "buildFrame",
			fn: () => buildFrameDescriptor({ node, renderer: this }),
		});
		({ frame, textures } = await renderExternalItems(
			frame,
			textures,
			mediaTimeToSeconds({ time: roundMediaTime({ time }) }),
		));
		wasmCompositor.ensureInitialized({
			width: this.width,
			height: this.height,
		});
		measureSpanSync({
			name: "syncTextures",
			fn: () => wasmCompositor.syncTextures(textures),
		});
		measureSpanSync({
			name: "renderFrame",
			fn: () => wasmCompositor.render(frame),
		});
		if (this.captureOutput) {
			const output = this.getCapturedCanvas();
			const context = output.getContext("2d");
			if (!context) throw new Error("Failed to capture rendered frame");
			context.clearRect(0, 0, this.width, this.height);
			context.drawImage(wasmCompositor.getCanvas(), 0, 0, this.width, this.height);
		}
	}

	async renderToCanvas({
		node,
		time,
		targetCanvas,
	}: {
		node: AnyBaseNode;
		time: number;
		targetCanvas: HTMLCanvasElement;
	}) {
		this.captureOutput = true;
		await this.render({ node, time });

		const ctx = targetCanvas.getContext("2d");
		if (!ctx) {
			throw new Error("Failed to get target canvas context");
		}

		measureSpanSync({
			name: "drawImage",
			fn: () =>
				ctx.drawImage(
					this.getCapturedCanvas(),
					0,
					0,
					targetCanvas.width,
					targetCanvas.height,
				),
		});
		onRenderPerfFrameComplete();
	}
}
