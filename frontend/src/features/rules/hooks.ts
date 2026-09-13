/**
 * Rules: React Query hooks bound to useCases (query keys namespaced ["rules", ...]).
 * The hook implementations live in ./useCases (application layer owns the
 * transport binding); this file is the stable import surface for ui/.
 */

export { RULES_QUERY_KEY, useRulesQuery, useToggleRule } from "./useCases";
export type { RuleCommandOutcome } from "./useCases";
