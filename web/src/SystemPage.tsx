import "./plugin.css";
import "./i18n";

import type { PluginSlotProps } from "@natural-land/plugin-sdk";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  uploadMedia,
  useActions,
  useCaptures,
  useEvents,
  useMedia,
  useSamples,
  useSaveSettings,
  useSettings,
  useStatus,
  type CaptureDoc,
  type Sample,
  type ScreenDoc,
  type SettingsDoc,
  type Status,
} from "./api";
import { CaptureTable, CaptureView } from "./captures";
import { EventList } from "./events";
import { CaptureFields, DEFAULT_CAPTURE, DEFAULT_SCREEN, MODES, ScreenEditor, mediaLabel } from "./forms";
import { NS } from "./i18n";
import { Badge, ErrorNotice, Notice, Root, Section, Stat, Toggle, cn, fmtBytes, fmtDuration, fmtTime, pct, useSdk } from "./ui";

const TABS = ["overview", "media", "captures", "events", "settings"] as const;
type Tab = (typeof TABS)[number];

/** Slot "system.page": the tablet — status and controls, media library, captures, events, settings. */
export default function SystemPage({ sdk }: PluginSlotProps) {
  return (
    <Root sdk={sdk}>
      <Body />
    </Root>
  );
}

function Body() {
  const { t } = useTranslation(NS);
  const [tab, setTab] = useState<Tab>("overview");
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-1 border-b border-border" role="tablist">
        {TABS.map((k) => (
          <button
            key={k}
            type="button"
            role="tab"
            aria-selected={tab === k}
            onClick={() => setTab(k)}
            className={cn("-mb-px border-b-2 px-3 py-2 text-sm", tab === k ? "border-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground")}
          >
            {t(`tabs.${k}`)}
          </button>
        ))}
      </div>
      {tab === "overview" ? <Overview onSettings={() => setTab("settings")} /> : null}
      {tab === "media" ? <MediaLibrary /> : null}
      {tab === "captures" ? <CapturesPage /> : null}
      {tab === "events" ? <EventsPage /> : null}
      {tab === "settings" ? <SettingsPage /> : null}
    </div>
  );
}

// ------------------------------------------------------------------ overview

export function ConnectionNotice({ s, onSettings }: { s: Status; onSettings?: () => void }) {
  const { t } = useTranslation(NS);
  const { Button } = useSdk().ui;
  if (!s.configured)
    return (
      <Notice tone="info">
        {t("status.notConfigured")}{" "}
        {onSettings ? (
          <Button size="xs" variant="link" onClick={onSettings}>
            {t("tabs.settings")}
          </Button>
        ) : null}
      </Notice>
    );
  if (!s.holding) return <Notice tone="warning">{t("status.notHolding")}</Notice>;
  if (!s.online) return <Notice>{t("status.offline", { address: s.address, error: s.error ?? "—" })}</Notice>;
  return null;
}

function Overview({ onSettings }: { onSettings: () => void }) {
  const { t } = useTranslation(NS);
  const sdk = useSdk();
  const status = useStatus(sdk);
  if (status.isPending) return <p className="text-sm text-muted-foreground">{t("loading")}</p>;
  if (status.error) return <ErrorNotice error={status.error} />;
  const s = status.data;
  const rep = s.tablet;
  return (
    <div className="space-y-4">
      <ConnectionNotice s={s} onSettings={onSettings} />
      {s.db_error ? <Notice tone="warning">{t("status.dbError", { error: s.db_error })}</Notice> : null}
      {rep ? <TabletStats s={s} /> : null}
      <CurrentCapture s={s} />
      <Transfers s={s} />
      {s.online ? (
        <div className="grid gap-4 xl:grid-cols-2">
          <Controls s={s} />
          <ManualCapture s={s} />
        </div>
      ) : null}
      <Health />
    </div>
  );
}

