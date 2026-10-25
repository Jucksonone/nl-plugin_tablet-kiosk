import "./plugin.css";
import "./i18n";

import type { PluginSlotProps } from "@natural-land/plugin-sdk";
import { useTranslation } from "react-i18next";

import { useMedia, useStatus } from "./api";
import { mediaLabel } from "./forms";
import { NS } from "./i18n";
import { useTouches } from "./touch";
import { Badge, ErrorNotice, Notice, Root, fmtDuration, pct, useSdk } from "./ui";

/** Slot "live.card": the tablet at a glance while recording. */
export default function LiveCard({ sdk }: PluginSlotProps) {
  return (
    <Root sdk={sdk}>
      <Body />
    </Root>
  );
}

function Body() {
  const { t } = useTranslation(NS);
  const sdk = useSdk();
  const q = useStatus(sdk, 3000);
  const { data: media } = useMedia(sdk);
  const capturing = q.data?.tablet?.capture?.session ?? null;
  const touches = useTouches(capturing, 10000);
  if (q.error) return <ErrorNotice error={q.error} />;
  if (!q.data) return <p className="text-sm text-muted-foreground">{t("loading")}</p>;
  const s = q.data;
  if (!s.configured) return <Notice tone="info">{t("status.notConfigured")}</Notice>;
  const rep = s.tablet;
  const cap = rep?.capture;
  return (
    <div className="space-y-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        {s.online ? <Badge tone="ok">{t("live.online")}</Badge> : <Badge tone="bad">{t("live.offline")}</Badge>}
        {cap ? <Badge tone="live">● {t("current.capturing")} {fmtDuration(Date.now() / 1000 - cap.started)}</Badge> : s.online ? <Badge>{t("current.idle")}</Badge> : null}
        {s.want && !cap ? <Badge tone="warn">{t(`decisions.${s.decision ?? "starting"}`, { defaultValue: t("decisions.starting") })}</Badge> : null}
      </div>
      {rep ? (
        <div className="grid grid-cols-2 gap-x-4 gap-y-1">
          <span className="text-muted-foreground">{t("status.screen")}</span>
          <span className="truncate">
            {t(`screen.modes.${rep.player?.mode ?? "content"}`)}
            {rep.player?.mode === "content" && rep.player.current ? ` · ${mediaLabel(media?.media, rep.player.current)}` : ""}
          </span>
          <span className="text-muted-foreground">{t("live.levels")}</span>
          <span>
            {pct(rep.volume)} / {pct(rep.brightness)}
          </span>
          <span className="text-muted-foreground">{t("status.battery")}</span>
          <span>
            {rep.battery?.capacity ? `${rep.battery.capacity}%` : "—"} · {rep.temperature !== null ? `${rep.temperature.toFixed(0)} °C` : "—"}
          </span>
        </div>
      ) : null}
      {cap && cap.touch && touches.data ? (
        <p className="text-sm">
          {t("live.touches", { n: touches.data.count })}
          {touches.data.contacts.length ? <span className="ml-2 text-xs text-muted-foreground">{t("live.lastTouch", { t: fmtDuration(touches.data.contacts[touches.data.contacts.length - 1].t0) })}</span> : null}
        </p>
      ) : null}
      {s.active_run && !s.want ? <p className="text-xs text-muted-foreground">{t("live.noPlan")}</p> : null}
      {s.low_disk ? <Notice tone="warning">{t("live.lowDisk")}</Notice> : null}
    </div>
  );
}
