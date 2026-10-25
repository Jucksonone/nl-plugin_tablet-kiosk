// Touches of one capture: where on the screen (map), when (timeline), and each touch (table).
// One touch = one finger put down (backend touches.py). Linked to the capture's video: the
// segment being played narrows the map, its playback time is the timeline's cursor, and a
// click on a touch plays the moment it happened.
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { fileUrl, useMedia } from "./api";
import { NS } from "./i18n";
import { ErrorNotice, Stat, cn, fmtDuration, useSdk } from "./ui";

export interface Contact {
  i: number;
  id: number;
  t0: number;
  t1: number;
  duration: number;
  frames: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  distance: number;
  fingers: number;
  path: [number, number, number][];
}

export interface TouchData {
  started: number | null;
  last: number | null;
  duration: number | null;
  frames: number;
  frames_down: number;
  max_fingers: number;
  count: number;
  truncated: boolean;
  complete: boolean;
  contacts: Contact[];
  screen: { t: number | null; mode: string; file: string | null; name: string | null }[];
  segments: { file: string; t0: number | null; t1: number | null }[];
}

export function useTouches(sessionId: string | null, refetchMs: number | false = 10000) {
  const sdk = useSdk();
  return useQuery({
    queryKey: [sdk.pluginId, "touches", sessionId],
    queryFn: ({ signal }) => sdk.api<TouchData>(`captures/${encodeURIComponent(sessionId ?? "")}/touches`, { signal }),
    enabled: sessionId !== null,
    refetchInterval: refetchMs,
  });
}

// Sequential single hue (blue ramp of the reference palette): earlier touches recede toward the surface,
// later ones stand out. Light: steps 250 → 650 (light → dark). Dark: its own steps, 400 → 100 (dark → light),
// so even the earliest touch keeps its contrast against the dark surface.
const RAMP_LIGHT = ["#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281"];
const RAMP_DARK = ["#3987e5", "#5598e7", "#6da7ec", "#86b6ef", "#9ec5f4", "#b7d3f6", "#cde2fb"];

function isDark(): boolean {
  return typeof document !== "undefined" && document.documentElement.classList.contains("dark");
}

function rampColor(f: number, dark: boolean): string {
  const ramp = dark ? RAMP_DARK : RAMP_LIGHT;
  return ramp[Math.round(Math.min(Math.max(f, 0), 1) * (ramp.length - 1))];
}

const fmtPress = (s: number) => (s < 0.03 ? "< 0.03" : s.toFixed(2));

const fmtT = (s: number) => `${fmtDuration(Math.floor(s))}.${Math.floor((s % 1) * 10)}`;
const pctXY = (v: number) => `${Math.round(v * 100)}%`;

const W = 1600; // the tablet's screen is 16:10
const H = 1000;

export interface Window {
  t0: number;
  t1: number;
}

export function TouchPanel({ sessionId, window, cursor, onSeek, live }: { sessionId: string; window: Window | null; cursor: number | null; onSeek: (t: number) => void; live: boolean }) {
  const { t } = useTranslation(NS);
  const sdk = useSdk();
  const q = useTouches(sessionId, live ? 10000 : false);
  const { data: media } = useMedia(sdk);
  const [onlyWindow, setOnlyWindow] = useState(true);
  const dark = isDark();
  if (q.isPending) return <p className="text-sm text-muted-foreground">{t("loading")}</p>;
  if (q.error) return <ErrorNotice error={q.error} />;
  const d = q.data;
  const span = Math.max(d.duration ?? d.last ?? 0, 1);
  const win = window && onlyWindow ? window : null;
  const shown = win ? d.contacts.filter((c) => c.t1 >= win.t0 && c.t0 < win.t1) : d.contacts;
  const totalDown = d.contacts.reduce((a, c) => a + c.duration, 0);
  // a single image on screen the whole time: draw it faintly behind the touches
  const content = d.screen.filter((e) => e.mode === "content" && e.file);
  const files = [...new Set(content.map((e) => e.file))];
  const bg = d.screen.length > 0 && d.screen.every((e) => e.mode === "content") && files.length === 1 && media?.media.find((m) => m.name === files[0])?.kind === "image" ? files[0] : null;

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label={t("touch.count")} value={d.count} hint={t("touch.perMinute", { n: span > 0 ? ((d.count / span) * 60).toFixed(1) : "—" })} />
        <Stat label={t("touch.downTime")} value={`${totalDown.toFixed(1)} s`} hint={t("touch.frames", { n: d.frames_down })} />
        <Stat label={t("touch.maxFingers")} value={d.max_fingers} />
        <Stat label={t("touch.last")} value={d.contacts.length ? fmtT(d.contacts[d.contacts.length - 1].t0) : "—"} hint={t("touch.sinceStart")} />
      </div>
      {d.count === 0 ? (
        <p className="text-sm text-muted-foreground">{d.complete ? t("touch.none") : t("touch.noneYet")}</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <TimeLegend span={span} dark={dark} />
            {window ? (
              <label className="inline-flex items-center gap-2 text-xs">
                <input type="checkbox" className="size-3.5" checked={onlyWindow} onChange={(e) => setOnlyWindow(e.target.checked)} />
                {t("touch.onlySegment", { from: fmtDuration(window.t0), to: fmtDuration(window.t1) })}
              </label>
            ) : (
              <span className="text-xs text-muted-foreground">{t("touch.playHint")}</span>
            )}
          </div>
          <TouchMap contacts={d.contacts} shown={shown} span={span} cursor={cursor} dark={dark} bg={bg ? fileUrl(sdk, `media/${bg}/file`) : null} onSeek={onSeek} />
          <Timeline contacts={d.contacts} span={span} screen={d.screen} window={window} cursor={cursor} onSeek={onSeek} />
          <TouchTable contacts={shown} screen={d.screen} onSeek={onSeek} csv={fileUrl(sdk, `captures/${encodeURIComponent(sessionId)}/touch.csv`)} truncated={d.truncated} />
        </>
      )}
    </div>
  );
}

