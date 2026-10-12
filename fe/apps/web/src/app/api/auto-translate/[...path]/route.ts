/** Local bridge: provider credentials remain in AutoTranslateAI, never in the browser. */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
async function forward(
	request: Request,
	context: { params: Promise<{ path: string[] }> },
) {
	const { path } = await context.params;
	const route = path.join("/");
	const allowed =
		(request.method === "GET" &&
			(route === "health" ||
				route === "api/jobs" ||
				route === "api/settings/ai" ||
				route === "api/voices" ||
				/^api\/jobs\/[a-zA-Z0-9_-]+(\/(subtitles(?:\/quality)?|download))?$/.test(route))) ||
		(request.method === "POST" &&
			(route === "api/uploads/video" ||
				route === "api/settings/ai" ||
				route === "api/settings/ai/test" ||
				route === "api/voices/preview" ||
				route === "api/jobs" ||
				/^api\/jobs\/[a-zA-Z0-9_-]+\/(cancel|subtitles\/realign)$/.test(route)));
	if (!allowed)
		return Response.json(
			{ detail: "Đường dẫn không được hỗ trợ." },
			{ status: 404 },
		);
	// This bridge is for the local editor. Reject cross-site writes.
	const origin = request.headers.get("origin");
	if (request.method === "POST" && origin) {
		let isSameOrigin = false;
		try {
			isSameOrigin = new URL(origin).host === request.headers.get("host");
		} catch {
			// Invalid origins are rejected like any other cross-site write.
		}
		if (!isSameOrigin)
			return Response.json({ detail: "Origin không hợp lệ." }, { status: 403 });
	}

	try {
		const headers = new Headers();
		for (const key of ["content-type", "range"]) {
			const value = request.headers.get(key);
			if (value) headers.set(key, value);
		}
		const options: RequestInit & { duplex?: "half" } = {
			method: request.method,
			headers,
			cache: "no-store",
			redirect: "error",
		};
		if (request.method === "POST") {
			options.body = request.body;
			options.duplex = "half";
		}
		const destination = new URL(
			`${(process.env.RVP_BACKEND_URL || "http://127.0.0.1:8100").replace(/\/$/, "")}/${route}`,
		);
		if (request.method === "GET" && route.endsWith("/subtitles")) {
			const language = new URL(request.url).searchParams.get("language");
			if (language !== null) destination.searchParams.set("language", language);
		}
		const response = await fetch(destination, options);
		const outgoing = new Headers({ "cache-control": "no-store" });
		for (const key of [
			"content-type",
			"content-length",
			"content-disposition",
			"accept-ranges",
			"content-range",
			"x-subtitle-timing",
		]) {
			const value = response.headers.get(key);
			if (value) outgoing.set(key, value);
		}
		return new Response(response.body, {
			status: response.status,
			headers: outgoing,
		});
	} catch {
		return Response.json(
			{
				detail:
					"Chưa kết nối được AutoTranslateAI. Hãy chạy backend ở cổng 8100.",
			},
			{ status: 503 },
		);
	}
}
export { forward as GET, forward as POST };
