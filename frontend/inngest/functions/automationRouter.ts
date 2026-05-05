import { NonRetriableError } from "inngest";

import { inngest } from "../client.js";
import { AUTOMATION_EVENT_NAMES } from "../../src/lib/automations/registries.js";
import { buildRuntimeContext, routeAutomationEvent, type AutomationEventData } from "../../src/lib/automations/runtime.js";

export const automationRouter = inngest.createFunction(
  {
    id: "automation-trigger-action-router",
    name: "Automation Trigger and Action Router",
    description: "Routes all supported automation trigger events through condition evaluation and action execution.",
  },
  AUTOMATION_EVENT_NAMES.map((event) => ({ event })),
  async ({ event, step, logger }) => {
    const context = buildRuntimeContext({
      eventName: event.name,
      eventId: event.id,
      eventTs: event.ts,
      data: event.data as AutomationEventData,
    });

    logger.info("Automation event received", {
      triggerType: context.event.triggerType,
      workspaceId: context.workspace.id,
      source: context.event.source,
    });

    const result = await step.run("route-automation-event", async () => routeAutomationEvent(context));

    if (result && typeof result === 'object' && 'loadedAutomations' in result && (result as any).loadedAutomations > 0 && (result as any).matchedAutomations === 0) {
      logger.info("Automation event had no condition matches", {
        triggerType: (result as any).triggerType,
        workspaceId: (result as any).workspaceId,
        loadedAutomations: (result as any).loadedAutomations,
      });
    }

    const failedAutomations = (result as any).results?.filter((automation: any) => automation.status !== "success") || [];
    if (failedAutomations.length > 0) {
      logger.warn("Automation actions completed with failures", {
        failedAutomations: failedAutomations.length,
        matchedAutomations: (result as any).matchedAutomations,
      });
    }

    if (!result.triggerType || !result.workspaceId) {
      throw new NonRetriableError("Malformed automation routing result");
    }

    return result;
  }
);