function TimeLegend({ span, dark }: { span: number; dark: boolean }) {
  const { t } = useTranslation(NS);
  const stops = [0, 0.25, 0.5, 0.75, 1].map((f) => rampColor(f, dark));
  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground">
      <span>{t("touch.colorByTime")}</span>
      <span className="tabular-nums">0:00</span>
      <span className="h-2 w-28 rounded-sm" style={{ background: `linear-gradient(to right, ${stops.join(",")})` }} aria-hidden />
      <span className="tabular-nums">{fmtDuration(span)}</span>
    </div>
  );
}

function Tip({ x, y, children }: { x: number; y: number; children: ReactNode }) {
  return (
    <div className="pointer-events-none absolute z-10 rounded-md border border-border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-md" style={{ left: `${x}%`, top: `${y}%`, transform: "translate(-50%, calc(-100% - 10px))" }}>
      {children}
    </div>
  );
}

function TouchMap({ contacts, shown, span, cursor, dark, bg, onSeek }: { contacts: Contact[]; shown: Contact[]; span: number; cursor: number | null; dark: boolean; bg: string | null; onSeek: (t: number) => void }) {
  const { t } = useTranslation(NS);
  const [hover, setHover] = useState<Contact | null>(null);
  const shownSet = useMemo(() => new Set(shown.map((c) => c.i)), [shown]);
  const context = contacts.filter((c) => !shownSet.has(c.i));
  const now = cursor === null ? [] : shown.filter((c) => cursor >= c.t0 - 0.3 && cursor <= c.t1 + 0.3);
  return (
    <div className="relative w-full max-w-3xl">
      <svg viewBox={`0 0 ${W} ${H}`} className="block w-full rounded-md border border-border bg-muted/40" role="img" aria-label={t("touch.mapLabel", { n: shown.length })}>
        {bg ? <image href={bg} x={0} y={0} width={W} height={H} preserveAspectRatio="xMidYMid meet" opacity={0.25} /> : null}
        {[0.25, 0.5, 0.75].map((f) => (
          <g key={f} className="text-border" stroke="currentColor" strokeWidth={1} vectorEffect="non-scaling-stroke">
            <line x1={f * W} y1={0} x2={f * W} y2={H} />
            <line x1={0} y1={f * H} x2={W} y2={f * H} />
          </g>
        ))}
        {context.map((c) => (
          <circle key={`ctx-${c.i}`} cx={c.x0 * W} cy={c.y0 * H} r={6} className="text-muted-foreground" fill="currentColor" opacity={0.25} />
        ))}
        {shown.map((c) => {
          const color = rampColor(c.t0 / span, dark);
          return (
            <g key={c.i}>
              {c.path.length > 1 ? (
                <polyline points={c.path.map((p) => `${p[1] * W},${p[2] * H}`).join(" ")} fill="none" stroke={color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
              ) : null}
              <circle cx={c.x0 * W} cy={c.y0 * H} r={9} fill={color} className="stroke-card" strokeWidth={3} />
            </g>
          );
        })}
        {now.map((c) => (
          <circle key={`now-${c.i}`} cx={c.x0 * W} cy={c.y0 * H} r={22} fill="none" className="stroke-foreground" strokeWidth={2} vectorEffect="non-scaling-stroke" />
        ))}
        {/* hit areas bigger than the marks */}
        {shown.map((c) => (
          <circle
            key={`hit-${c.i}`}
            cx={c.x0 * W}
            cy={c.y0 * H}
            r={24}
            fill="transparent"
            className="cursor-pointer"
            tabIndex={0}
            aria-label={t("touch.tip", { i: c.i, t: fmtT(c.t0), d: fmtPress(c.duration), interpolation: { escapeValue: false } })}
            onPointerEnter={() => setHover(c)}
            onPointerLeave={() => setHover(null)}
            onFocus={() => setHover(c)}
            onBlur={() => setHover(null)}
            onClick={() => onSeek(c.t0)}
          />
        ))}
      </svg>
      {hover ? (
        <Tip x={hover.x0 * 100} y={hover.y0 * 100}>
          <div className="font-semibold tabular-nums">
            #{hover.i} · {fmtT(hover.t0)}
          </div>
          <div className="text-muted-foreground">
            {t("touch.tipDetail", { d: fmtPress(hover.duration), x: pctXY(hover.x0), y: pctXY(hover.y0), move: pctXY(hover.distance), interpolation: { escapeValue: false } })}
            {hover.fingers > 1 ? ` · ${t("touch.fingers", { n: hover.fingers })}` : ""}
          </div>
        </Tip>
      ) : null}
      <div className="mt-1 flex justify-between text-[11px] text-muted-foreground">
        <span>{t("touch.topLeft")}</span>
        <span>{t("touch.mapHint")}</span>
      </div>
    </div>
  );
}

function niceBin(span: number): number {
  const target = span / 60;
  for (const s of [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600]) if (s >= target) return s;
  return 3600;
}

/** Axis ticks at a round step (10 s, 30 s, 1 min, …), 3 to 7 of them. */
function ticks(total: number): number[] {
  const step = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400].find((s) => total / s <= 6) ?? 14400;
  const out: number[] = [];
  for (let s = 0; s <= total + 1e-6; s += step) out.push(s);
  return out;
}