function TabletStats({ s }: { s: Status }) {
  const { t } = useTranslation(NS);
  const { data: media } = useMedia(useSdk());
  const rep = s.tablet!;
  const bat = rep.battery ?? {};
  const mem = rep.memory ?? {};
  const current = rep.player?.current;
  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-6">
      <Stat
        label={t("status.screen")}
        value={t(`screen.modes.${rep.player?.mode ?? "content"}`)}
        hint={rep.player?.mode === "content" && current ? mediaLabel(media?.media, current) : undefined}
      />
      <Stat label={t("status.volume")} value={pct(rep.volume)} />
      <Stat label={t("status.brightness")} value={pct(rep.brightness)} />
      <Stat label={t("status.battery")} value={bat.capacity ? `${bat.capacity}%` : "—"} hint={bat.status ? t(`battery.${bat.status}`, { defaultValue: bat.status }) : undefined} />
      <Stat label={t("status.temperature")} value={rep.temperature !== null ? `${rep.temperature.toFixed(0)} °C` : "—"} hint={mem.total_mb ? t("status.memory", { used: Math.round((mem.total_mb - (mem.available_mb ?? 0)) / 100) / 10, total: Math.round(mem.total_mb / 100) / 10 }) : undefined} />
      <Stat label={t("status.clock")} value={s.clock_offset_s !== null ? `${s.clock_offset_s > 0 ? "+" : ""}${s.clock_offset_s.toFixed(1)} s` : "—"} hint={t("status.version", { version: rep.version, id: rep.device_id })} />
    </div>
  );
}

function CurrentCapture({ s }: { s: Status }) {
  const { t } = useTranslation(NS);
  const sdk = useSdk();
  const { Button } = sdk.ui;
  const actions = useActions(sdk);
  const cap = s.tablet?.capture;
  const now = Date.now() / 1000;
  return (
    <Section
      title={t("current.title")}
      actions={
        cap && !(s.want && s.want.session_id === cap.session) ? (
          <Button size="sm" variant="destructive" disabled={actions.stop.isPending} onClick={() => actions.stop.mutate()}>
            {t("current.stop")}
          </Button>
        ) : null
      }
    >
      {s.active_run ? (
        <p className="text-sm">
          {t("current.recording", { run: s.active_run.run_id })}{" "}
          {s.plan === null || s.plan.version_no === null ? (
            <Badge>{t("current.noPlan")}</Badge>
          ) : s.plan.enabled ? (
            <Badge tone="ok">{t("current.planOn", { v: s.plan.version_no })}</Badge>
          ) : (
            <Badge>{t("current.planOff", { v: s.plan.version_no })}</Badge>
          )}
          {s.plan?.inferred ? <span className="ml-2 text-xs text-muted-foreground">{t("current.inferred")}</span> : null}
        </p>
      ) : (
        <p className="text-sm text-muted-foreground">{t("current.noRecord")}</p>
      )}
      {cap ? (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <Badge tone="live">● {t("current.capturing")}</Badge>
          <span className="font-mono text-xs">{cap.session}</span>
          <span className="tabular-nums">{fmtDuration(now - cap.started)}</span>
          {cap.until && cap.until - now < 7 * 3600 ? <span className="text-muted-foreground">{t("current.until", { time: fmtTime(cap.until) })}</span> : null}
          <span className="text-muted-foreground">
            {[cap.video && t("capture.video"), cap.audio && t("capture.audio"), cap.touch && t("capture.touch")].filter(Boolean).join(" · ")}
          </span>
        </div>
      ) : s.online ? (
        <p className="text-sm text-muted-foreground">{t("current.idle")}</p>
      ) : null}
      {s.want && !cap ? <Notice tone="info">{t(`decisions.${s.decision ?? "starting"}`, { defaultValue: t("decisions.starting") })}</Notice> : null}
      <ErrorNotice error={actions.stop.error} />
    </Section>
  );
}

function Transfers({ s }: { s: Status }) {
  const { t } = useTranslation(NS);
  const { data: media } = useMedia(useSdk());
  const spool = s.tablet?.spool;
  const items: React.ReactNode[] = [];
  if (s.pushing) items.push(<span key="push">{t("transfers.pushing", { name: mediaLabel(media?.media, s.pushing.name), size: fmtBytes(s.pushing.bytes) })}</span>);
  if (s.waiting_media.length) items.push(<span key="wait">{t("transfers.waiting", { n: s.waiting_media.length })}</span>);
  if (s.fetching) items.push(<span key="fetch">{t("transfers.fetching", { name: `${s.fetching.session}/${s.fetching.name}` })}</span>);
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm text-muted-foreground">
        <span>{t("transfers.onTablet", { n: spool?.files ?? 0, size: fmtBytes(spool?.bytes ?? 0), free: fmtBytes(spool?.free_bytes) })}</span>
        <span>{t("transfers.host", { free: fmtBytes(s.disk.free_bytes), min: fmtBytes(s.disk.min_free_bytes) })}</span>
        {items}
      </div>
      {s.low_disk ? <Notice tone="warning">{t("transfers.lowDisk", { free: fmtBytes(s.disk.free_bytes), min: fmtBytes(s.disk.min_free_bytes) })}</Notice> : null}
      {s.push_error ? <Notice tone="warning">{t("transfers.pushError", { error: s.push_error })}</Notice> : null}
      {s.fetch_error ? <Notice tone="warning">{t("transfers.fetchError", { error: s.fetch_error })}</Notice> : null}
    </div>
  );
}

