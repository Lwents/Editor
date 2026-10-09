import catalog from "gl-transitions/gl-transitions.json";
import { snapshot } from "./effects";
export const glTransitions = catalog;
export type GlTransition = (typeof catalog)[number];
let canvas: OffscreenCanvas | undefined, gl: WebGLRenderingContext | undefined;
const programs = new Map<string, WebGLProgram>();
let buffer: WebGLBuffer | undefined;
const textures: WebGLTexture[] = [];
function init() {
	if (gl) return gl;
	canvas = new OffscreenCanvas(160, 160);
	gl =
		canvas.getContext("webgl", {
			preserveDrawingBuffer: true,
			alpha: true,
			premultipliedAlpha: false,
			depth: false,
			antialias: false,
		}) ?? undefined;
	if (!gl) throw Error("WebGL unavailable for transitions");
	buffer = gl.createBuffer()!;
	gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
	gl.bufferData(
		gl.ARRAY_BUFFER,
		new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]),
		gl.STATIC_DRAW,
	);
	for (let i = 0; i < 3; i++) textures.push(gl.createTexture()!);
	return gl;
}
function shader(g: WebGLRenderingContext, type: number, source: string) {
	const s = g.createShader(type)!;
	g.shaderSource(s, source);
	g.compileShader(s);
	if (!g.getShaderParameter(s, g.COMPILE_STATUS)) {
		const message = g.getShaderInfoLog(s);
		g.deleteShader(s);
		throw Error(message ?? "Transition shader failed");
	}
	return s;
}
function program(g: WebGLRenderingContext, entry: GlTransition) {
	const existing = programs.get(entry.name);
	if (existing) return existing;
	const vertex = shader(
		g,
		g.VERTEX_SHADER,
		"attribute vec2 position; varying vec2 uv; void main(){uv=position*0.5+0.5;gl_Position=vec4(position,0.,1.);}",
	);
	const fragment = shader(
		g,
		g.FRAGMENT_SHADER,
		`precision highp float;
 varying vec2 uv; uniform sampler2D sourceFrom; uniform sampler2D sourceTo;
 uniform float progress; uniform float ratio;
 vec4 getFromColor(vec2 p){return texture2D(sourceFrom,p);}
 vec4 getToColor(vec2 p){return texture2D(sourceTo,p);}
 ${entry.glsl}
 void main(){if(progress<=0.)gl_FragColor=getFromColor(uv);else if(progress>=1.)gl_FragColor=getToColor(uv);else gl_FragColor=transition(uv);}`,
	);
	const p = g.createProgram()!;
	g.attachShader(p, vertex);
	g.attachShader(p, fragment);
	g.linkProgram(p);
	g.deleteShader(vertex);
	g.deleteShader(fragment);
	if (!g.getProgramParameter(p, g.LINK_STATUS)) {
		const error = g.getProgramInfoLog(p);
		g.deleteProgram(p);
		throw Error(error ?? "Transition linking failed");
	}
	programs.set(entry.name, p);
	if (programs.size > 24) {
		const first = programs.keys().next().value!;
		g.deleteProgram(programs.get(first)!);
		programs.delete(first);
	}
	return p;
}
function upload(
	g: WebGLRenderingContext,
	slot: number,
	source: CanvasImageSource,
) {
	g.activeTexture(g.TEXTURE0 + slot);
	g.bindTexture(g.TEXTURE_2D, textures[slot]);
	g.texParameteri(g.TEXTURE_2D, g.TEXTURE_MIN_FILTER, g.LINEAR);
	g.texParameteri(g.TEXTURE_2D, g.TEXTURE_MAG_FILTER, g.LINEAR);
	g.texParameteri(g.TEXTURE_2D, g.TEXTURE_WRAP_S, g.CLAMP_TO_EDGE);
	g.texParameteri(g.TEXTURE_2D, g.TEXTURE_WRAP_T, g.CLAMP_TO_EDGE);
	g.pixelStorei(g.UNPACK_FLIP_Y_WEBGL, 1);
	g.pixelStorei(g.UNPACK_PREMULTIPLY_ALPHA_WEBGL, 0);
	g.texImage2D(
		g.TEXTURE_2D,
		0,
		g.RGBA,
		g.RGBA,
		g.UNSIGNED_BYTE,
		source as TexImageSource,
	);
}
let displacement: OffscreenCanvas | undefined;
function displacementMap() {
	if (displacement) return displacement;
	displacement = new OffscreenCanvas(128, 128);
	const ctx = displacement.getContext("2d")!,
		pixels = ctx.createImageData(128, 128);
	for (let i = 0; i < pixels.data.length; i += 4) {
		const x = (i / 4) % 128,
			y = Math.floor(i / 512);
		pixels.data.set(
			[128 + 60 * Math.sin(x / 8), 128 + 60 * Math.cos(y / 8), 128, 255],
			i,
		);
	}
	ctx.putImageData(pixels, 0, 0);
	return displacement;
}
export function renderGlTransition(
	from: CanvasImageSource,
	to: CanvasImageSource,
	name: string,
	progress: number,
	width: number,
	height: number,
	params: Record<string, number | number[] | boolean> = {},
): OffscreenCanvas {
	const entry = catalog.find((x) => x.name === name);
	if (!entry) throw Error(`Unknown transition ${name}`);
	const g = init();
	canvas!.width = width;
	canvas!.height = height;
	g.viewport(0, 0, width, height);
	const p = program(g, entry);
	g.useProgram(p);
	g.bindBuffer(g.ARRAY_BUFFER, buffer!);
	const position = g.getAttribLocation(p, "position");
	g.enableVertexAttribArray(position);
	g.vertexAttribPointer(position, 2, g.FLOAT, false, 0, 0);
	upload(g, 0, from);
	upload(g, 1, to);
	g.uniform1i(g.getUniformLocation(p, "sourceFrom"), 0);
	g.uniform1i(g.getUniformLocation(p, "sourceTo"), 1);
	g.uniform1f(
		g.getUniformLocation(p, "progress"),
		Math.min(1, Math.max(0, progress)),
	);
	g.uniform1f(g.getUniformLocation(p, "ratio"), width / height);
	for (const [key, type] of Object.entries(entry.paramsTypes)) {
		const loc = g.getUniformLocation(p, key);
		if (loc === null) continue;
		const value =
			params[key] ??
			(entry.defaultParams as Record<string, number | number[] | boolean>)[key];
		const a = Array.isArray(value) ? value : [Number(value)];
		if (type === "sampler2D") {
			upload(g, 2, displacementMap());
			g.uniform1i(loc, 2);
		} else if (type === "bool" || type === "int")
			g.uniform1i(loc, Number(value));
		else if (type === "ivec2") g.uniform2iv(loc, a);
		else if (type === "vec2") g.uniform2fv(loc, a);
		else if (type === "vec3") g.uniform3fv(loc, a);
		else if (type === "vec4") g.uniform4fv(loc, a);
		else g.uniform1f(loc, Number(value));
	}
	g.drawArrays(g.TRIANGLE_STRIP, 0, 4);
	return snapshot(canvas!, width, height);
}
