import "./plugin.css";
import "./i18n";

import type { PluginSlotProps } from "@natural-land/plugin-sdk";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { NS } from "./i18n";

interface Note {
  id: number;
  text: string;
  author: string | null;
  created_at: string;
}

/** Slot "experiment.tab": the experiment's notes (example — replace with the plugin's page). */
export default function ExperimentTab({ sdk }: PluginSlotProps) {
  const { t } = useTranslation(NS);
  const client = useQueryClient();
  const backend = sdk.useBackend();
  const experimentId = sdk.context.experimentId;
  const [text, setText] = useState("");
  // Query keys start with the plugin id so they never collide with the platform's.
  const key = [sdk.pluginId, "notes", experimentId];
  const notes = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => sdk.api<{ notes: Note[] }>(`experiments/${experimentId}/notes`, { signal }),
    enabled: experimentId !== null && backend.state === "ready",
  });
  const add = useMutation({
    mutationFn: (value: string) => sdk.api(`experiments/${experimentId}/notes`, { method: "POST", body: { text: value } }),
    onSuccess: () => {
      setText("");
      void client.invalidateQueries({ queryKey: key });
    },
  });
  const { Button, Card, CardContent } = sdk.ui;

  if (backend.state !== "ready") {
    return <p className="rounded-md border border-border bg-card p-4 text-sm text-muted-foreground">{t("backendDown", { reason: backend.reason ?? backend.state })}</p>;
  }
  return (
    <Card>
      <CardContent className="space-y-3 pt-4">
        <form
          className="flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (text.trim()) add.mutate(text.trim());
          }}
        >
          <input
            className="min-w-0 flex-1 rounded-md border border-input bg-card px-3 py-1.5 text-sm"
            value={text}
            maxLength={2000}
            placeholder={t("placeholder")}
            onChange={(event) => setText(event.target.value)}
          />
          <Button type="submit" disabled={add.isPending || !text.trim()}>
            {t("add")}
          </Button>
        </form>
        {add.error ? <p role="alert" className="text-sm text-destructive">{t("failed", { message: add.error.message })}</p> : null}
        {notes.data?.notes.length === 0 ? <p className="text-sm text-muted-foreground">{t("empty")}</p> : null}
        <ul className="space-y-2">
          {notes.data?.notes.map((note) => (
            <li key={note.id} className="rounded-md border border-border px-3 py-2 text-sm">
              <p className="whitespace-pre-wrap">{note.text}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {note.author ?? "—"} · {new Date(note.created_at).toLocaleString()}
              </p>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
