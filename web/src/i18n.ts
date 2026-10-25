import i18next from "i18next";

// The platform's i18next instance (shared): this plugin adds its own namespace once.
export const NS = "plugin-tablet_kiosk";

const resources = {
  "zh-CN": {
    empty: "还没有笔记",
    add: "添加笔记",
    placeholder: "写点什么…",
    failed: "操作失败：{{message}}",
    backendDown: "插件服务不可用（{{reason}}），笔记暂时不能读写",
  },
  en: {
    empty: "No notes yet",
    add: "Add note",
    placeholder: "Write something…",
    failed: "Failed: {{message}}",
    backendDown: "The plugin service is unavailable ({{reason}}); notes cannot be read or written now",
  },
};

for (const [lng, bundle] of Object.entries(resources)) {
  if (!i18next.hasResourceBundle(lng, NS)) i18next.addResourceBundle(lng, NS, bundle);
}
