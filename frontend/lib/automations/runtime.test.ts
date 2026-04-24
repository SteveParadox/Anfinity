import { beforeEach, describe, expect, it, vi } from "vitest";

import { buildRuntimeContext, routeAutomationEvent, shouldRunAutomation } from "./runtime";
import type { AutomationDefinition } from "./validation";

vi.mock("./backend", () => ({
  fetchEnabledAutomations: vi.fn(),
  executeBackendAction: vi.fn(async () => ({ ok: true })),
}));

import { fetchEnabledAutomations } from "./backend";

const baseAutomation: AutomationDefinition = {
  id: "automation-1",
  workspaceId: "workspace-1",
  name: "Notify launches",
  triggerType: "note.created",
  enabled: true,
  conditions: [{ path: "note.title", operator: "contains", value: "Launch" }],
  actions: [
    {
      id: "action-1",
      type: "send_notification",
      config: {
        recipientUserIds: ["user-1"],
        title: "{{note.title}}",
        message: "Created",
      },
    },
  ],
};

describe("automation runtime routing", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("builds runtime context from an Inngest event", () => {
    const context = buildRuntimeContext({
      eventName: "automation/note.created",
      eventId: "event-1",
      eventTs: Date.parse("2026-04-23T00:00:00Z"),
      data: {
        workspace: { id: "workspace-1", slug: "acme" },
        note: { id: "note-1", title: "Launch Plan" },
        payload: { metadata: { source: "manual" } },
      },
    });

    expect(context.event.triggerType).toBe("note.created");
    expect(context.workspace.slug).toBe("acme");
    expect(context.note?.title).toBe("Launch Plan");
  });

  it("skips disabled and non-matching automations", () => {
    const context = buildRuntimeContext({
      eventName: "automation/note.created",
      data: {
        workspace: { id: "workspace-1" },
        note: { title: "Personal note" },
      },
    });

    expect(shouldRunAutomation(baseAutomation, context)).toBe(false);
    expect(shouldRunAutomation({ ...baseAutomation, enabled: false }, context)).toBe(false);
  });

  it("routes multiple matching automations and preserves partial action failure semantics", async () => {
    vi.mocked(fetchEnabledAutomations).mockResolvedValue([
      baseAutomation,
      {
        ...baseAutomation,
        id: "automation-2",
        actions: [{ type: "call_webhook", config: { url: "not-a-url", method: "POST", headers: {} } }],
      },
    ]);

    const context = buildRuntimeContext({
      eventName: "automation/note.created",
      data: {
        workspace: { id: "workspace-1" },
        note: { id: "note-1", title: "Launch Plan" },
      },
    });

    const result = await routeAutomationEvent(context);

    expect(result.loadedAutomations).toBe(2);
    expect(result.matchedAutomations).toBe(2);
    expect(result.executedAutomations).toBe(2);
    expect(result.invalidAutomations).toBe(0);
    expect(result.results[0]?.executionPolicy).toBe("sequential_continue_on_error");
    expect(result.results[0]?.status).toBe("success");
    expect(result.results[1]?.status).toBe("failed");
  });

  it("reports malformed automations as validation failures without blocking valid matches", async () => {
    vi.mocked(fetchEnabledAutomations).mockResolvedValue([
      {
        ...baseAutomation,
        id: "automation-bad",
        conditions: [{ path: "note.__proto__.polluted", operator: "equals", value: true }],
      },
      baseAutomation,
    ]);

    const context = buildRuntimeContext({
      eventName: "automation/note.created",
      data: {
        workspace: { id: "workspace-1" },
        note: { id: "note-1", title: "Launch Plan" },
      },
    });

    const result = await routeAutomationEvent(context);

    expect(result.loadedAutomations).toBe(2);
    expect(result.invalidAutomations).toBe(1);
    expect(result.matchedAutomations).toBe(1);
    expect(result.executedAutomations).toBe(1);
    expect(result.results[0]?.actionResults[0]).toMatchObject({ phase: "validation", status: "failed" });
    expect(result.results[1]?.status).toBe("success");
  });
});
