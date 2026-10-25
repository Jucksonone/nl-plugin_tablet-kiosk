// Editors shared by the experiment plan, the manual capture and the screen controls.
import { useTranslation } from "react-i18next";

import type { CaptureDoc, Media, ScreenDoc, ScreenMode } from "./api";
import { NS } from "./i18n";
import { Badge, Toggle, cn, useSdk } from "./ui";

export const MODES: ScreenMode[] = ["content", "white", "black", "off"];

export const DEFAULT_SCREEN: ScreenDoc = { mode: "content", items: [], loop: true, image_duration: 10 };

export const DEFAULT_CAPTURE: CaptureDoc = { video: true, audio: true, touch: true, screen: null, volume: null, brightness: null, restore: true };

export function mediaLabel(media: Media[] | undefined, name: string): string {
  return media?.find((m) => m.name === name)?.original_name ?? `${name.slice(0, 10)}…`;
}

/** Mode, playlist (from the library, in order), loop and image duration. */
export function ScreenEditor({ id, value, onChange, media }: { id: string; value: ScreenDoc; onChange: (v: ScreenDoc) => void; media: Media[] | undefined }) {
  const { t } = useTranslation(NS);
  const { Button, FormField, Input, NativeSelect } = useSdk().ui;
  const set = (patch: Partial<ScreenDoc>) => onChange({ ...value, ...patch });
  const move = (i: number, d: number) => {
    const items = [...value.items];
    const j = i + d;
    if (j < 0 || j >= items.length) return;
    [items[i], items[j]] = [items[j], items[i]];
    set({ items });
  };
  const available = (media ?? []).filter((m) => !value.items.includes(m.name));
  return (
    <div className="space-y-3">
      <FormField id={`${id}-mode`} label={t("screen.mode")}>
        <NativeSelect id={`${id}-mode`} value={value.mode} onChange={(e) => set({ mode: e.target.value as ScreenMode })}>
          {MODES.map((m) => (
            <option key={m} value={m}>
              {t(`screen.modes.${m}`)}
            </option>
          ))}
        </NativeSelect>
      </FormField>
      {value.mode === "content" ? (
        <>
          <div className="space-y-1">
            <div className="text-sm font-medium">{t("screen.playlist")}</div>
            {value.items.length === 0 ? <p className="text-sm text-muted-foreground">{t("screen.emptyPlaylist")}</p> : null}
            <ol className="space-y-1">
              {value.items.map((name, i) => {
                const m = media?.find((x) => x.name === name);
                return (
                  <li key={name} className="flex items-center gap-2 rounded-md border border-border px-2 py-1 text-sm">
                    <span className="w-5 text-right text-muted-foreground tabular-nums">{i + 1}</span>
                    <span className="min-w-0 flex-1 truncate">{mediaLabel(media, name)}</span>
                    {m ? <Badge>{t(`media.kinds.${m.kind}`)}</Badge> : <Badge tone="bad">{t("media.missing")}</Badge>}
                    <Button type="button" size="xs" variant="ghost" onClick={() => move(i, -1)} disabled={i === 0} aria-label={t("screen.up")}>
                      ↑
                    </Button>
                    <Button type="button" size="xs" variant="ghost" onClick={() => move(i, 1)} disabled={i === value.items.length - 1} aria-label={t("screen.down")}>
                      ↓
                    </Button>
                    <Button type="button" size="xs" variant="ghost" onClick={() => set({ items: value.items.filter((x) => x !== name) })} aria-label={t("screen.remove")}>
                      ✕
                    </Button>
                  </li>
                );
              })}
            </ol>
            {available.length ? (
              <NativeSelect
                aria-label={t("screen.add")}
                value=""
                onChange={(e) => {
                  if (e.target.value) set({ items: [...value.items, e.target.value] });
                }}
              >
                <option value="">{t("screen.add")}</option>
                {available.map((m) => (
                  <option key={m.name} value={m.name}>
                    {m.original_name}
                  </option>
                ))}
              </NativeSelect>
            ) : media && media.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("screen.noMedia")}</p>
            ) : null}
          </div>
          <div className="flex flex-wrap items-end gap-4">
            <Toggle id={`${id}-loop`} checked={value.loop} onChange={(loop) => set({ loop })} label={t("screen.loop")} />
            <FormField id={`${id}-imgdur`} label={t("screen.imageDuration")} className="w-40">
              <Input
                id={`${id}-imgdur`}
                type="number"
                min={0.5}
                max={3600}
                step={0.5}
                value={value.image_duration}
                onChange={(e) => set({ image_duration: Number(e.target.value) || 10 })}
              />
            </FormField>
          </div>
          <p className="text-xs text-muted-foreground">{t("screen.singleHint")}</p>
        </>
      ) : null}
    </div>
  );
}

/** An optional 0–1 level: unchecked = leave the tablet's own setting. */
export function LevelField({ id, label, value, onChange }: { id: string; label: string; value: number | null; onChange: (v: number | null) => void }) {
  const { t } = useTranslation(NS);
  return (
    <div className="space-y-1">
      <Toggle id={`${id}-on`} checked={value !== null} onChange={(on) => onChange(on ? 0.6 : null)} label={label} />
      {value !== null ? (
        <div className="flex items-center gap-2">
          <input id={id} type="range" min={0} max={1} step={0.01} value={value} onChange={(e) => onChange(Number(e.target.value))} className="w-48" aria-label={label} />
          <span className="w-12 text-sm tabular-nums">{Math.round(value * 100)}%</span>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">{t("capture.leaveAsIs")}</p>
      )}
    </div>
  );
}

/** What a capture records and what the tablet shows meanwhile. */
export function CaptureFields({ id, value, onChange, media }: { id: string; value: CaptureDoc; onChange: (v: CaptureDoc) => void; media: Media[] | undefined }) {
  const { t } = useTranslation(NS);
  const set = (patch: Partial<CaptureDoc>) => onChange({ ...value, ...patch });
  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <div className="text-sm font-medium">{t("capture.record")}</div>
        <div className="flex flex-wrap gap-4">
          <Toggle id={`${id}-video`} checked={value.video} onChange={(video) => set({ video })} label={t("capture.video")} />
          <Toggle id={`${id}-audio`} checked={value.audio} onChange={(audio) => set({ audio })} label={t("capture.audio")} />
          <Toggle id={`${id}-touch`} checked={value.touch} onChange={(touch) => set({ touch })} label={t("capture.touch")} />
        </div>
      </div>
      <div className="space-y-2">
        <Toggle id={`${id}-screen-on`} checked={value.screen !== null} onChange={(on) => set({ screen: on ? DEFAULT_SCREEN : null })} label={t("capture.setScreen")} />
        {value.screen ? (
          <div className={cn("rounded-md border border-border p-3")}>
            <ScreenEditor id={`${id}-screen`} value={value.screen} onChange={(screen) => set({ screen })} media={media} />
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">{t("capture.screenAsIs")}</p>
        )}
      </div>
      <div className="flex flex-wrap gap-8">
        <LevelField id={`${id}-volume`} label={t("capture.volume")} value={value.volume} onChange={(volume) => set({ volume })} />
        <LevelField id={`${id}-brightness`} label={t("capture.brightness")} value={value.brightness} onChange={(brightness) => set({ brightness })} />
      </div>
      <Toggle id={`${id}-restore`} checked={value.restore} onChange={(restore) => set({ restore })} label={t("capture.restore")} />
    </div>
  );
}
