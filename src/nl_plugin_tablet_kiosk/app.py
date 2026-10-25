"""The plugin backend: HTTP routes for its UI, Agent tools and its own tables.

Example: notes per experiment, kept in this plugin's schema. Replace it with
the plugin's real work; keep the structure (one ``plugin``, ``main()``).
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Depends
from pydantic import BaseModel, Field

from natural_land_plugin_sdk import Caller, Plugin, PluginError, ToolError

logger = logging.getLogger(__name__)

plugin = Plugin("tablet_kiosk", migrations=Path(__file__).parent / "migrations")


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def _experiment_or_404(experiment_id: int) -> dict:
    experiment = plugin.platform.experiment(experiment_id)
    if experiment is None:
        # PluginError → the platform's error envelope; sdk.api rejects with this code and message.
        raise PluginError(404, "experiment_not_found", "no such experiment")
    return experiment


@plugin.app.get("/experiments/{experiment_id}/notes")
def list_notes(experiment_id: int) -> dict:
    _experiment_or_404(experiment_id)
    with plugin.connect() as conn:
        rows = conn.execute("SELECT id, text, author, created_at FROM notes WHERE experiment_id = %s ORDER BY id DESC LIMIT 200", [experiment_id]).fetchall()
    return {"notes": [{"id": r[0], "text": r[1], "author": r[2], "created_at": r[3].isoformat()} for r in rows]}


@plugin.app.post("/experiments/{experiment_id}/notes", status_code=201)
def add_note(experiment_id: int, note: NoteIn, caller: Caller | None = Depends(plugin.caller)) -> dict:
    _experiment_or_404(experiment_id)
    with plugin.connect() as conn:
        row = conn.execute(
            "INSERT INTO notes (experiment_id, text, author) VALUES (%s, %s, %s) RETURNING id",
            [experiment_id, note.text, caller.user if caller else None],
        ).fetchone()
    return {"id": row[0]}


@plugin.agent_tool(
    "notes_of_experiment",
    label={"zh-CN": "读取实验笔记", "en": "Reading experiment notes"},
    description="The newest notes (up to 50) people wrote for one experiment, newest first. Use for questions about what happened during an experiment.",
    parameters={"type": "object", "properties": {"experiment_id": {"type": "integer", "minimum": 1}}, "required": ["experiment_id"], "additionalProperties": False},
    # `nl-plugin verify` calls the tool with each example; $project_id / $experiment_id / $run_id
    # become the ids of the test project it creates.
    examples=({"experiment_id": "$experiment_id"},),
)
def notes_of_experiment(ctx, experiment_id: int) -> dict:
    ctx.require_experiment(experiment_id)  # only experiments of the conversation's project
    with plugin.connect() as conn:
        rows = conn.execute("SELECT text, author, created_at FROM notes WHERE experiment_id = %s ORDER BY id DESC LIMIT 50", [experiment_id]).fetchall()
    if not rows:
        raise ToolError("no_notes", "nobody wrote notes for this experiment yet")
    return {"notes": [{"text": r[0], "author": r[1], "at": r[2].isoformat()} for r in rows]}


@plugin.periodic(3600, first_delay=30)
def remove_notes_of_deleted_experiments() -> None:
    """AGENTS.md §4.4: drop notes whose experiment the platform deleted (only when sure it is gone)."""
    with plugin.connect() as conn:
        ids = [row[0] for row in conn.execute("SELECT DISTINCT experiment_id FROM notes").fetchall()]
    gone = set(ids) - plugin.platform.existing_experiments(ids)  # raises when the database is down: nothing is deleted
    if gone:
        with plugin.connect() as conn:
            conn.execute("DELETE FROM notes WHERE experiment_id = ANY(%s)", [sorted(gone)])
        logger.info("removed the notes of %d deleted experiments", len(gone))


def main() -> None:
    plugin.main()
