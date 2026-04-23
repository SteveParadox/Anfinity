import { describe, expect, it } from "vitest";

import { evaluateCondition, evaluateConditions, resolvePath, type Condition } from "./conditions";

const context = {
  note: {
    title: "Launch Plan",
    tags: ["release", "urgent"],
    wordCount: 1200,
    metadata: {
      source: "manual",
    },
  },
  workspace: {
    id: "workspace-1",
  },
  payload: {
    score: "42",
    nullable: null,
  },
};

describe("automation condition evaluator", () => {
  it("resolves nested dot-notation paths safely", () => {
    expect(resolvePath(context, "note.title")).toEqual({ found: true, value: "Launch Plan" });
    expect(resolvePath(context, "note.metadata.source")).toEqual({ found: true, value: "manual" });
    expect(resolvePath(context, "note.missing.value")).toEqual({ found: false, value: undefined });
    expect(resolvePath(context, "note..title").found).toBe(false);
    expect(resolvePath(context, "note.__proto__.polluted").found).toBe(false);
    expect(resolvePath(context, "constructor.prototype").found).toBe(false);
  });

  it.each([
    [{ path: "note.title", operator: "equals", value: "Launch Plan" }, true],
    [{ path: "note.title", operator: "not_equals", value: "Other" }, true],
    [{ path: "note.title", operator: "contains", value: "Plan" }, true],
    [{ path: "note.title", operator: "not_contains", value: "Roadmap" }, true],
    [{ path: "note.title", operator: "matches_regex", value: "^Launch" }, true],
    [{ path: "note.wordCount", operator: "greater_than", value: 1000 }, true],
    [{ path: "payload.score", operator: "less_than", value: 100 }, true],
    [{ path: "note.title", operator: "exists" }, true],
  ] satisfies Array<[Condition, boolean]>)("evaluates %j", (condition, expected) => {
    expect(evaluateCondition(condition, context)).toBe(expected);
  });

  it("handles arrays, nulls, missing fields, and invalid regex deterministically", () => {
    expect(evaluateCondition({ path: "note.tags", operator: "contains", value: "urgent" }, context)).toBe(true);
    expect(evaluateCondition({ path: "note.tags", operator: "contains", value: ["release", "urgent"] }, context)).toBe(true);
    expect(evaluateCondition({ path: "payload.nullable", operator: "exists" }, context)).toBe(false);
    expect(evaluateCondition({ path: "note.missing", operator: "not_contains", value: "x" }, context)).toBe(true);
    expect(evaluateCondition({ path: "note.title", operator: "matches_regex", value: "[" }, context)).toBe(false);
    expect(evaluateCondition({ path: "note.title", operator: "matches_regex", value: "^(a+)+$" }, context)).toBe(false);
  });

  it("compares nested objects deterministically regardless of key insertion order", () => {
    expect(
      evaluateCondition(
        { path: "note.metadata", operator: "equals", value: { nested: { b: 2, a: 1 }, source: "manual" } },
        { note: { metadata: { source: "manual", nested: { a: 1, b: 2 } } } }
      )
    ).toBe(true);
  });

  it("supports nested all/any/not groups", () => {
    expect(
      evaluateConditions(
        [
          {
            all: [
              { path: "workspace.id", operator: "equals", value: "workspace-1" },
              {
                any: [
                  { path: "note.tags", operator: "contains", value: "release" },
                  { path: "note.tags", operator: "contains", value: "archive" },
                ],
              },
              { not: { path: "note.title", operator: "contains", value: "Draft" } },
            ],
          },
        ],
        context
      )
    ).toBe(true);
  });
});
