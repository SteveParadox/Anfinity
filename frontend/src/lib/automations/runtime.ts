import { NonRetriableError } from "inngest";

import { evaluateConditions } from "./conditions";
import { executeAutomationActions, type AutomationExecutionResult } from "./executor";
import { fetchEnabledAutomations } from "./backend";
import { triggerTypeFromEventName, type AutomationTriggerType } from "./registries";
import { parseAutomationDefinition, validateConditionFieldPaths, type AutomationDefinition } from "./validation";

export interface AutomationRuntimeContext {
  event: {
    id?: string;
    name: string;
    triggerType: AutomationTriggerType;
    source: "internal" | "external_webhook";
    timestamp: string;
  };
  workspace: {
    id: string;
    slug?: string | null;
    name?: string | null;
  };
  user?: Record<string, unknown> | null;
  author?: Record<string, unknown> | null;
  note?: Record<string, unknown> | null;
  document?: Record<string, unknown> | null;
  thinkingSession?: Record<string, unknown> | null;
  payload: Record<string, unknown>;
}

export interface AutomationEventData {
  workspace?: {
    id?: string;
    slug?: string | null;
    name?: string | null;
  };
  workspaceId?: string;
  workspace_id?: string;
  user?: Record<string, unknown> | null;
  author?: Record<string, unknown> | null;
  note?: Record<string, unknown> | null;
  document?: Record<string, unknown> | null;
  thinkingSession?: Record<string, unknown> | null;
  thinking_session?: Record<string, unknown> | null;
  payload?: Record<string, unknown>;
  source?: "internal" | "external_webhook";
}

export interface AutomationRouterResult {
  triggerType: AutomationTriggerType;
  workspaceId: string;
  loadedAutomations: number;
  matchedAutomations: number;
  executedAutomations: number;
  invalidAutomations: number;
  results: AutomationExecutionResult[];
}

export function buildRuntimeContext(params: {
  eventName: string;
  eventId?: string;
  eventTs?: number;
  data: AutomationEventData;
}): AutomationRuntimeContext {
  const triggerType = triggerTypeFromEventName(params.eventName);
  if (!triggerType) {
    throw new NonRetriableError(`Unsupported automation event name: ${params.eventName}`);
  }

  const workspaceId = params.data.workspace?.id ?? params.data.workspaceId ?? params.data.workspace_id;
  if (!workspaceId) {
    throw new NonRetriableError(`Automation event ${params.eventName} is missing workspace.id`);
  }

  return {
    event: {
      id: params.eventId,
      name: params.eventName,
      triggerType,
      source: params.data.source ?? "internal",
      timestamp: params.eventTs ? new Date(params.eventTs).toISOString() : new Date().toISOString(),
    },
    workspace: {
      id: workspaceId,
      slug: params.data.workspace?.slug ?? null,
      name: params.data.workspace?.name ?? null,
    },
    user: params.data.user ?? null,
    author: params.data.author ?? null,
    note: params.data.note ?? null,
    document: params.data.document ?? null,
    thinkingSession: params.data.thinkingSession ?? params.data.thinking_session ?? null,
    payload: params.data.payload ?? {},
  };
}

export async function routeAutomationEvent(context: AutomationRuntimeContext): Promise<AutomationRouterResult> {
  const automations = await fetchEnabledAutomations(context.workspace.id, context.event.triggerType);
  const results: AutomationExecutionResult[] = [];
  let matchedAutomations = 0;
  let invalidAutomations = 0;

  for (const rawAutomation of automations) {
    let automation: AutomationDefinition;
    try {
      automation = parseAutomationDefinition(rawAutomation);
    } catch (error) {
      invalidAutomations += 1;
      results.push(buildValidationFailureResult(rawAutomation, error));
      continue;
    }

    let matched = false;
    try {
      matched = shouldRunAutomation(automation, context);
    } catch (error) {
      invalidAutomations += 1;
      results.push(buildValidationFailureResult(automation, error));
      continue;
    }

    if (!matched) {
      continue;
    }

    matchedAutomations += 1;
    results.push(await executeAutomationActions({ automation, context }));
  }

  return {
    triggerType: context.event.triggerType,
    workspaceId: context.workspace.id,
    loadedAutomations: automations.length,
    matchedAutomations,
    executedAutomations: results.filter((result) => result.actionResults.some((action) => action.phase === "execution")).length,
    invalidAutomations,
    results,
  };
}

function buildValidationFailureResult(rawAutomation: unknown, error: unknown): AutomationExecutionResult {
  const fallback = rawAutomation as Partial<AutomationDefinition>;
  return {
    automationId: String(fallback?.id ?? "unknown"),
    automationName: String(fallback?.name ?? "Malformed automation"),
    executionPolicy: "sequential_continue_on_error",
    status: "failed",
    actionResults: [
      {
        phase: "validation",
        status: "failed",
        error: error instanceof Error ? error.message : String(error),
      },
    ],
  };
}

export function shouldRunAutomation(
  automation: AutomationDefinition,
  context: AutomationRuntimeContext
): boolean {
  if (!automation.enabled) {
    return false;
  }

  if (automation.workspaceId !== context.workspace.id) {
    return false;
  }

  if (automation.triggerType !== context.event.triggerType) {
    return false;
  }

  validateConditionFieldPaths(automation.conditions);
  return evaluateConditions(automation.conditions, context);
}