function Timeline({ contacts, span, screen, window, cursor, onSeek }: { contacts: Contact[]; span: number; screen: TouchData["screen"]; window: Window | null; cursor: number | null; onSeek: (t: number) => void }) {
  const { t } = useTranslation(NS);
  const [hover, setHover] = useState<number | null>(null);
  const bin = niceBin(span);
  const n = Math.max(1, Math.ceil(span / bin));
  const counts = useMemo(() => {
    const c = new Array<number>(n).fill(0);
    for (const x of contacts) c[Math.min(n - 1, Math.max(0, Math.floor(x.t0 / bin)))]++;
    return c;
  }, [contacts, n, bin]);
  const max = Math.max(...counts, 1);
  const VW = 1000;
  const VH = 120;
  const top = 16;
  const base = 100;
  const bw = VW / n;
  const x = (s: number) => (s / (n * bin)) * VW;
  const bar = (i: number) => {
    const h = (counts[i] / max) * (base - top);
    const x0 = i * bw + 1;
    const w = Math.max(bw - 2, 1); // 2px surface gap between bars
    const r = Math.min(4, w / 2, h);
    return `M${x0},${base} V${base - h + r} Q${x0},${base - h} ${x0 + r},${base - h} H${x0 + w - r} Q${x0 + w},${base - h} ${x0 + w},${base - h + r} V${base} Z`;
  };
  const changes = screen.filter((e, i) => i > 0 && e.t !== null && e.t > 0 && e.t < span);
  return (
    <div className="relative w-full">
      <div className="mb-1 flex justify-between text-xs text-muted-foreground">
        <span>{t("touch.timeline", { bin: bin < 60 ? `${bin} s` : `${bin / 60} min` })}</span>
        <span className="tabular-nums">{t("touch.max", { n: max })}</span>
      </div>
      <svg viewBox={`0 0 ${VW} ${VH}`} className="block h-28 w-full" preserveAspectRatio="none" role="img" aria-label={t("touch.timelineLabel")}>
        {window ? <rect x={x(window.t0)} y={top - 6} width={Math.max(x(window.t1) - x(window.t0), 2)} height={base - top + 6} className="fill-muted" /> : null}
        <line x1={0} y1={base} x2={VW} y2={base} className="stroke-border" strokeWidth={1} vectorEffect="non-scaling-stroke" />
        {counts.map((c, i) => (c > 0 ? <path key={i} d={bar(i)} fill={hover === i ? "#3987e5" : "#2a78d6"} /> : null))}
        {changes.map((e, i) => (
          <line key={i} x1={x(e.t!)} y1={top - 8} x2={x(e.t!)} y2={base} className="stroke-muted-foreground" strokeWidth={1} strokeDasharray="3 3" vectorEffect="non-scaling-stroke">
            <title>{`${fmtT(e.t!)} ${t(`screen.modes.${e.mode}`)}${e.mode === "content" && e.name ? `: ${e.name}` : ""}`}</title>
          </line>
        ))}
        {cursor !== null ? <line x1={x(cursor)} y1={top - 8} x2={x(cursor)} y2={base + 4} className="stroke-foreground" strokeWidth={2} vectorEffect="non-scaling-stroke" /> : null}
        {counts.map((_, i) => (
          <rect key={`hit-${i}`} x={i * bw} y={0} width={bw} height={VH} fill="transparent" className="cursor-pointer" onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)} onClick={() => onSeek(i * bin)} />
        ))}
        {ticks(n * bin).map((s) => (
          <g key={s}>
            <line x1={x(s)} y1={base} x2={x(s)} y2={base + 4} className="stroke-border" strokeWidth={1} vectorEffect="non-scaling-stroke" />
            <text x={x(s)} y={VH - 2} textAnchor={s === 0 ? "start" : x(s) > VW - 30 ? "end" : "middle"} className="fill-muted-foreground" fontSize={11}>
              {fmtDuration(s)}
            </text>
          </g>
        ))}
      </svg>
      {hover !== null ? (
        <Tip x={((hover + 0.5) / n) * 100} y={10}>
          <span className="font-semibold tabular-nums">{t("touch.binCount", { n: counts[hover] })}</span>{" "}
          <span className="text-muted-foreground tabular-nums">
            {fmtDuration(hover * bin)}–{fmtDuration((hover + 1) * bin)}
          </span>
        </Tip>
      ) : null}
      <p className="mt-1 text-[11px] text-muted-foreground">{t("touch.timelineHint")}</p>
    </div>
  );
}

