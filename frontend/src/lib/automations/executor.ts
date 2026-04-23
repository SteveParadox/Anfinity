import { ACTION_TYPES, type AutomationActionType } from "./registries";
import { executeBackendAction } from "./backend";
import { interpolateConfig } from "./templates";
import { validateActionConfig, type AutomationAction, type AutomationDefinition } from "./validation";
import type { AutomationRuntimeContext } from "./runtime";

export interface ExecuteAutomationActionsInput {
  automation: AutomationDefinition;
  context: AutomationRuntimeContext;
}

export interface ActionExecutionResult {
  actionId?: string;
  actionType?: AutomationActionType;
  phase?: "validation" | "execution";
  status: "success" | "failed";
  output?: unknown;
  error?: string;
}

export interface AutomationExecutionResult {
  automationId: string;
  automationName: string;
  executionPolicy: "sequential_continue_on_error";
  status: "success" | "partial_failure" | "failed";
  actionResults: ActionExecutionResult[];
}

export async function executeAutomationActions(
  input: ExecuteAutomationActionsInput
): Promise<AutomationExecutionResult> {
  const actionResults: ActionExecutionResult[] = [];

  for (const action of input.automation.actions) {
    actionResults.push(await executeSingleAction(action, input));
  }

  const failedCount = actionResults.filter((result) => result.status === "failed").length;
  const status =
    failedCount === 0 ? "success" : failedCount === actionResults.length ? "failed" : "partial_failure";

  return {
    automationId: input.automation.id,
    automationName: input.automation.name,
    executionPolicy: "sequential_continue_on_error",
    status,
    actionResults,
  };
}

export async function executeSingleAction(
  action: AutomationAction,
  input: ExecuteAutomationActionsInput
): Promise<ActionExecutionResult> {
  try {
    const validated = validateActionConfig(action.type, action.config);
    const resolvedConfig = interpolateConfig(validated, input.context) as Record<string, unknown>;
    const output = await dispatchAction(action.type, resolvedConfig, action, input);
    return {
      actionId: action.id,
      actionType: action.type,
      phase: "execution",
      status: "success",
      output,
    };
  } catch (error) {
    return {
      actionId: action.id,
      actionType: action.type,
      phase: "execution",
      status: "failed",
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

async function dispatchAction(
  actionType: AutomationActionType,
  config: Record<string, unknown>,
  action: AutomationAction,
  input: ExecuteAutomationActionsInput
): Promise<unknown> {
  const definition = ACTION_TYPES[actionType];

  if (definition.execution === "backend") {
    return executeBackendAction({
      actionType,
      config,
      context: input.context,
      automationId: input.automation.id,
      actionId: action.id,
    });
  }

  if (actionType === "call_webhook") {
    return callWebhook(config);
  }

  if (actionType === "send_email") {
    return sendEmail(config);
  }

  throw new Error(`No action handler registered for ${actionType}`);
}

async function callWebhook(config: Record<string, unknown>): Promise<Record<string, unknown>> {
  const url = validateOutboundWebhookUrl(String(config.url || ""));

  const method = String(config.method || "POST");
  const headers = isRecord(config.headers) ? stringifyHeaderValues(config.headers) : {};
  const body = buildRequestBody(config.body);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15_000);

  let response: Response;
  try {
    response = await fetch(url.toString(), {
      method,
      headers: {
        "Content-Type": "application/json",
        ...headers,
      },
      body,
      signal: controller.signal,
    });
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error("Webhook request timed out after 15 seconds");
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }

  const responseBody = await response.text().catch(() => "");
  if (!response.ok) {
    throw new Error(`Webhook request failed (${response.status}): ${responseBody.slice(0, 500)}`);
  }

  return {
    status: response.status,
    responseBody: responseBody.slice(0, 2_000),
  };
}

async function sendEmail(config: Record<string, unknown>): Promise<Record<string, unknown>> {
  const emailWebhookUrl = process.env.AUTOMATION_EMAIL_WEBHOOK_URL;
  if (!emailWebhookUrl) {
    throw new Error("AUTOMATION_EMAIL_WEBHOOK_URL is required for send_email actions");
  }

  return callWebhook({
    url: emailWebhookUrl,
    method: "POST",
    headers: {},
    body: {
      to: config.to,
      subject: config.subject,
      body: config.body,
    },
  });
}

function buildRequestBody(body: unknown): string | undefined {
  if (body === undefined || body === null || body === "") {
    return undefined;
  }

  if (typeof body === "string") {
    const trimmed = body.trim();
    if (!trimmed) {
      return undefined;
    }

    try {
      JSON.parse(trimmed);
      return trimmed;
    } catch {
      return JSON.stringify({ text: body });
    }
  }

  return JSON.stringify(body);
}

function stringifyHeaderValues(headers: Record<string, unknown>): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [key, value] of Object.entries(headers)) {
    if (typeof value === "string") {
      result[key] = value;
    }
  }
  return result;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function validateOutboundWebhookUrl(rawUrl: string): URL {
  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    throw new Error("call_webhook requires an absolute http(s) URL");
  }

  if (url.protocol !== "https:" && url.protocol !== "http:") {
    throw new Error("call_webhook requires an absolute http(s) URL");
  }

  if (url.protocol === "http:" && process.env.AUTOMATION_ALLOW_INSECURE_WEBHOOKS !== "true") {
    throw new Error("call_webhook requires https URLs unless AUTOMATION_ALLOW_INSECURE_WEBHOOKS is enabled");
  }

  if (isPrivateOrLocalHostname(url.hostname) && process.env.AUTOMATION_ALLOW_PRIVATE_WEBHOOKS !== "true") {
    throw new Error("call_webhook cannot target localhost or private network addresses");
  }

  return url;
}

function isPrivateOrLocalHostname(hostname: string): boolean {
  const normalized = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (normalized === "localhost" || normalized.endsWith(".localhost") || normalized === "::1") {
    return true;
  }

  const octets = normalized.split(".").map((part) => Number(part));
  if (octets.length !== 4 || octets.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) {
    return false;
  }

  const [first = 0, second = 0] = octets;
  return (
    first === 10 ||
    first === 127 ||
    (first === 172 && second >= 16 && second <= 31) ||
    (first === 192 && second === 168) ||
    (first === 169 && second === 254)
  );
}
