// Types of the backend's answers and the react-query hooks around them.
import type { PluginSdk } from "@natural-land/plugin-sdk";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export type ScreenMode = "content" | "white" | "black" | "off";

export interface ScreenDoc {
  mode: ScreenMode;
  items: string[];
  loop: boolean;
  image_duration: number;
}

export interface CaptureDoc {
  video: boolean;
  audio: boolean;
  touch: boolean;
  screen: ScreenDoc | null;
  volume: number | null;
  brightness: number | null;
  restore: boolean;
}

export interface PlanDoc extends CaptureDoc {
  enabled: boolean;
}

export interface SettingsDoc {
  enabled: boolean;
  host: string;
  port: number;
  token: string;
  min_free_gb: number;
}

export interface TabletCapture {
  session: string;
  started: number;
  until: number | null;
  lease_until: number | null;
  video?: boolean;
  audio?: boolean;
  touch?: boolean;
}

export interface TabletReport {
  device_id: string;
  version: string;
  time: number;
  volume: number | null;
  brightness: number | null;
  player: { mode: ScreenMode; playlist_len: number; running: boolean; current?: string; paused?: boolean };
  capture: TabletCapture | null;
  spool: { files: number; bytes: number; free_bytes: number };
  cache_bytes: number;
  battery: { capacity?: string; status?: string; ac_online?: boolean };
  temperature: number | null;
  memory: { total_mb?: number; available_mb?: number; agent_rss_mb?: number | null; mpv_rss_mb?: number | null };
  load: number[] | null;
  uptime: number;
}

export interface Status {
  holding: boolean;
  configured: boolean;
  enabled: boolean;
  address: string | null;
  online: boolean;
  error: string | null;
  db_error: string | null;
  report_age_s: number | null;
  clock_offset_s: number | null;
  tablet: TabletReport | null;
  active_run: { run_id: string; experiment_id: number | null } | null;
  plan: { version_no: number | null; enabled: boolean; inferred: boolean } | null;
  want: { session_id: string; kind: string; run_id: string; part: number } | null;
  decision: string | null;
  waiting_media: string[];
  pushing: { name: string; bytes: number; started: number } | null;
  push_queue: number;
  push_error: string | null;
  fetching: { session: string; name: string; bytes: number } | null;
  fetch_error: string | null;
  low_disk: boolean;
  disk: { free_bytes: number; total_bytes: number; min_free_bytes: number };
}

export interface Media {
  name: string;
  original_name: string;
  kind: "video" | "image" | "audio";
  bytes: number;
  uploaded_by: string | null;
  uploaded_at: string;
  on_tablet: boolean | null;
  used_by_experiments: number[];
}

export interface PlanVersion {
  id: number;
  version_no: number;
  doc: PlanDoc;
  created_by: string | null;
  created_at: string;
}

export interface CaptureFile {
  name: string;
  bytes: number;
  stored_at: string;
  info: { frames?: number; touching?: number; max_fingers?: number };
}

export interface CaptureSummary {
  duration_s?: number | null;
  segments?: number;
  bytes?: number;
  touch_frames?: number;
  touch_frames_down?: number;
  max_fingers?: number;
  screen_changes?: number;
  error?: string | null;
}

export interface Capture {
  session_id: string;
  kind: "run" | "manual" | "other";
  run_id: string | null;
  experiment_id: number | null;
  part: number;
  plan_version_id: number | null;
  request: Record<string, unknown>;
  requested_by: string | null;
  requested_at: string;
  started_at: string | null;
  ended_at: string | null;
  stop_reason: string | null;
  complete: boolean;
  summary: CaptureSummary;
  bytes: number;
  file_count?: number;
  capturing?: boolean;
}

export interface ScreenLogEntry {
  t: number;
  mode: ScreenMode;
  file: string | null;
}

export interface CaptureDetail extends Capture {
  files: CaptureFile[];
  media_names: Record<string, string>;
  meta: {
    started_utc?: number | null;
    ended_utc?: number | null;
    segments?: { file: string; start_utc: number | null; end_utc: number | null }[];
    settings?: { volume?: number | null; brightness?: number | null };
    audio?: { tracks?: string[] } | null;
    video?: { width: number; height: number } | null;
    screen?: { requested?: { mode: ScreenMode; items: string[] } | null; log?: ScreenLogEntry[]; playback?: unknown[]; restored?: string[] };
    error?: string | null;
  } | null;
}

export interface EventRow {
  id: number;
  at: string;
  source: "tablet" | "plugin";
  name: string;
  session_id: string | null;
  run_id: string | null;
  actor: string | null;
  data: Record<string, unknown>;
}

export interface Sample {
  t: number;
  battery: number | null;
  temperature: number | null;
  mem_used_mb: number | null;
  agent_rss_mb: number | null;
  mpv_rss_mb: number | null;
  load1: number | null;
  spool_bytes: number | null;
  capturing: boolean;
}

const k = (sdk: PluginSdk, ...rest: unknown[]) => [sdk.pluginId, ...rest];

export function fileUrl(sdk: PluginSdk, path: string): string {
  return `/api/plugins/${sdk.pluginId}/${path}`;
}

export function useStatus(sdk: PluginSdk, intervalMs = 2000) {
  return useQuery({ queryKey: k(sdk, "status"), queryFn: ({ signal }) => sdk.api<Status>("status", { signal }), refetchInterval: intervalMs });
}

