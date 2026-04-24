import { afterEach, describe, expect, it, vi } from "vitest";

import { executeSingleAction } from "./executor";
import type { AutomationRuntimeContext } from "./runtime";
import type { AutomationDefinition } from "./validation";

const context: AutomationRuntimeContext = {
  event: {
    name: "automation/note.created",
    triggerType: "note.created",
    source: "internal",
    timestamp: "2026-04-23T00:00:00.000Z",
  },
  workspace: { id: "workspace-1" },
  note: { id: "note-1", title: "Launch Plan" },
  payload: {},
};

const automation: AutomationDefinition = {
  id: "automation-1",
  workspaceId: "workspace-1",
  name: "Webhook automation",
  triggerType: "note.created",
  conditions: [],
  enabled: true,
  actions: [],
};

describe("automation action executor hardening", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("blocks insecure webhook URLs by default", async () => {
    const result = await executeSingleAction(
      { type: "call_webhook", config: { url: "http://example.com/hook", method: "POST", headers: {} } },
      { automation, context }
    );

    expect(result).toMatchObject({
      status: "failed",
      actionType: "call_webhook",
      phase: "execution",
    });
    expect(result.error).toContain("requires https URLs");
  });

  it("blocks localhost and private webhook targets", async () => {
    const result = await executeSingleAction(
      { type: "call_webhook", config: { url: "https://127.0.0.1/hook", method: "POST", headers: {} } },
      { automation, context }
    );

    expect(result.status).toBe("failed");
    expect(result.error).toContain("private network");
  });

  it("allows safe https webhook targets and reports response metadata", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("accepted", { status: 202 })));

    const result = await executeSingleAction(
      { type: "call_webhook", config: { url: "https://hooks.example.com/automation", method: "POST", headers: {} } },
      { automation, context }
    );

    expect(result.status).toBe("success");
    expect(result.output).toEqual({ status: 202, responseBody: "accepted" });
  });
});
