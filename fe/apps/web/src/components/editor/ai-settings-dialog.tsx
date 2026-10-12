"use client";

import { useEffect, useState } from "react";
import { Settings2, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Dialog, DialogTrigger, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter } from "../ui/dialog";

const endpoint = "/api/auto-translate/api/settings/ai";
type Config = { api_url: string; model: string; has_api_key: boolean };

async function readResponse(response: Response) {
 const data = await response.json();
 if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Không xử lý được cấu hình. Kiểm tra URL, model và API key.");
 return data;
}

export function AiSettingsDialog() {
 const [open, setOpen] = useState(false);
 const [config, setConfig] = useState<Config | null>(null);
 const [apiUrl, setApiUrl] = useState("");
 const [model, setModel] = useState("");
 const [key, setKey] = useState("");
 const [busy, setBusy] = useState<"load" | "test" | "save" | null>(null);
 const [result, setResult] = useState<{ok: boolean; message: string} | null>(null);
 useEffect(() => {
  if (!open) return;
  const controller = new AbortController();
  setBusy("load"); setConfig(null); setResult(null); setKey("");
  fetch(endpoint, {cache: "no-store", signal: controller.signal}).then(readResponse).then((data: Config) => {
   setConfig(data); setApiUrl(data.api_url); setModel(data.model);
  }).catch((error: Error) => {if (!controller.signal.aborted) setResult({ok: false, message: error.message});})
   .finally(() => {if (!controller.signal.aborted) setBusy(null);});
  return () => controller.abort();
 }, [open]);
 const canSubmit = !!config && !!apiUrl.trim() && !!model.trim() && !busy && (!!key.trim() || (config.has_api_key && apiUrl.trim().replace(/\/$/, "") === config.api_url.replace(/\/$/, "")));
 async function submit(action: "test" | "save") {
  setBusy(action); setResult(null);
  try {
   const data = await readResponse(await fetch(action === "test" ? `${endpoint}/test` : endpoint, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({api_url: apiUrl.trim(), model: model.trim(), api_key: key.trim() || null}),
   }));
   setResult({ok: action === "save" || data.ok, message: data.message + (data.latency_ms ? ` (${(data.latency_ms / 1000).toFixed(1)} giây)` : "")});
   if (action === "save") {
    setConfig(data); setApiUrl(data.api_url); setModel(data.model); setKey("");
    setOpen(false);
    toast.success("Đã lưu cấu hình AI", {description: "Cấu hình mới sẽ được dùng cho lần dịch tiếp theo."});
   }
  } catch (error) {setResult({ok: false, message: error instanceof Error ? error.message : "Chưa kết nối được backend."});}
  finally {setBusy(null);}
 }
 return <Dialog open={open} onOpenChange={(value) => {if (!busy || busy === "load") {setOpen(value); if (!value) setKey("");}}}>
  <DialogTrigger asChild><Button variant="ghost" size="icon" className="size-8" title="Cấu hình AI" aria-label="Cấu hình AI"><Settings2 className="size-4" /></Button></DialogTrigger>
  <DialogContent className="max-h-[90dvh] overflow-y-auto">
   <DialogHeader><DialogTitle>Cấu hình AI</DialogTitle><DialogDescription>Kết nối 9Router hoặc API tương thích OpenAI để dịch phụ đề.</DialogDescription></DialogHeader>
   <DialogBody>
    {busy === "load" ? <p className="flex items-center gap-2 text-sm"><Loader2 className="size-4 animate-spin" />Đang đọc cấu hình…</p> : config && <>
     <label className="space-y-2 text-sm"><span>Địa chỉ API</span><Input value={apiUrl} onChange={(e) => {setApiUrl(e.target.value); setResult(null);}} disabled={!!busy} placeholder="http://127.0.0.1:20128/v1" /><span className="block text-xs text-muted-foreground">Nhập URL API, thường kết thúc bằng /v1; không dùng địa chỉ dashboard.</span></label>
     <label className="space-y-2 text-sm"><span>API key</span><Input type="password" autoComplete="new-password" value={key} onChange={(e) => {setKey(e.target.value); setResult(null);}} disabled={!!busy} placeholder={config.has_api_key ? "Đã có key — để trống để giữ nguyên" : "Nhập API key"} /><span className="block text-xs text-muted-foreground">Key được lưu ở backend trên máy này. Key đã lưu không được gửi lại trình duyệt.</span></label>
     <label className="space-y-2 text-sm"><span>Model dịch phụ đề</span><Input value={model} onChange={(e) => {setModel(e.target.value); setResult(null);}} disabled={!!busy} placeholder="Tên model do nhà cung cấp cấp" /></label>
     <p className="text-xs text-muted-foreground">Test kết nối gửi một yêu cầu nhỏ tới model, có thể tính phí theo nhà cung cấp. Test không lưu cấu hình.</p>
    </>}
    {result && <p role="status" className={`rounded-lg border p-3 text-sm ${result.ok ? "border-green-500/30 bg-green-500/10 text-green-700 dark:text-green-300" : "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-300"}`}>{result.message}</p>}
   </DialogBody>
   <DialogFooter>
    <Button variant="outline" disabled={!canSubmit} onClick={() => submit("test")}>{busy === "test" && <Loader2 className="size-4 animate-spin" />}Test kết nối</Button>
    <Button disabled={!canSubmit} onClick={() => submit("save")}>{busy === "save" && <Loader2 className="size-4 animate-spin" />}Lưu cấu hình</Button>
   </DialogFooter>
  </DialogContent>
 </Dialog>;
}