function Level({ id, label, value, onApply, busy }: { id: string; label: string; value: number | null; onApply: (v: number) => void; busy: boolean }) {
  const [v, setV] = useState<number | null>(null);
  const shown = v ?? value ?? 0.5;
  return (
    <div className="flex items-center gap-3">
      <label htmlFor={id} className="w-12 text-sm">
        {label}
      </label>
      <input
        id={id}
        type="range"
        min={0}
        max={1}
        step={0.01}
        value={shown}
        disabled={busy}
        className="w-48"
        onChange={(e) => setV(Number(e.target.value))}
        onPointerUp={() => v !== null && (onApply(v), setV(null))}
        onKeyUp={() => v !== null && (onApply(v), setV(null))}
      />
      <span className="w-12 text-sm tabular-nums">{Math.round(shown * 100)}%</span>
    </div>
  );
}

function Controls({ s }: { s: Status }) {
  const { t } = useTranslation(NS);
  const sdk = useSdk();
  const { Button } = sdk.ui;
  const { data: media } = useMedia(sdk);
  const { control } = useActions(sdk);
  const [screen, setScreen] = useState<ScreenDoc>(DEFAULT_SCREEN);
  const rep = s.tablet!;
  const busy = control.isPending;
  const send = (cmd: string, params: Record<string, unknown>) => control.mutate({ cmd, params });
  return (
    <Section title={t("controls.title")}>
      {s.tablet?.capture ? <Notice tone="info">{t("controls.duringCapture")}</Notice> : null}
      <Level id="tk-volume" label={t("status.volume")} value={rep.volume} busy={busy} onApply={(value) => send("set_volume", { value })} />
      <Level id="tk-brightness" label={t("status.brightness")} value={rep.brightness} busy={busy} onApply={(value) => send("set_brightness", { value })} />
      <div className="flex flex-wrap items-center gap-2">
        <span className="w-12 text-sm">{t("status.screen")}</span>
        {MODES.map((m) => (
          <Button key={m} size="sm" variant={rep.player?.mode === m ? "default" : "outline"} disabled={busy} onClick={() => send("set_screen", { mode: m })}>
            {t(`screen.modes.${m}`)}
          </Button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="w-12 text-sm">{t("controls.player")}</span>
        {(["prev", "pause", "resume", "next"] as const).map((a) => (
          <Button key={a} size="sm" variant="outline" disabled={busy || rep.player?.mode !== "content"} onClick={() => send("player", { action: a })}>
            {t(`controls.${a}`)}
          </Button>
        ))}
      </div>
      <details className="rounded-md border border-border p-3">
        <summary className="cursor-pointer text-sm font-medium">{t("controls.show")}</summary>
        <div className="mt-3 space-y-3">
          <ScreenEditor id="tk-show" value={screen} onChange={setScreen} media={media?.media} />
          <Button size="sm" disabled={busy || (screen.mode === "content" && screen.items.length === 0)} onClick={() => send("show", screen as unknown as Record<string, unknown>)}>
            {t("controls.showNow")}
          </Button>
        </div>
      </details>
      <ErrorNotice error={control.error} />
    </Section>
  );
}

function ManualCapture({ s }: { s: Status }) {
  const { t } = useTranslation(NS);
  const sdk = useSdk();
  const { Button, FormField, Input } = sdk.ui;
  const { data: media } = useMedia(sdk);
  const { start } = useActions(sdk);
  const [doc, setDoc] = useState<CaptureDoc>(DEFAULT_CAPTURE);
  const [minutes, setMinutes] = useState(1);
  const blocked = s.want !== null ? t("manual.recordCapture") : s.tablet?.capture ? t("manual.running") : null;
  return (
    <Section title={t("manual.title")}>
      <p className="text-xs text-muted-foreground">{t("manual.hint")}</p>
      <FormField id="tk-man-dur" label={t("manual.duration")} className="w-48">
        <Input id="tk-man-dur" type="number" min={0.1} max={360} step={0.5} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} />
      </FormField>
      <CaptureFields id="tk-man" value={doc} onChange={setDoc} media={media?.media} />
      {blocked ? <Notice tone="info">{blocked}</Notice> : null}
      <Button disabled={!!blocked || start.isPending || !(minutes > 0) || !(doc.video || doc.audio || doc.touch)} onClick={() => start.mutate({ ...doc, duration_s: Math.round(minutes * 60) })}>
        {t("manual.start")}
      </Button>
      <ErrorNotice error={start.error} />
      {start.data ? <Notice tone="ok">{t("manual.started", { session: start.data.session_id })}</Notice> : null}
    </Section>
  );
}

function Spark({ samples, field, label, unit, max }: { samples: Sample[]; field: keyof Sample; label: string; unit: string; max?: number }) {
  const pts = samples.filter((p) => typeof p[field] === "number") as (Sample & Record<string, number>)[];
  if (pts.length < 2) return null;
  const vals = pts.map((p) => p[field] as number);
  const lo = Math.min(...vals, 0);
  const hi = max ?? Math.max(...vals, 1);
  const t0 = pts[0].t;
  const t1 = pts[pts.length - 1].t;
  const d = pts.map((p, i) => `${i ? "L" : "M"}${(((p.t - t0) / (t1 - t0 || 1)) * 300).toFixed(1)},${(60 - ((Number(p[field]) - lo) / (hi - lo || 1)) * 56 - 2).toFixed(1)}`).join(" ");
  return (
    <div className="rounded-md border border-border p-2">
      <div className="flex justify-between text-xs text-muted-foreground">
        <span>{label}</span>
        <span className="tabular-nums text-foreground">
          {vals[vals.length - 1].toFixed(0)} {unit}
        </span>
      </div>
      <svg viewBox="0 0 300 60" className="h-14 w-full" preserveAspectRatio="none" role="img" aria-label={label}>
        <path d={d} fill="none" stroke="currentColor" strokeWidth="1.5" className="text-primary" vectorEffect="non-scaling-stroke" />
      </svg>
    </div>
  );
}

function Health() {
  const { t } = useTranslation(NS);
  const [hours, setHours] = useState(6);
  const q = useSamples(useSdk(), hours);
  const samples = q.data?.samples ?? [];
  return (
    <Section
      title={t("health.title")}
      actions={[1, 6, 24, 168].map((h) => (
        <button key={h} type="button" onClick={() => setHours(h)} className={cn("rounded px-2 py-0.5 text-xs", hours === h ? "bg-muted font-medium" : "text-muted-foreground")}>
          {t("health.hours", { n: h })}
        </button>
      ))}
    >
      {samples.length < 2 ? (
        <p className="text-sm text-muted-foreground">{t("health.empty")}</p>
      ) : (
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          <Spark samples={samples} field="battery" label={t("status.battery")} unit="%" max={100} />
          <Spark samples={samples} field="temperature" label={t("status.temperature")} unit="°C" />
          <Spark samples={samples} field="mem_used_mb" label={t("health.memory")} unit="MB" />
          <Spark samples={samples} field="load1" label={t("health.load")} unit="" />
        </div>
      )}
    </Section>
  );
}

// ------------------------------------------------------------------ media

function MediaLibrary() {
  const { t } = useTranslation(NS);
  const sdk = useSdk();
  const { Button } = sdk.ui;
  const q = useMedia(sdk);
  const actions = useActions(sdk);
  const input = useRef<HTMLInputElement>(null);
  const [uploads, setUploads] = useState<{ name: string; size: number; sent: number; error?: string; done?: boolean }[]>([]);
  const [preview, setPreview] = useState<string | null>(null);

  const upload = async (files: FileList) => {
    for (const file of Array.from(files)) {
      setUploads((u) => [...u, { name: file.name, size: file.size, sent: 0 }]);
      const patch = (p: Partial<{ sent: number; error: string; done: boolean }>) => setUploads((u) => u.map((x) => (x.name === file.name ? { ...x, ...p } : x)));
      try {
        await uploadMedia(sdk, file, (sent) => patch({ sent }));
        patch({ done: true });
      } catch (e) {
        patch({ error: e instanceof Error ? e.message : String(e) });
      }
      void q.refetch();
    }
  };

  const items = q.data?.media ?? [];
  const p = items.find((m) => m.name === preview);
  return (
    <div className="space-y-4">
      <Section
        title={t("media.title")}
        actions={
          <>
            <input ref={input} type="file" multiple className="hidden" accept="video/*,image/*,audio/*" onChange={(e) => e.target.files && void upload(e.target.files)} />
            <Button size="sm" onClick={() => input.current?.click()}>
              {t("media.upload")}
            </Button>
          </>
        }
      >
        <p className="text-xs text-muted-foreground">{t("media.hint", { total: fmtBytes(q.data?.bytes ?? 0) })}</p>
        {uploads.map((u) => (
          <div key={u.name} className="text-sm">
            <div className="flex justify-between gap-2">
              <span className="truncate">{u.name}</span>
              <span className="tabular-nums text-muted-foreground">{u.error ? "" : u.done ? t("media.uploaded") : `${Math.floor((u.sent / u.size) * 100)}%`}</span>
            </div>
            {u.error ? <Notice>{u.error}</Notice> : !u.done ? <progress className="h-1 w-full" max={u.size} value={u.sent} /> : null}
          </div>
        ))}
        <ErrorNotice error={q.error ?? actions.deleteMedia.error ?? actions.pushMedia.error} />
        {items.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("media.empty")}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted-foreground">
                <tr>
                  <th className="py-1 pr-3 font-medium">{t("media.name")}</th>
                  <th className="py-1 pr-3 font-medium">{t("media.kind")}</th>
                  <th className="py-1 pr-3 font-medium">{t("media.size")}</th>
                  <th className="py-1 pr-3 font-medium">{t("media.onTablet")}</th>
                  <th className="py-1 pr-3 font-medium">{t("media.usedBy")}</th>
                  <th className="py-1 font-medium" />
                </tr>
              </thead>
              <tbody>
                {items.map((m) => (
                  <tr key={m.name} className="border-t border-border">
                    <td className="max-w-64 truncate py-1.5 pr-3" title={m.name}>
                      <button type="button" className="underline-offset-2 hover:underline" onClick={() => setPreview(preview === m.name ? null : m.name)}>
                        {m.original_name}
                      </button>
                    </td>
                    <td className="py-1.5 pr-3">{t(`media.kinds.${m.kind}`)}</td>
                    <td className="py-1.5 pr-3 tabular-nums">{fmtBytes(m.bytes)}</td>
                    <td className="py-1.5 pr-3">{m.on_tablet === null ? "—" : m.on_tablet ? <Badge tone="ok">{t("media.yes")}</Badge> : <Badge>{t("media.no")}</Badge>}</td>
                    <td className="py-1.5 pr-3 text-xs">{m.used_by_experiments.length ? t("media.experiments", { ids: m.used_by_experiments.join(", ") }) : "—"}</td>
                    <td className="space-x-1 py-1.5 text-right whitespace-nowrap">
                      {m.on_tablet === false ? (
                        <Button size="xs" variant="outline" onClick={() => actions.pushMedia.mutate(m.name)}>
                          {t("media.push")}
                        </Button>
                      ) : null}
                      <Button
                        size="xs"
                        variant="ghost"
                        disabled={m.used_by_experiments.length > 0}
                        title={m.used_by_experiments.length ? t("media.inUse") : undefined}
                        onClick={() => window.confirm(t("media.confirmDelete", { name: m.original_name })) && actions.deleteMedia.mutate(m.name)}
                      >
                        {t("media.delete")}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
      {p ? (
        <Section title={p.original_name} actions={<Button size="xs" variant="ghost" onClick={() => setPreview(null)}>✕</Button>}>
          {p.kind === "video" ? (
            <video className="max-h-96 max-w-full rounded-md bg-black" controls src={`/api/plugins/${sdk.pluginId}/media/${p.name}/file`} />
          ) : p.kind === "image" ? (
            <img className="max-h-96 max-w-full rounded-md" alt={p.original_name} src={`/api/plugins/${sdk.pluginId}/media/${p.name}/file`} />
          ) : (
            <audio controls src={`/api/plugins/${sdk.pluginId}/media/${p.name}/file`} />
          )}
          <p className="text-xs text-muted-foreground">{t("media.uploadedBy", { who: p.uploaded_by ?? "—", at: fmtTime(p.uploaded_at) })}</p>
        </Section>
      ) : null}
    </div>
  );
}

// ------------------------------------------------------------------ captures, events

const PAGE = 50;

function CapturesPage() {
  const { t } = useTranslation(NS);
  const sdk = useSdk();
  const { Button, NativeSelect } = sdk.ui;
  const [kind, setKind] = useState<string>("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const q = useCaptures(sdk, { kind: kind || null, limit: PAGE, offset });
  useEffect(() => setOffset(0), [kind]);
  const total = q.data?.total ?? 0;
  return (
    <Section
      title={t("captures.title")}
      actions={
        <NativeSelect aria-label={t("captures.kind")} value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">{t("captures.allKinds")}</option>
          {(["run", "manual", "other"] as const).map((k) => (
            <option key={k} value={k}>
              {t(`captures.kinds.${k}`)}
            </option>
          ))}
        </NativeSelect>
      }
    >
      <ErrorNotice error={q.error} />
      <CaptureTable rows={q.data?.captures ?? []} selected={selected} onSelect={setSelected} showRun />
      {total > PAGE ? (
        <div className="flex items-center gap-2 text-sm">
          <Button size="xs" variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
            ←
          </Button>
          <span className="tabular-nums">
            {offset + 1}–{Math.min(offset + PAGE, total)} / {total}
          </span>
          <Button size="xs" variant="outline" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
            →
          </Button>
        </div>
      ) : null}
      {selected ? <CaptureView sessionId={selected} onDeleted={() => setSelected(null)} /> : null}
    </Section>
  );
}

function EventsPage() {
  const { t } = useTranslation(NS);
  const q = useEvents(useSdk(), { limit: 300 });
  return (
    <Section title={t("tabs.events")}>
      <ErrorNotice error={q.error} />
      <EventList events={q.data?.events ?? []} />
    </Section>
  );
}

// ------------------------------------------------------------------ settings

function SettingsPage() {
  const { t } = useTranslation(NS);
  const sdk = useSdk();
  const { Button, FormField, Input } = sdk.ui;
  const q = useSettings(sdk);
  const save = useSaveSettings(sdk);
  const [doc, setDoc] = useState<SettingsDoc | null>(null);
  useEffect(() => {
    if (q.data && doc === null) setDoc(q.data.settings);
  }, [q.data, doc]);
  if (q.error) return <ErrorNotice error={q.error} />;
  if (!doc) return <p className="text-sm text-muted-foreground">{t("loading")}</p>;
  const set = (patch: Partial<SettingsDoc>) => setDoc({ ...doc, ...patch });
  const dirty = JSON.stringify(doc) !== JSON.stringify(q.data?.settings);
  const newToken = async () => set({ token: (await sdk.api<{ token: string }>("settings/new-token", { method: "POST" })).token });
  const snippet = `token = "${doc.token}"\nlisten_port = ${doc.port}`;
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Section title={t("settings.title")}>
        <Toggle id="tk-enabled" checked={doc.enabled} onChange={(enabled) => set({ enabled })} label={t("settings.enabled")} />
        <div className="grid gap-3 sm:grid-cols-[1fr_8rem]">
          <FormField id="tk-host" label={t("settings.host")} hint={t("settings.hostHint")}>
            <Input id="tk-host" value={doc.host} placeholder="192.168.31.x" onChange={(e) => set({ host: e.target.value })} />
          </FormField>
          <FormField id="tk-port" label={t("settings.port")}>
            <Input id="tk-port" type="number" min={1} max={65535} value={doc.port} onChange={(e) => set({ port: Number(e.target.value) })} />
          </FormField>
        </div>
        <FormField id="tk-token" label={t("settings.token")} hint={t("settings.tokenHint")}>
          <div className="flex gap-2">
            <Input id="tk-token" value={doc.token} onChange={(e) => set({ token: e.target.value })} className="font-mono" />
            <Button type="button" size="sm" variant="outline" onClick={() => void newToken()}>
              {t("settings.newToken")}
            </Button>
          </div>
        </FormField>
        <FormField id="tk-minfree" label={t("settings.minFree")} hint={t("settings.minFreeHint")} className="w-64">
          <Input id="tk-minfree" type="number" min={1} max={10000} value={doc.min_free_gb} onChange={(e) => set({ min_free_gb: Number(e.target.value) })} />
        </FormField>
        <div className="flex items-center gap-3">
          <Button disabled={!dirty || save.isPending} onClick={() => save.mutate(doc, { onSuccess: () => setDoc(null) })}>
            {t("settings.save")}
          </Button>
          {q.data ? <span className="text-xs text-muted-foreground">{t("settings.version", { v: q.data.version })}</span> : null}
        </div>
        <ErrorNotice error={save.error} />
      </Section>
      <Section title={t("settings.tabletTitle")}>
        <p className="text-sm">{t("settings.tabletHint")}</p>
        <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs">{snippet}</pre>
        <p className="text-xs text-muted-foreground">{t("settings.tabletRestart")}</p>
      </Section>
    </div>
  );
}
