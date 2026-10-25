import { useTranslation } from "react-i18next";

import type { EventRow } from "./api";
import { NS } from "./i18n";
import { Badge, fmtTime } from "./ui";

const BAD = new Set(["capture_start_failed", "capture_stop_failed", "capture_error", "tablet_offline", "low_disk", "capture_interrupted"]);

function detail(e: EventRow): string {
  const d = { ...e.data };
  delete d.session;
  const parts = Object.entries(d)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`);
  return parts.join(" ").slice(0, 300);
}

export function EventList({ events }: { events: EventRow[] }) {
  const { t } = useTranslation(NS);
  if (events.length === 0) return <p className="text-sm text-muted-foreground">{t("events.empty")}</p>;
  return (
    <ul className="max-h-[32rem] space-y-1 overflow-auto text-sm">
      {events.map((e) => (
        <li key={e.id} className="flex flex-wrap items-baseline gap-x-3 border-b border-border pb-1">
          <span className="w-40 shrink-0 tabular-nums text-muted-foreground">{fmtTime(e.at)}</span>
          <Badge tone={BAD.has(e.name) ? "bad" : e.source === "tablet" ? "muted" : "ok"}>{t(`events.source.${e.source}`)}</Badge>
          <span className="font-medium">{t(`events.names.${e.name}`, { defaultValue: e.name })}</span>
          {e.session_id ? <span className="font-mono text-xs text-muted-foreground">{e.session_id}</span> : null}
          {e.actor ? <span className="text-xs text-muted-foreground">{e.actor}</span> : null}
          <span className="min-w-0 basis-full truncate pl-[10.75rem] text-xs text-muted-foreground">{detail(e)}</span>
        </li>
      ))}
    </ul>
  );
}
