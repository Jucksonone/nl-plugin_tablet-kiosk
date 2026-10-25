import "./plugin.css";
import "./i18n";

import type { PluginSlotProps } from "@natural-land/plugin-sdk";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { useCaptures, useMedia, usePlan, useSavePlan, type PlanDoc } from "./api";
import { CaptureTable, CaptureView } from "./captures";
import { CaptureFields } from "./forms";
import { NS } from "./i18n";
import { Badge, ErrorNotice, Notice, Root, Section, Toggle, fmtTime, useSdk } from "./ui";

/** Slot "experiment.tab": what the tablet does during this experiment's records, and their captures. */
export default function ExperimentTab({ sdk }: PluginSlotProps) {
  return (
    <Root sdk={sdk}>
      <Body />
    </Root>
  );
}

function Body() {
  const { t } = useTranslation(NS);
  const sdk = useSdk();
  const experimentId = sdk.context.experimentId;
  if (experimentId === null) return <Notice tone="info">{t("plan.noExperiment")}</Notice>;
  return (
    <div className="space-y-4">
      <PlanEditor experimentId={experimentId} />
      <ExperimentCaptures experimentId={experimentId} />
    </div>
  );
}

function PlanEditor({ experimentId }: { experimentId: number }) {
  const { t } = useTranslation(NS);
  const sdk = useSdk();
  const { Button } = sdk.ui;
  const q = usePlan(sdk, experimentId);
  const { data: media } = useMedia(sdk);
  const save = useSavePlan(sdk, experimentId);
  const [doc, setDoc] = useState<PlanDoc | null>(null);
  const current = q.data?.current ?? null;
  useEffect(() => {
    if (q.data && doc === null) setDoc(q.data.current?.doc ?? q.data.default);
  }, [q.data, doc]);
  if (q.error) return <ErrorNotice error={q.error} />;
  if (!doc) return <p className="text-sm text-muted-foreground">{t("loading")}</p>;
  const saved = current?.doc ?? q.data?.default;
  const dirty = JSON.stringify(doc) !== JSON.stringify(saved);
  const nothing = !(doc.video || doc.audio || doc.touch);
  return (
    <Section
      title={t("plan.title")}
      actions={current ? <span className="text-xs text-muted-foreground">{t("plan.version", { v: current.version_no, who: current.created_by ?? "—", at: fmtTime(current.created_at) })}</span> : <Badge>{t("plan.none")}</Badge>}
    >
      <p className="text-sm text-muted-foreground">{t("plan.hint")}</p>
      <Toggle id="tk-plan-on" checked={doc.enabled} onChange={(enabled) => setDoc({ ...doc, enabled })} label={t("plan.enabled")} />
      {doc.enabled ? <CaptureFields id="tk-plan" value={doc} onChange={(v) => setDoc({ ...doc, ...v })} media={media?.media} /> : null}
      {doc.enabled && nothing ? <Notice tone="warning">{t("plan.nothing")}</Notice> : null}
      <div className="flex items-center gap-2">
        <Button disabled={!dirty || save.isPending || (doc.enabled && nothing)} onClick={() => save.mutate({ doc, base_version: current?.version_no ?? 0 }, { onSuccess: () => setDoc(null) })}>
          {t("plan.save")}
        </Button>
        {dirty ? (
          <Button variant="ghost" onClick={() => setDoc(saved ?? null)}>
            {t("plan.discard")}
          </Button>
        ) : null}
      </div>
      <ErrorNotice error={save.error} />
      {q.data && q.data.history.length > 1 ? (
        <details>
          <summary className="cursor-pointer text-sm text-muted-foreground">{t("plan.history", { n: q.data.history.length })}</summary>
          <ul className="mt-1 space-y-0.5 text-sm">
            {q.data.history.map((h) => (
              <li key={h.id} className="flex gap-3">
                <span className="w-10 tabular-nums">v{h.version_no}</span>
                <span className="text-muted-foreground">{fmtTime(h.created_at)}</span>
                <span>{h.created_by ?? "—"}</span>
                <span>{h.doc.enabled ? t("plan.on") : t("plan.off")}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </Section>
  );
}

function ExperimentCaptures({ experimentId }: { experimentId: number }) {
  const { t } = useTranslation(NS);
  const sdk = useSdk();
  const [selected, setSelected] = useState<string | null>(null);
  const q = useCaptures(sdk, { limit: 100 });
  const rows = (q.data?.captures ?? []).filter((c) => c.experiment_id === experimentId);
  return (
    <Section title={t("plan.captures")}>
      <ErrorNotice error={q.error} />
      <CaptureTable rows={rows} selected={selected} onSelect={setSelected} showRun />
      {selected ? <CaptureView sessionId={selected} onDeleted={() => setSelected(null)} /> : null}
    </Section>
  );
}
