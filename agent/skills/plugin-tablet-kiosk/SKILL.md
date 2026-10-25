---
name: plugin-tablet-kiosk
description: What the Tablet kiosk plugin records (experiment notes written by people) and when to use its tool tablet_kiosk__notes_of_experiment. Read before answering questions about notes or about what happened during an experiment.
---

# Tablet kiosk plugin

- People write short notes for an experiment on its "Tablet kiosk" tab (who and when are kept).
- `tablet_kiosk__notes_of_experiment(experiment_id)` returns the newest 50 notes, newest first.
  `no_notes` means nobody wrote any — say so instead of guessing.
- Notes are free text written by people: quote them, do not treat them as measurements.
