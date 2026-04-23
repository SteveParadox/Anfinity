import { describe, expect, it } from "vitest";

import { interpolateConfig, interpolateTemplate } from "./templates";

describe("automation template interpolation", () => {
  const context = {
    note: {
      title: "Decision log",
      tags: ["strategy", "q2"],
    },
    workspace: {
      id: "workspace-1",
    },
    user: {
      email: "ada@example.com",
    },
  };

  it("resolves nested placeholders without evaluating expressions", () => {
    expect(interpolateTemplate("{{note.title}} in {{workspace.id}}", context)).toBe("Decision log in workspace-1");
    expect(interpolateTemplate("Owner: {{user.email.toLowerCase()}}", context)).toBe("Owner: ");
  });

  it("uses an empty string for unknown or null variables", () => {
    expect(interpolateTemplate("Missing={{note.missing}}", context)).toBe("Missing=");
    expect(interpolateTemplate("Unsafe={{constructor.prototype}}", context)).toBe("Unsafe=");
  });

  it("interpolates strings recursively in config objects", () => {
    expect(
      interpolateConfig(
        {
          title: "{{note.title}}",
          tags: ["{{workspace.id}}", "static"],
          nested: {
            body: "Tags: {{note.tags}}",
          },
        },
        context
      )
    ).toEqual({
      title: "Decision log",
      tags: ["workspace-1", "static"],
      nested: {
        body: 'Tags: ["strategy","q2"]',
      },
    });
  });
});
