// Small shared pieces: the slot root, the sdk in context, notices and formatting.
// Platform components come through `sdk.ui` (AGENTS.md rule 6).
import type { PluginSdk } from "@natural-land/plugin-sdk";
import { createContext, useContext, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { NS } from "./i18n";

export const SdkContext = createContext<PluginSdk | null>(null);

export function useSdk(): PluginSdk {
  const sdk = useContext(SdkContext);
  if (!sdk) throw new Error("tablet plugin: no sdk in context");
  return sdk;
}

/** Every slot renders inside this: the plugin's CSS only applies within it (vite.config.ts scopeCss). */
export function Root({ sdk, children }: { sdk: PluginSdk; children: ReactNode }) {
  return (
    <SdkContext.Provider value={sdk}>
      <div className="nl-plugin-tablet-kiosk">
        <Ready>{children}</Ready>
      </div>
    </SdkContext.Provider>
  );
}

function Ready({ children }: { children: ReactNode }) {
  const { t } = useTranslation(NS);
  const backend = useSdk().useBackend();
  if (backend.state !== "ready") {
    return <Notice tone="warning">{t("backendDown", { reason: backend.reason ?? backend.state })}</Notice>;
  }
  return <>{children}</>;
}

export function cn(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

export function Notice({ tone = "error", children, className }: { tone?: "error" | "warning" | "info" | "ok"; children: ReactNode; className?: string }) {
  const tones = {
    error: "bg-destructive/10 text-destructive",
    warning: "bg-amber-500/10 text-amber-700 dark:text-amber-300",
    info: "bg-muted text-foreground",
    ok: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  };
  return (
    <div role={tone === "error" ? "alert" : "status"} className={cn("rounded-md px-3 py-2 text-sm", tones[tone], className)}>
      {children}
    </div>
  );
}

export function errorText(error: unknown): string {
  if (typeof error === "object" && error !== null && "message" in error) return String((error as { message: unknown }).message);
  return String(error);
}

export function ErrorNotice({ error }: { error: unknown }) {
  const { t } = useTranslation(NS);
  if (!error) return null;
  return <Notice>{t("failed", { message: errorText(error) })}</Notice>;
}

export function Section({ title, actions, children, className }: { title: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  const { Card, CardContent, CardHeader, CardTitle } = useSdk().ui;
  return (
    <Card className={className}>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2 space-y-0">
        <CardTitle className="text-base">{title}</CardTitle>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </CardHeader>
      <CardContent className="space-y-3">{children}</CardContent>
    </Card>
  );
}

export function Stat({ label, value, hint }: { label: ReactNode; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="rounded-md border border-border px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-lg font-semibold tabular-nums">{value}</div>
      {hint ? <div className="text-xs text-muted-foreground">{hint}</div> : null}
    </div>
  );
}

export function Badge({ tone = "muted", children }: { tone?: "muted" | "ok" | "warn" | "bad" | "live"; children: ReactNode }) {
  const tones = {
    muted: "bg-muted text-muted-foreground",
    ok: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
    warn: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
    bad: "bg-destructive/15 text-destructive",
    live: "bg-red-500/15 text-red-600 dark:text-red-400",
  };
  return <span className={cn("inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium", tones[tone])}>{children}</span>;
}

export function Toggle({ id, checked, onChange, label, disabled }: { id: string; checked: boolean; onChange: (v: boolean) => void; label: ReactNode; disabled?: boolean }) {
  return (
    <label htmlFor={id} className="inline-flex cursor-pointer items-center gap-2 text-sm">
      <input id={id} type="checkbox" className="size-4" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

export function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let i = 0;
  while (v >= 1000 && i < units.length - 1) {
    v /= 1000;
    i++;
  }
  return `${v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}

export function fmtTime(v: string | number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const d = typeof v === "number" ? new Date(v * 1000) : new Date(v);
  return d.toLocaleString();
}

export function fmtDuration(s: number | null | undefined): string {
  if (s === null || s === undefined) return "—";
  const total = Math.round(s);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}

export function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`;
}