export function screenAt(screen: TouchData["screen"], s: number): TouchData["screen"][number] | null {
  let cur = null;
  for (const e of screen) if (e.t === null || e.t <= s) cur = e;
  return cur;
}

function TouchTable({ contacts, screen, onSeek, csv, truncated }: { contacts: Contact[]; screen: TouchData["screen"]; onSeek: (t: number) => void; csv: string; truncated: boolean }) {
  const { t } = useTranslation(NS);
  const rows = contacts.slice(0, 500);
  return (
    <details>
      <summary className="cursor-pointer text-sm text-muted-foreground">
        {t("touch.table", { n: contacts.length })}
        <a className="ml-3 underline-offset-2 hover:underline" href={csv} download onClick={(e) => e.stopPropagation()}>
          {t("touch.csv")}
        </a>
      </summary>
      <div className="mt-2 max-h-80 overflow-auto">
        <table className="w-full text-xs tabular-nums">
          <thead className="sticky top-0 bg-card text-left text-muted-foreground">
            <tr>
              <th className="py-1 pr-3 font-medium">#</th>
              <th className="py-1 pr-3 font-medium">{t("touch.col.time")}</th>
              <th className="py-1 pr-3 font-medium">{t("touch.col.duration")}</th>
              <th className="py-1 pr-3 font-medium">{t("touch.col.start")}</th>
              <th className="py-1 pr-3 font-medium">{t("touch.col.end")}</th>
              <th className="py-1 pr-3 font-medium">{t("touch.col.move")}</th>
              <th className="py-1 pr-3 font-medium">{t("touch.col.screen")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => {
              const s = screenAt(screen, c.t0);
              return (
                <tr key={c.i} className={cn("cursor-pointer border-t border-border hover:bg-muted/50")} onClick={() => onSeek(c.t0)}>
                  <td className="py-1 pr-3">{c.i}</td>
                  <td className="py-1 pr-3">{fmtT(c.t0)}</td>
                  <td className="py-1 pr-3">{fmtPress(c.duration)} s</td>
                  <td className="py-1 pr-3">
                    {pctXY(c.x0)}, {pctXY(c.y0)}
                  </td>
                  <td className="py-1 pr-3">
                    {pctXY(c.x1)}, {pctXY(c.y1)}
                  </td>
                  <td className="py-1 pr-3">{pctXY(c.distance)}</td>
                  <td className="max-w-40 truncate py-1 pr-3">{s ? (s.mode === "content" ? (s.name ?? "—") : t(`screen.modes.${s.mode}`)) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {contacts.length > rows.length || truncated ? <p className="mt-1 text-xs text-muted-foreground">{t("touch.more")}</p> : null}
      </div>
    </details>
  );
}
