/**
 * i18n message registry — the ONE place every scope's messages module is
 * registered with lib/i18n. ORCHESTRATOR-OWNED: lanes never edit this file;
 * every scope stub below was pre-wired in the wave's scaffold commit, so
 * lanes only fill their own <scope>/i18n.ts and need zero shared-file edits.
 *
 * Side-effect import lives in main.tsx (before first render), so every
 * dictionary is registered before any component calls t().
 */
import { registerMessages } from "@/lib/i18n";
import { MESSAGES as app } from "@/app/i18n";
import { MESSAGES as components } from "@/components/i18n";
import { MESSAGES as sharedWidgets } from "@/pages/_shared/i18n";
import { MESSAGES as dashboard } from "@/pages/Dashboard/i18n";
import { MESSAGES as trading } from "@/pages/Trading/i18n";
import { MESSAGES as positions } from "@/pages/Positions/i18n";
import { MESSAGES as risk } from "@/pages/Risk/i18n";
import { MESSAGES as ml } from "@/pages/ML/i18n";
import { MESSAGES as intelligence } from "@/pages/Intelligence/i18n";
import { MESSAGES as audit } from "@/pages/Audit/i18n";
import { MESSAGES as settings } from "@/pages/Settings/i18n";
import { MESSAGES as account } from "@/features/account/i18n";
import { MESSAGES as aiAnalysis } from "@/features/ai-analysis/i18n";
import { MESSAGES as commandCenter } from "@/features/command-center/i18n";
import { MESSAGES as config } from "@/features/config/i18n";
import { MESSAGES as controlCenter } from "@/features/control-center/i18n";
import { MESSAGES as database } from "@/features/database/i18n";
import { MESSAGES as decisionTrace } from "@/features/decision-trace/i18n";
import { MESSAGES as debug } from "@/features/debug/i18n";
import { MESSAGES as dependency } from "@/features/dependency/i18n";
import { MESSAGES as factory } from "@/features/factory/i18n";
import { MESSAGES as governance } from "@/features/governance/i18n";
import { MESSAGES as health } from "@/features/health/i18n";
import { MESSAGES as incidents } from "@/features/incidents/i18n";
import { MESSAGES as liquidity } from "@/features/liquidity/i18n";
import { MESSAGES as marketplace } from "@/features/marketplace/i18n";
import { MESSAGES as modelStudio } from "@/features/model-studio/i18n";
import { MESSAGES as news } from "@/features/news/i18n";
import { MESSAGES as positionAdviser } from "@/features/position-adviser/i18n";
import { MESSAGES as provisioning } from "@/features/provisioning/i18n";
import { MESSAGES as research } from "@/features/research/i18n";
import { MESSAGES as rules } from "@/features/rules/i18n";

/** Every scope, registered sequentially so duplicate keys can be reported
 *  per scope by registerMessages (first registration wins). */
for (const scope of [
  app,
  components,
  sharedWidgets,
  dashboard,
  trading,
  positions,
  risk,
  ml,
  intelligence,
  audit,
  settings,
  account,
  aiAnalysis,
  commandCenter,
  config,
  controlCenter,
  database,
  decisionTrace,
  debug,
  dependency,
  factory,
  governance,
  health,
  incidents,
  liquidity,
  marketplace,
  modelStudio,
  news,
  positionAdviser,
  provisioning,
  research,
  rules,
]) {
  registerMessages(scope);
}
