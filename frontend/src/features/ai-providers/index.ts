/**
 * AI Provider ecosystem feature — Control Center for the switchable provider
 * architecture (ECOSYSTEM-001).
 *
 * New-web-only (like position-adviser): the backend routes under
 * /api/ai-providers are consumed exclusively by this console, so legacyTab is
 * empty for registry-parity purposes.
 *
 * Everything the screen shows is backend-authoritative. The UI edits no secret
 * values, derives no provider state, and labels an AI output as an AI
 * RECOMMENDATION — never as an executed decision (Sections 57, 61).
 */
import { defineFeature, featurePage } from "@/app/featureModule";

export { aiProvidersApi } from "./api";
export * from "./api";
export * from "./model";

export default defineFeature({
  meta: {
    route: "/ai-providers",
    label: "AI Providers",
    icon: "◈",
    section: "SAFETY & GOVERNANCE",
    legacyTab: "",
    keywords:
      "ai providers system one openrouter internal ml ensemble hybrid shadow comparison fallback decision policy risk gate test centre model selection switching",
  },
  lazy: featurePage(() => import("./ui/AIProvidersPage")),
});
