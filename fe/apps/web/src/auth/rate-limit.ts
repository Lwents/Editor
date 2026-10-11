import { Ratelimit } from "@upstash/ratelimit";
import { Redis } from "@upstash/redis";
import { webEnv } from "@/env/web";

const redis = new Redis({
	url: webEnv.UPSTASH_REDIS_REST_URL,
	token: webEnv.UPSTASH_REDIS_REST_TOKEN,
});

export const baseRateLimit = new Ratelimit({
	redis,
	limiter: Ratelimit.slidingWindow(100, "1 m"), // 100 requests per minute
	analytics: true,
	prefix: "rate-limit",
});

const desktopWindows = new Map<string, { started: number; count: number }>();

export async function checkRateLimit({ request }: { request: Request }) {
	const ip = request.headers.get("x-forwarded-for") ?? "anonymous";
	if (process.env.EDITOR_DESKTOP_MODE === "1") {
		const now = Date.now();
		for (const [key, entry] of desktopWindows) {
			if (now - entry.started >= 60_000) desktopWindows.delete(key);
		}
		const entry = desktopWindows.get(ip) ?? { started: now, count: 0 };
		entry.count += 1;
		desktopWindows.set(ip, entry);
		const success = entry.count <= 100;
		return { success, limited: !success };
	}
	const { success } = await baseRateLimit.limit(ip);
	return { success, limited: !success };
}
