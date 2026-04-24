import type { AutomationActionType, AutomationTriggerType } from "./registries";
import { parseAutomationDefinition, type AutomationDefinition } from "./validation";
import type { AutomationRuntimeContext } from "./runtime";

const API_BASE_URL = process.env.API_BASE_URL || process.env.VITE_API_URL || "http://localhost:8000";
const AUTOMATION_INTERNAL_TOKEN = process.env.AUTOMATION_INTERNAL_TOKEN || "";

export interface WorkspaceResolution {
  id: string;
  slug: string;
  name?: string | null;
}

export interface BackendActionRequest {
  actionType: AutomationActionType;
  config: Record<string, unknown>;
  context: AutomationRuntimeContext;
  automationId: string;
  actionId?: string;
}

export async function fetchEnabledAutomations(
  workspaceId: string,
  triggerType: AutomationTriggerType
): Promise<AutomationDefinition[]> {
  const response = await automationFetch(
    `/automations/internal/workspaces/${encodeURIComponent(workspaceId)}/enabled?trigger_type=${encodeURIComponent(
      triggerType
    )}`
  );
  const payload = (await response.json()) as { automations?: unknown[] };
  return (payload.automations ?? []).map(parseAutomationDefinition);
}

export async function resolveWorkspaceBySlug(workspaceSlug: string): Promise<WorkspaceResolution> {
  const response = await automationFetch(
    `/automations/internal/workspaces/resolve/${encodeURIComponent(workspaceSlug)}`
  );
  return (await response.json()) as WorkspaceResolution;
}

export async function executeBackendAction(request: BackendActionRequest): Promise<Record<string, unknown>> {
  const response = await automationFetch("/automations/internal/actions", {
    method: "POST",
    body: JSON.stringify({
      action_type: request.actionType,
      config: request.config,
      context: request.context,
      automation_id: request.automationId,
      action_id: request.actionId ?? null,
    }),
  });
  return (await response.json()) as Record<string, unknown>;
}

async function automationFetch(path: string, init: RequestInit = {}): Promise<Response> {
  if (!AUTOMATION_INTERNAL_TOKEN) {
    throw new Error("AUTOMATION_INTERNAL_TOKEN is required for automation backend access");
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "x-automation-internal-token": AUTOMATION_INTERNAL_TOKEN,
      ...(init.headers ?? {}),
    },
  });

  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new Error(`Automation backend request failed (${response.status}): ${body}`);
  }

  return response;
}
