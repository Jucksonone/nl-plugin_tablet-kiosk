import "./plugin.css";
import "./i18n";

import type { PluginSlotProps } from "@natural-land/plugin-sdk";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useMedia, type Capture, type EventRow, type PlanDoc } from "./api";
import { CaptureTable, CaptureView } from "./captures";
import { EventList } from "./events";
import { mediaLabel } from "./forms";
import { NS } from "./i18n";
import { Badge, ErrorNotice, Notice, Root, Section, pct, useSdk } from "./ui";

interface RunDetail {
  run_id: string;
  binding: { plan: (PlanDoc & { version_no: number }) | null; inferred: boolean; seen_at: string } | null;
  captures: Capture[];
  events: EventRow[];
}

/** Slot "record.tab": what the tablet did during this record — plan used, captures, events. */
export default function RecordTab({ sdk }: PluginSlotProps) {
  return (
    <Root sdk={sdk}>
      <Body />
    </Root>
  );
}

function Body() {
  const { t } = useTranslation(NS);
  const sdk = useSdk();
  const runId = sdk.context.runId;
  const { data: media } = useMedia(sdk);
  const [selected, setSelected] = useState<string | null>(null);
  const q = useQuery({
    queryKey: [sdk.pluginId, "run", runId],
    queryFn: ({ signal }) => sdk.api<RunDetail>(`runs/${encodeURIComponent(runId ?? "")}`, { signal }),
    enabled: runId !== null,
    refetchInterval: 5000,
  });
  if (runId === null) return <Notice tone="info">{t("record.noRun")}</Notice>;
  if (q.error) return <ErrorNotice error={q.error} />;
  if (!q.data) return <p className="text-sm text-muted-foreground">{t("loading")}</p>;
  const plan = q.data.binding?.plan ?? null;
  const first = selected ?? (q.data.captures.length === 1 ? q.data.captures[0].session_id : null);
  return (
    <div className="space-y-4">
      <Section title={t("record.plan")}>
        {!q.data.binding ? (
          <p className="text-sm text-muted-foreground">{t("record.notSeen")}</p>
        ) : !plan ? (
          <p className="text-sm text-muted-foreground">{t("record.noPlan")}</p>
        ) : (
          <div className="space-y-1 text-sm">
            <p>
              {t("record.planVersion", { v: plan.version_no })} {plan.enabled ? <Badge tone="ok">{t("plan.on")}</Badge> : <Badge>{t("plan.off")}</Badge>}
              {q.data.binding.inferred ? <span className="ml-2 text-xs text-muted-foreground">{t("current.inferred")}</span> : null}
            </p>
            {plan.enabled ? (
              <p className="text-muted-foreground">
                {[plan.video && t("capture.video"), plan.audio && t("capture.audio"), plan.touch && t("capture.touch")].filter(Boolean).join(" · ")}
                {plan.screen ? ` · ${t(`screen.modes.${plan.screen.mode}`)}${plan.screen.mode === "content" ? `：${plan.screen.items.map((n) => mediaLabel(media?.media, n)).join("、")}` : ""}` : ""}
                {plan.volume !== null ? ` · ${t("capture.volume")} ${pct(plan.volume)}` : ""}
                {plan.brightness !== null ? ` · ${t("capture.brightness")} ${pct(plan.brightness)}` : ""}
              </p>
            ) : null}
          </div>
        )}
      </Section>
      <Section title={t("record.captures")}>
        <CaptureTable rows={q.data.captures} selected={first} onSelect={setSelected} />
        {first ? <CaptureView sessionId={first} onDeleted={() => setSelected(null)} /> : null}
      </Section>
      <Section title={t("tabs.events")}>
        <EventList events={q.data.events} />
      </Section>
    </div>
  );
}
