import { z } from "zod";

import {
  ACTION_TYPES,
  SUPPORTED_ACTION_TYPES,
  SUPPORTED_TRIGGER_TYPES,
  approvalPrioritySchema,
  httpMethodSchema,
  isAutomationActionType,
  isAutomationTriggerType,
  noteTypeSchema,
  type AutomationActionType,
  type AutomationTriggerType,
} from "./registries";
import { conditionSchema, fieldPathSchema, type Condition } from "./conditions";

const nonEmptyTemplateSchema = z.string().trim().min(1);
const optionalTemplateSchema = z.string().optional();
const stringArraySchema = z.array(z.string().trim().min(1)).min(1);
const optionalStringArraySchema = z.array(z.string().trim().min(1)).optional();

const actionBaseSchema = z.object({
  id: z.string().trim().min(1).max(120).optional(),
  type: z.string().refine(isAutomationActionType, "Unsupported action type"),
  config: z.record(z.string(), z.unknown()).default({}),
});

export const actionConfigSchemas = {
  send_notification: z.object({
    recipientUserIds: stringArraySchema,
    title: nonEmptyTemplateSchema,
    message: nonEmptyTemplateSchema,
  }),
  create_note: z.object({
    title: nonEmptyTemplateSchema,
    content: nonEmptyTemplateSchema,
    tags: optionalStringArraySchema.default([]),
    noteType: noteTypeSchema.default("note"),
  }),
  update_note: z.object({
    noteId: nonEmptyTemplateSchema,
    title: optionalTemplateSchema,
    content: optionalTemplateSchema,
    tags: optionalStringArraySchema,
    noteType: noteTypeSchema.optional(),
  }),
  append_note_content: z.object({
    noteId: nonEmptyTemplateSchema,
    content: nonEmptyTemplateSchema,
  }),
  add_note_tags: z.object({
    noteId: nonEmptyTemplateSchema,
    tags: stringArraySchema,
  }),
  remove_note_tags: z.object({
    noteId: nonEmptyTemplateSchema,
    tags: stringArraySchema,
  }),
  set_note_type: z.object({
    noteId: nonEmptyTemplateSchema,
    noteType: noteTypeSchema,
  }),
  link_notes: z.object({
    sourceNoteId: nonEmptyTemplateSchema,
    targetNoteId: nonEmptyTemplateSchema,
  }),
  submit_for_approval: z.object({
    noteId: nonEmptyTemplateSchema,
    priority: approvalPrioritySchema.default("normal"),
    comment: optionalTemplateSchema,
  }),
  approve_note: z.object({
    noteId: nonEmptyTemplateSchema,
    comment: optionalTemplateSchema,
  }),
  reject_note: z.object({
    noteId: nonEmptyTemplateSchema,
    comment: nonEmptyTemplateSchema,
  }),
  request_approval_changes: z.object({
    noteId: nonEmptyTemplateSchema,
    comment: nonEmptyTemplateSchema,
  }),
  call_webhook: z.object({
    url: nonEmptyTemplateSchema,
    method: httpMethodSchema.default("POST"),
    headers: z.record(z.string(), z.string()).default({}),
    body: z.union([z.string(), z.record(z.string(), z.unknown()), z.array(z.unknown())]).optional(),
  }),
  send_email: z.object({
    to: stringArraySchema,
    subject: nonEmptyTemplateSchema,
    body: nonEmptyTemplateSchema,
  }),
} satisfies Record<AutomationActionType, z.ZodTypeAny>;

export type AutomationActionConfig<T extends AutomationActionType = AutomationActionType> = z.infer<
  (typeof actionConfigSchemas)[T]
>;

export interface AutomationAction<T extends AutomationActionType = AutomationActionType> {
  id?: string;
  type: T;
  config: AutomationActionConfig<T>;
}

export interface AutomationDefinition {
  id: string;
  workspaceId: string;
  name: string;
  triggerType: AutomationTriggerType;
  conditions: Condition[];
  actions: AutomationAction[];
  enabled: boolean;
  createdAt?: string;
  updatedAt?: string | null;
}

export const automationInputSchema = z.object({
  id: z.string().trim().min(1).max(120).optional(),
  workspaceId: z.string().trim().min(1).optional(),
  workspace_id: z.string().trim().min(1).optional(),
  name: z.string().trim().min(1).max(255),
  triggerType: z.string().optional(),
  trigger_type: z.string().optional(),
  conditions: z.array(conditionSchema).default([]),
  actions: z.array(actionBaseSchema).min(1),
  enabled: z.boolean().default(true),
  createdAt: z.string().optional(),
  created_at: z.string().optional(),
  updatedAt: z.string().nullable().optional(),
  updated_at: z.string().nullable().optional(),
});

export function parseAutomationDefinition(input: unknown): AutomationDefinition {
  const parsed = automationInputSchema.parse(input);
  const triggerType = parsed.triggerType ?? parsed.trigger_type;
  const workspaceId = parsed.workspaceId ?? parsed.workspace_id;

  if (!isAutomationTriggerType(triggerType)) {
    throw new Error(`Unsupported trigger type: ${String(triggerType)}`);
  }

  if (!workspaceId) {
    throw new Error("Automation workspaceId is required");
  }

  const actions = parsed.actions.map((action, index) => parseAutomationAction(action, index));

  return {
    id: parsed.id ?? `automation-${Date.now()}`,
    workspaceId,
    name: parsed.name,
    triggerType,
    conditions: parsed.conditions,
    actions,
    enabled: parsed.enabled,
    createdAt: parsed.createdAt ?? parsed.created_at,
    updatedAt: parsed.updatedAt ?? parsed.updated_at ?? null,
  };
}

export function parseAutomationAction(input: unknown, index = 0): AutomationAction {
  const parsed = actionBaseSchema.parse(input);
  if (!isAutomationActionType(parsed.type)) {
    throw new Error(`Unsupported action type at index ${index}: ${parsed.type}`);
  }

  const config = validateActionConfig(parsed.type, parsed.config);
  return {
    id: parsed.id,
    type: parsed.type,
    config,
  };
}

export function validateActionConfig<T extends AutomationActionType>(
  type: T,
  config: unknown
): AutomationActionConfig<T> {
  const schema = actionConfigSchemas[type];
  if (!schema) {
    throw new Error(`No config schema registered for action type ${type}`);
  }
  return schema.parse(config) as AutomationActionConfig<T>;
}

export function validateAutomationRegistryIntegrity(): void {
  const triggerIds = new Set(SUPPORTED_TRIGGER_TYPES);
  if (triggerIds.size !== 10) {
    throw new Error(`Expected exactly 10 trigger types, found ${triggerIds.size}`);
  }

  const actionIds = new Set(SUPPORTED_ACTION_TYPES);
  if (actionIds.size !== 14) {
    throw new Error(`Expected exactly 14 action types, found ${actionIds.size}`);
  }

  for (const actionType of SUPPORTED_ACTION_TYPES) {
    if (!ACTION_TYPES[actionType]) {
      throw new Error(`Missing action registry entry for ${actionType}`);
    }
    if (!actionConfigSchemas[actionType]) {
      throw new Error(`Missing action config schema for ${actionType}`);
    }
  }
}

export function validateConditionFieldPaths(conditions: Condition[]): void {
  for (const condition of conditions) {
    if ("path" in condition) {
      fieldPathSchema.parse(condition.path);
      continue;
    }

    validateConditionFieldPaths(condition.all ?? []);
    validateConditionFieldPaths(condition.any ?? []);
    if (condition.not) {
      validateConditionFieldPaths([condition.not]);
    }
  }
}
