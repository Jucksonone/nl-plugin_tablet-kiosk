// Capture lists and one capture in detail (files, playback, screen log, export).
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { fileUrl, useActions, useCapture, type Capture, type CaptureDetail } from "./api";
import { NS } from "./i18n";
import { Badge, ErrorNotice, Notice, Stat, fmtBytes, fmtDuration, fmtTime, pct, useSdk } from "./ui";

export function captureState(c: Capture): { tone: "live" | "ok" | "warn" | "muted"; key: string } {
  if (c.capturing) return { tone: "live", key: "capturing" };
  if (c.complete) return { tone: c.summary?.error ? "warn" : "ok", key: "complete" };
  return { tone: "muted", key: "transferring" };
}

export function CaptureTable({ rows, selected, onSelect, showRun }: { rows: Capture[]; selected: string | null; onSelect: (sid: string | null) => void; showRun?: boolean }) {
  const { t } = useTranslation(NS);
  if (rows.length === 0) return <p className="text-sm text-muted-foreground">{t("captures.empty")}</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted-foreground">
          <tr>
            <th className="py-1 pr-3 font-medium">{t("captures.started")}</th>
            <th className="py-1 pr-3 font-medium">{t("captures.kind")}</th>
            {showRun ? <th className="py-1 pr-3 font-medium">{t("captures.run")}</th> : null}
            <th className="py-1 pr-3 font-medium">{t("captures.duration")}</th>
            <th className="py-1 pr-3 font-medium">{t("captures.size")}</th>
            <th className="py-1 pr-3 font-medium">{t("captures.state")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => {
            const st = captureState(c);
            return (
              <tr
                key={c.session_id}
                className={`cursor-pointer border-t border-border hover:bg-muted/50 ${selected === c.session_id ? "bg-muted" : ""}`}
                onClick={() => onSelect(selected === c.session_id ? null : c.session_id)}
              >
                <td className="py-1.5 pr-3 tabular-nums">{fmtTime(c.started_at ?? c.requested_at)}</td>
                <td className="py-1.5 pr-3">
                  {t(`captures.kinds.${c.kind}`)}
                  {c.kind === "run" && c.part > 1 ? ` · ${t("captures.part", { n: c.part })}` : ""}
                </td>
                {showRun ? <td className="max-w-48 truncate py-1.5 pr-3 font-mono text-xs">{c.run_id ?? "—"}</td> : null}
                <td className="py-1.5 pr-3 tabular-nums">{fmtDuration(c.summary?.duration_s)}</td>
                <td className="py-1.5 pr-3 tabular-nums">{fmtBytes(c.bytes)}</td>
                <td className="py-1.5 pr-3">
                  <Badge tone={st.tone}>{t(`captures.states.${st.key}`)}</Badge>
                  {c.stop_reason ? <span className="ml-2 text-xs text-muted-foreground">{t(`stopReasons.${c.stop_reason}`, { defaultValue: c.stop_reason })}</span> : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function CaptureView({ sessionId, onDeleted }: { sessionId: string; onDeleted?: () => void }) {
  const { t } = useTranslation(NS);
  const sdk = useSdk();
  const { Button } = sdk.ui;
  const q = useCapture(sdk, sessionId);
  const actions = useActions(sdk);
  const [segment, setSegment] = useState<string | null>(null);
  if (q.isPending) return <p className="text-sm text-muted-foreground">{t("loading")}</p>;
  if (q.error) return <ErrorNotice error={q.error} />;
  const c: CaptureDetail = q.data;
  const meta = c.meta ?? {};
  const s = c.summary ?? {};
  const videos = c.files.filter((f) => f.name.endsWith(".mp4"));
  const log = meta.screen?.log ?? [];
  const name = (n: string | null) => (n ? (c.media_names[n] ?? n) : "—");
  const start = meta.started_utc ?? null;
  return (
    <div className="space-y-4 rounded-md border border-border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="font-mono text-sm">{c.session_id}</div>
        <div className="flex flex-wrap gap-2">
          <a className="inline-flex items-center rounded-md border border-border px-3 py-1 text-sm hover:bg-muted" href={fileUrl(sdk, `captures/${encodeURIComponent(c.session_id)}/export`)} download>
            {t("captures.export")}
          </a>
          <Button
            size="sm"
            variant="destructive"
            disabled={c.capturing || actions.deleteCapture.isPending}
            onClick={() => {
              if (window.confirm(t("captures.confirmDelete", { size: fmtBytes(c.bytes) }))) actions.deleteCapture.mutate(c.session_id, { onSuccess: () => onDeleted?.() });
            }}
          >
            {t("captures.delete")}
          </Button>
        </div>
      </div>
      <ErrorNotice error={actions.deleteCapture.error} />
      {!c.complete && !c.capturing ? <Notice tone="info">{t("captures.waitingFiles")}</Notice> : null}
      {s.error ? <Notice tone="warning">{t("captures.tabletError", { message: s.error })}</Notice> : null}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label={t("captures.duration")} value={fmtDuration(s.duration_s)} hint={`${fmtTime(c.started_at)} → ${c.ended_at ? fmtTime(c.ended_at) : "…"}`} />
        <Stat label={t("captures.segments")} value={s.segments ?? videos.length} hint={fmtBytes(c.bytes)} />
        <Stat label={t("captures.touchFrames")} value={s.touch_frames ?? "—"} hint={t("captures.touchDown", { n: s.touch_frames_down ?? 0, max: s.max_fingers ?? 0 })} />
        <Stat label={t("captures.levels")} value={`${pct(meta.settings?.volume)} / ${pct(meta.settings?.brightness)}`} hint={t("captures.levelsHint")} />
      </div>
      {videos.length ? (
        <div className="space-y-2">
          <div className="text-sm font-medium">{t("captures.playback")}</div>
          <div className="flex flex-wrap gap-1">
            {videos.map((f) => (
              <Button key={f.name} size="xs" variant={segment === f.name ? "default" : "outline"} onClick={() => setSegment(f.name)}>
                {f.name.replace(/^av_0*/, "").replace(".mp4", "") || "0"}
              </Button>
            ))}
          </div>
          {segment ? (
            <video key={segment} className="max-h-80 w-full max-w-xl rounded-md bg-black" controls autoPlay src={fileUrl(sdk, `captures/${encodeURIComponent(c.session_id)}/files/${segment}`)} />
          ) : (
            <p className="text-xs text-muted-foreground">{t("captures.pickSegment")}</p>
          )}
          {meta.audio?.tracks?.length ? <p className="text-xs text-muted-foreground">{t("captures.tracks", { tracks: meta.audio.tracks.map((x, i) => `${i + 1} ${t(`tracks.${x}`, { defaultValue: x })}`).join(" · ") })}</p> : null}
        </div>
      ) : null}
      <div className="space-y-1">
        <div className="text-sm font-medium">{t("captures.screenLog")}</div>
        {log.length === 0 ? (
          <p className="text-sm text-muted-foreground">—</p>
        ) : (
          <ul className="max-h-48 space-y-0.5 overflow-auto text-sm">
            {log.map((e, i) => (
              <li key={i} className="flex gap-3">
                <span className="w-20 shrink-0 text-right tabular-nums text-muted-foreground">{start ? `+${fmtDuration(e.t - start)}` : fmtTime(e.t)}</span>
                <span>{t(`screen.modes.${e.mode}`)}</span>
                {e.mode === "content" ? <span className="truncate">{name(e.file)}</span> : null}
              </li>
            ))}
          </ul>
        )}
      </div>
      <details>
        <summary className="cursor-pointer text-sm text-muted-foreground">{t("captures.files", { n: c.files.length })}</summary>
        <ul className="mt-1 max-h-60 space-y-0.5 overflow-auto text-sm">
          {c.files.map((f) => (
            <li key={f.name} className="flex justify-between gap-3">
              <a className="font-mono text-xs underline-offset-2 hover:underline" href={fileUrl(sdk, `captures/${encodeURIComponent(c.session_id)}/files/${f.name}`)} target="_blank" rel="noreferrer">
                {f.name}
              </a>
              <span className="tabular-nums text-muted-foreground">{fmtBytes(f.bytes)}</span>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
