import { nlPlugin } from "@natural-land/plugin-sdk/vite";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Every exposed module must be listed in plugin.json ui.contributions (and the other way round).
const nl = nlPlugin({
  id: "tablet_kiosk",
  exposes: { "./ExperimentTab": "./src/ExperimentTab.tsx" },
});

export default defineConfig({ base: nl.base, plugins: [react(), tailwindcss(), nl.plugin], build: nl.build });
