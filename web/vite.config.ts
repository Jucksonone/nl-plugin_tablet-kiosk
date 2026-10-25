import { nlPlugin } from "@natural-land/plugin-sdk/vite";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import postcss from "postcss";
import { defineConfig, type Plugin } from "vite";

// Every exposed module must be listed in plugin.json ui.contributions (and the other way round).
const nl = nlPlugin({
  id: "tablet_kiosk",
  exposes: {
    "./SystemPage": "./src/SystemPage.tsx",
    "./ExperimentTab": "./src/ExperimentTab.tsx",
    "./RecordTab": "./src/RecordTab.tsx",
    "./LiveCard": "./src/LiveCard.tsx",
  },
});

/**
 * Our utilities apply only inside the plugin's own root (`<Root>`, class SCOPE). Unscoped, a utility
 * the plugin generates (`hidden`, `grid-cols-*` …) comes after the platform's stylesheet and can beat
 * the platform's responsive variants on the platform's own elements. `:where()` keeps the specificity.
 */
export const SCOPE = "nl-plugin-tablet-kiosk";

function scopeCss(scope: string): Plugin {
  return {
    name: "nl-plugin-scope-css",
    enforce: "post",
    generateBundle(_options, bundle) {
      for (const file of Object.values(bundle)) {
        if (file.type !== "asset" || !file.fileName.endsWith(".css")) continue;
        const root = postcss.parse(String(file.source));
        root.walkRules((rule) => {
          const parent = rule.parent;
          if (parent?.type === "atrule" && /keyframes$/.test((parent as postcss.AtRule).name)) return;
          rule.selectors = rule.selectors.map((s) => `:where(.${scope}) ${s}`);
        });
        file.source = root.toString();
      }
    },
  };
}

export default defineConfig({ base: nl.base, plugins: [react(), tailwindcss(), nl.plugin, scopeCss(SCOPE)], build: nl.build });
