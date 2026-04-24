import { describe, expect, it } from "vitest";

import { parseAutomationDefinition, validateActionConfig } from "./validation";

describe("automation config validation", () => {
  it("rejects unsupported trigger and action types", () => {
    expect(() =>
      parseAutomationDefinition({
        id: "automation-1",
        workspace_id: "workspace-1",
        name: "Bad trigger",
        trigger_type: "missing.trigger",
        actions: [{ type: "send_notification", config: { recipientUserIds: ["user-1"], title: "Hi", message: "Body" } }],
      })
    ).toThrow(/Unsupported trigger/);

    expect(() =>
      parseAutomationDefinition({
        id: "automation-1",
        workspace_id: "workspace-1",
        name: "Bad action",
        trigger_type: "note.created",
        actions: [{ type: "unknown", config: {} }],
      })
    ).toThrow();
  });

  it("validates action config by action type", () => {
    expect(validateActionConfig("create_note", { title: "T", content: "C" })).toMatchObject({
      title: "T",
      content: "C",
      noteType: "note",
      tags: [],
    });
    expect(() => validateActionConfig("reject_note", { noteId: "note-1" })).toThrow();
  });

  it("parses a valid automation definition", () => {
    const automation = parseAutomationDefinition({
      id: "automation-1",
      workspace_id: "workspace-1",
      name: "Notify on new note",
      trigger_type: "note.created",
      conditions: [{ path: "note.title", operator: "contains", value: "Launch" }],
      actions: [
        {
          id: "action-1",
          type: "send_notification",
          config: {
            recipientUserIds: ["user-1"],
            title: "{{note.title}}",
            message: "Created in {{workspace.id}}",
          },
        },
      ],
    });

    expect(automation.triggerType).toBe("note.created");
    expect(automation.actions[0]?.type).toBe("send_notification");
  });
});
