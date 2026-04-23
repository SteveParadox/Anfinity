import { describe, expect, it } from "vitest";

import {
  ACTION_TYPES,
  ACTION_TYPE_IDS,
  SUPPORTED_ACTION_TYPES,
  SUPPORTED_TRIGGER_TYPES,
  TRIGGER_TYPES,
  TRIGGER_TYPE_IDS,
  eventNameForTrigger,
  triggerTypeFromEventName,
} from "./registries";
import { validateAutomationRegistryIntegrity } from "./validation";

describe("automation registries", () => {
  it("defines exactly the supported trigger and action set", () => {
    expect(SUPPORTED_TRIGGER_TYPES).toHaveLength(10);
    expect(SUPPORTED_ACTION_TYPES).toHaveLength(14);
    expect(new Set(TRIGGER_TYPE_IDS).size).toBe(TRIGGER_TYPE_IDS.length);
    expect(new Set(ACTION_TYPE_IDS).size).toBe(ACTION_TYPE_IDS.length);
    expect(() => validateAutomationRegistryIntegrity()).not.toThrow();
  });

  it("keeps trigger keys, ids, and event names aligned", () => {
    for (const triggerType of SUPPORTED_TRIGGER_TYPES) {
      expect(TRIGGER_TYPES[triggerType].id).toBe(triggerType);
      expect(TRIGGER_TYPES[triggerType].eventName).toBe(eventNameForTrigger(triggerType));
      expect(triggerTypeFromEventName(TRIGGER_TYPES[triggerType].eventName)).toBe(triggerType);
    }
  });

  it("keeps action keys and ids aligned", () => {
    for (const actionType of SUPPORTED_ACTION_TYPES) {
      expect(ACTION_TYPES[actionType].id).toBe(actionType);
      expect(ACTION_TYPES[actionType].label).toBeTruthy();
      expect(ACTION_TYPES[actionType].icon).toBeTruthy();
    }
  });
});
