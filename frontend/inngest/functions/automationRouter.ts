import { NonRetriableError } from "inngest";

import { inngest } from "../client";
import { AUTOMATION_EVENT_NAMES } from "@/lib/automations/registries";
import { buildRuntimeContext, routeAutomationEvent, type AutomationEventData } from "@/lib/automations/runtime";

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

    if (result.loadedAutomations > 0 && result.matchedAutomations === 0) {
      logger.info("Automation event had no condition matches", {
        triggerType: result.triggerType,
        workspaceId: result.workspaceId,
        loadedAutomations: result.loadedAutomations,
      });
    }

    const failedAutomations = result.results.filter((automation) => automation.status !== "success");
    if (failedAutomations.length > 0) {
      logger.warn("Automation actions completed with failures", {
        failedAutomations: failedAutomations.length,
        matchedAutomations: result.matchedAutomations,
      });
    }

    if (!result.triggerType || !result.workspaceId) {
      throw new NonRetriableError("Malformed automation routing result");
    }

    return result;
  }
);
