import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import {
  ACTION_EXECUTION,
  ACTION_TYPE_IDS,
  APPROVAL_PRIORITY_IDS,
  CONDITION_OPERATOR_IDS,
  HTTP_METHOD_IDS,
  NOTE_TYPE_IDS,
  TRIGGER_TYPE_IDS,
} from "./generatedRegistry";

interface RegistryManifest {
  triggerTypes: string[];
  actionTypes: string[];
  conditionOperators: string[];
  actionExecution: Record<string, "backend" | "http">;
  noteTypes: string[];
  approvalPriorities: string[];
  httpMethods: string[];
}

describe("generated automation registry", () => {
  it("matches the shared manifest", () => {
    const manifest = JSON.parse(
      readFileSync(resolve(process.cwd(), "..", "automation-registry.manifest.json"), "utf-8")
    ) as RegistryManifest;

    expect(TRIGGER_TYPE_IDS).toEqual(manifest.triggerTypes);
    expect(ACTION_TYPE_IDS).toEqual(manifest.actionTypes);
    expect(CONDITION_OPERATOR_IDS).toEqual(manifest.conditionOperators);
    expect(ACTION_EXECUTION).toEqual(manifest.actionExecution);
    expect(NOTE_TYPE_IDS).toEqual(manifest.noteTypes);
    expect(APPROVAL_PRIORITY_IDS).toEqual(manifest.approvalPriorities);
    expect(HTTP_METHOD_IDS).toEqual(manifest.httpMethods);
  });
});