export function useSettings(sdk: PluginSdk) {
  return useQuery({ queryKey: k(sdk, "settings"), queryFn: ({ signal }) => sdk.api<{ settings: SettingsDoc; version: number }>("settings", { signal }) });
}

export function useSaveSettings(sdk: PluginSdk) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (doc: SettingsDoc) => sdk.api<{ version: number }>("settings", { method: "PUT", body: doc }),
    onSuccess: () => qc.invalidateQueries({ queryKey: k(sdk) }),
  });
}

export function useMedia(sdk: PluginSdk) {
  return useQuery({ queryKey: k(sdk, "media"), queryFn: ({ signal }) => sdk.api<{ media: Media[]; bytes: number }>("media", { signal }), refetchInterval: 10000 });
}

export function usePlan(sdk: PluginSdk, experimentId: number | null) {
  return useQuery({
    queryKey: k(sdk, "plan", experimentId),
    queryFn: ({ signal }) => sdk.api<{ current: PlanVersion | null; history: PlanVersion[]; default: PlanDoc }>(`experiments/${experimentId}/plan`, { signal }),
    enabled: experimentId !== null,
  });
}

export function useSavePlan(sdk: PluginSdk, experimentId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { doc: PlanDoc; base_version: number }) => sdk.api<{ id: number; version_no: number }>(`experiments/${experimentId}/plan`, { method: "PUT", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: k(sdk, "plan", experimentId) }),
  });
}

export function useCaptures(sdk: PluginSdk, query: { run_id?: string | null; kind?: string | null; limit?: number; offset?: number }) {
  return useQuery({
    queryKey: k(sdk, "captures", query),
    queryFn: ({ signal }) => sdk.api<{ captures: Capture[]; total: number }>("captures", { signal, query }),
    refetchInterval: 5000,
  });
}

export function useCapture(sdk: PluginSdk, sessionId: string | null) {
  return useQuery({
    queryKey: k(sdk, "capture", sessionId),
    queryFn: ({ signal }) => sdk.api<CaptureDetail>(`captures/${encodeURIComponent(sessionId ?? "")}`, { signal }),
    enabled: sessionId !== null,
    refetchInterval: 5000,
  });
}

export function useEvents(sdk: PluginSdk, query: { run_id?: string | null; limit?: number }) {
  return useQuery({
    queryKey: k(sdk, "events", query),
    queryFn: ({ signal }) => sdk.api<{ events: EventRow[] }>("events", { signal, query }),
    refetchInterval: 5000,
  });
}

export function useSamples(sdk: PluginSdk, hours: number) {
  return useQuery({
    queryKey: k(sdk, "samples", hours),
    queryFn: ({ signal }) => sdk.api<{ samples: Sample[] }>("samples", { signal, query: { hours, points: 400 } }),
    refetchInterval: 15000,
  });
}

/** Commands to the tablet and captures; every success refreshes the status. */
export function useActions(sdk: PluginSdk) {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: k(sdk) });
  return {
    control: useMutation({ mutationFn: (body: { cmd: string; params: Record<string, unknown> }) => sdk.api("control", { method: "POST", body }), onSuccess: done }),
    start: useMutation({ mutationFn: (body: CaptureDoc & { duration_s: number }) => sdk.api<{ session_id: string }>("capture/start", { method: "POST", body }), onSuccess: done }),
    stop: useMutation({ mutationFn: () => sdk.api("capture/stop", { method: "POST" }), onSuccess: done }),
    deleteCapture: useMutation({ mutationFn: (sid: string) => sdk.api(`captures/${encodeURIComponent(sid)}`, { method: "DELETE" }), onSuccess: done }),
    deleteMedia: useMutation({ mutationFn: (name: string) => sdk.api(`media/${name}`, { method: "DELETE" }), onSuccess: done }),
    pushMedia: useMutation({ mutationFn: (name: string) => sdk.api(`media/${name}/push`, { method: "POST" }), onSuccess: done }),
  };
}

async function raw(url: string, init: RequestInit): Promise<unknown> {
  const res = await fetch(url, { credentials: "same-origin", ...init });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const err = (body as { error?: { code?: string; message?: string } } | null)?.error;
    throw Object.assign(new Error(err?.message ?? `HTTP ${res.status}`), { status: res.status, code: err?.code ?? `http_${res.status}` });
  }
  return body;
}

/** Upload one file in chunks under the console's 1 MiB request limit; `onProgress` gets the bytes sent. */
export async function uploadMedia(sdk: PluginSdk, file: File, onProgress: (sent: number) => void, signal?: AbortSignal): Promise<{ name: string; added: boolean }> {
  const { upload_id, chunk_bytes } = await sdk.api<{ upload_id: string; chunk_bytes: number }>("media/uploads", {
    method: "POST",
    body: { filename: file.name, size: file.size },
  });
  try {
    let offset = 0;
    while (offset < file.size) {
      const chunk = file.slice(offset, offset + chunk_bytes);
      const r = (await raw(fileUrl(sdk, `media/uploads/${upload_id}?offset=${offset}`), {
        method: "PUT",
        body: chunk,
        headers: { "content-type": "application/octet-stream" },
        signal,
      })) as { received: number };
      offset = r.received;
      onProgress(offset);
    }
    return await sdk.api<{ name: string; added: boolean }>(`media/uploads/${upload_id}/finish`, { method: "POST", timeoutMs: 120000 });
  } catch (e) {
    void sdk.api(`media/uploads/${upload_id}`, { method: "DELETE" }).catch(() => undefined);
    throw e;
  }
}
