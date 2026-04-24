import { z } from "zod";

import {
  ACTION_EXECUTION,
  ACTION_TYPE_IDS,
  APPROVAL_PRIORITY_IDS,
  HTTP_METHOD_IDS,
  NOTE_TYPE_IDS,
  TRIGGER_TYPE_IDS,
} from "./generatedRegistry";

export {
  ACTION_EXECUTION,
  ACTION_TYPE_IDS,
  APPROVAL_PRIORITY_IDS,
  HTTP_METHOD_IDS,
  NOTE_TYPE_IDS,
  TRIGGER_TYPE_IDS,
} from "./generatedRegistry";

export type AutomationTriggerType = (typeof TRIGGER_TYPE_IDS)[number];
export type AutomationActionType = (typeof ACTION_TYPE_IDS)[number];

export type AutomationIconName =
  | "Bell"
  | "BookOpen"
  | "CheckCircle2"
  | "Clock3"
  | "Code2"
  | "FileCheck2"
  | "FileText"
  | "GitBranch"
  | "Globe2"
  | "Link2"
  | "Mail"
  | "MessageSquareWarning"
  | "PenLine"
  | "PlusCircle"
  | "RefreshCw"
  | "Send"
  | "ShieldCheck"
  | "Tag"
  | "Trash2"
  | "Webhook"
  | "XCircle";

export type ConfigFieldKind =
  | "string"
  | "text"
  | "number"
  | "boolean"
  | "string_array"
  | "select"
  | "json"
  | "url"
  | "template";

export interface ConfigFieldDefinition {
  key: string;
  label: string;
  kind: ConfigFieldKind;
  required?: boolean;
  description?: string;
  options?: Array<{ label: string; value: string }>;
  defaultValue?: unknown;
  templateEnabled?: boolean;
}

export interface TriggerTypeDefinition {
  id: AutomationTriggerType;
  eventName: `automation/${AutomationTriggerType}`;
  label: string;
  description: string;
  icon: AutomationIconName;
  payloadFields: ConfigFieldDefinition[];
}

export interface ActionTypeDefinition {
  id: AutomationActionType;
  label: string;
  description: string;
  icon: AutomationIconName;
  configFields: ConfigFieldDefinition[];
  execution: "backend" | "http";
}

function defineTriggerRegistry<T extends Record<AutomationTriggerType, TriggerTypeDefinition>>(registry: T): T {
  return registry;
}

function defineActionRegistry<T extends Record<AutomationActionType, ActionTypeDefinition>>(registry: T): T {
  return registry;
}

export function eventNameForTrigger(triggerType: AutomationTriggerType): `automation/${AutomationTriggerType}` {
  return `automation/${triggerType}`;
}

export const TRIGGER_TYPES = defineTriggerRegistry({
  "note.created": {
    id: "note.created",
    eventName: "automation/note.created",
    label: "Note Created",
    description: "Runs when a workspace note is created.",
    icon: "PlusCircle",
    payloadFields: [
      { key: "workspace.id", label: "Workspace ID", kind: "string", required: true },
      { key: "note.id", label: "Note ID", kind: "string", required: true },
      { key: "note.title", label: "Note title", kind: "string" },
      { key: "author.email", label: "Author email", kind: "string" },
    ],
  },
  "note.updated": {
    id: "note.updated",
    eventName: "automation/note.updated",
    label: "Note Updated",
    description: "Runs when a workspace note changes.",
    icon: "PenLine",
    payloadFields: [
      { key: "workspace.id", label: "Workspace ID", kind: "string", required: true },
      { key: "note.id", label: "Note ID", kind: "string", required: true },
      { key: "payload.changedFields", label: "Changed fields", kind: "string_array" },
    ],
  },
  "note.deleted": {
    id: "note.deleted",
    eventName: "automation/note.deleted",
    label: "Note Deleted",
    description: "Runs when a workspace note is deleted.",
    icon: "Trash2",
    payloadFields: [
      { key: "workspace.id", label: "Workspace ID", kind: "string", required: true },
      { key: "note.id", label: "Note ID", kind: "string", required: true },
    ],
  },
  "note.approval_submitted": {
    id: "note.approval_submitted",
    eventName: "automation/note.approval_submitted",
    label: "Approval Submitted",
    description: "Runs when a note is submitted for approval.",
    icon: "Send",
    payloadFields: [
      { key: "workspace.id", label: "Workspace ID", kind: "string", required: true },
      { key: "note.approvalStatus", label: "Approval status", kind: "string" },
    ],
  },
  "note.approval_approved": {
    id: "note.approval_approved",
    eventName: "automation/note.approval_approved",
    label: "Approval Approved",
    description: "Runs when a submitted note is approved.",
    icon: "CheckCircle2",
    payloadFields: [
      { key: "workspace.id", label: "Workspace ID", kind: "string", required: true },
      { key: "note.id", label: "Note ID", kind: "string", required: true },
    ],
  },
  "note.approval_rejected": {
    id: "note.approval_rejected",
    eventName: "automation/note.approval_rejected",
    label: "Approval Rejected",
    description: "Runs when a submitted note is rejected.",
    icon: "XCircle",
    payloadFields: [
      { key: "workspace.id", label: "Workspace ID", kind: "string", required: true },
      { key: "payload.comment", label: "Decision comment", kind: "text" },
    ],
  },
  "note.approval_needs_changes": {
    id: "note.approval_needs_changes",
    eventName: "automation/note.approval_needs_changes",
    label: "Changes Requested",
    description: "Runs when a reviewer requests note changes.",
    icon: "MessageSquareWarning",
    payloadFields: [
      { key: "workspace.id", label: "Workspace ID", kind: "string", required: true },
      { key: "payload.comment", label: "Review comment", kind: "text" },
    ],
  },
  "document.completed": {
    id: "document.completed",
    eventName: "automation/document.completed",
    label: "Document Processed",
    description: "Runs after document ingestion and indexing completes.",
    icon: "FileCheck2",
    payloadFields: [
      { key: "workspace.id", label: "Workspace ID", kind: "string", required: true },
      { key: "document.id", label: "Document ID", kind: "string", required: true },
      { key: "document.title", label: "Document title", kind: "string" },
    ],
  },
  "thinking_session.completed": {
    id: "thinking_session.completed",
    eventName: "automation/thinking_session.completed",
    label: "Thinking Session Completed",
    description: "Runs when a live thinking session is completed.",
    icon: "GitBranch",
    payloadFields: [
      { key: "workspace.id", label: "Workspace ID", kind: "string", required: true },
      { key: "thinkingSession.id", label: "Session ID", kind: "string", required: true },
    ],
  },
  "webhook.received": {
    id: "webhook.received",
    eventName: "automation/webhook.received",
    label: "Webhook Received",
    description: "Runs when the workspace webhook endpoint receives a signed external event.",
    icon: "Webhook",
    payloadFields: [
      { key: "workspace.id", label: "Workspace ID", kind: "string", required: true },
      { key: "payload", label: "External payload", kind: "json", required: true },
      { key: "payload.metadata.source", label: "Payload source", kind: "string" },
    ],
  },
  "competitive_intelligence.urgent_finding": {
    id: "competitive_intelligence.urgent_finding",
    eventName: "automation/competitive_intelligence.urgent_finding",
    label: "Urgent Competitive Finding",
    description: "Runs immediately when competitive intelligence detects a high-urgency page change.",
    icon: "Globe2",
    payloadFields: [
      { key: "workspace.id", label: "Workspace ID", kind: "string", required: true },
      { key: "competitive.source.id", label: "Source ID", kind: "string", required: true },
      { key: "competitive.source.name", label: "Source name", kind: "string" },
      { key: "competitive.source.url", label: "Source URL", kind: "url" },
      { key: "competitive.analysis.overall_urgency", label: "Overall urgency", kind: "number" },
      { key: "competitive.analysis.urgency_label", label: "Urgency label", kind: "string" },
      { key: "competitive.analysis.findings", label: "Findings", kind: "json" },
    ],
  },
});

export const ACTION_TYPES = defineActionRegistry({
  send_notification: {
    id: "send_notification",
    label: "Send Notification",
    description: "Create durable in-app notifications for workspace users.",
    icon: "Bell",
    execution: ACTION_EXECUTION.send_notification,
    configFields: [
      { key: "recipientUserIds", label: "Recipient user IDs", kind: "string_array", required: true, templateEnabled: true },
      { key: "title", label: "Title", kind: "template", required: true },
      { key: "message", label: "Message", kind: "template", required: true },
    ],
  },
  create_note: {
    id: "create_note",
    label: "Create Note",
    description: "Create a new note in the triggering workspace.",
    icon: "BookOpen",
    execution: ACTION_EXECUTION.create_note,
    configFields: [
      { key: "title", label: "Title", kind: "template", required: true },
      { key: "content", label: "Content", kind: "template", required: true },
      { key: "tags", label: "Tags", kind: "string_array", templateEnabled: true },
      { key: "noteType", label: "Note type", kind: "select", options: noteTypeOptions(), defaultValue: "note" },
    ],
  },
  update_note: {
    id: "update_note",
    label: "Update Note",
    description: "Update title, content, tags, or type on a note.",
    icon: "PenLine",
    execution: ACTION_EXECUTION.update_note,
    configFields: [
      { key: "noteId", label: "Note ID", kind: "template", required: true },
      { key: "title", label: "Title", kind: "template" },
      { key: "content", label: "Content", kind: "template" },
      { key: "tags", label: "Tags", kind: "string_array", templateEnabled: true },
      { key: "noteType", label: "Note type", kind: "select", options: noteTypeOptions() },
    ],
  },
  append_note_content: {
    id: "append_note_content",
    label: "Append Note Content",
    description: "Append templated text to an existing note.",
    icon: "FileText",
    execution: ACTION_EXECUTION.append_note_content,
    configFields: [
      { key: "noteId", label: "Note ID", kind: "template", required: true },
      { key: "content", label: "Content to append", kind: "template", required: true },
    ],
  },
  add_note_tags: {
    id: "add_note_tags",
    label: "Add Note Tags",
    description: "Add one or more tags to a note.",
    icon: "Tag",
    execution: ACTION_EXECUTION.add_note_tags,
    configFields: [
      { key: "noteId", label: "Note ID", kind: "template", required: true },
      { key: "tags", label: "Tags", kind: "string_array", required: true, templateEnabled: true },
    ],
  },
  remove_note_tags: {
    id: "remove_note_tags",
    label: "Remove Note Tags",
    description: "Remove one or more tags from a note.",
    icon: "Tag",
    execution: ACTION_EXECUTION.remove_note_tags,
    configFields: [
      { key: "noteId", label: "Note ID", kind: "template", required: true },
      { key: "tags", label: "Tags", kind: "string_array", required: true, templateEnabled: true },
    ],
  },
  set_note_type: {
    id: "set_note_type",
    label: "Set Note Type",
    description: "Change a note type to one of the supported note categories.",
    icon: "Code2",
    execution: ACTION_EXECUTION.set_note_type,
    configFields: [
      { key: "noteId", label: "Note ID", kind: "template", required: true },
      { key: "noteType", label: "Note type", kind: "select", required: true, options: noteTypeOptions() },
    ],
  },
  link_notes: {
    id: "link_notes",
    label: "Link Notes",
    description: "Create a bidirectional note connection.",
    icon: "Link2",
    execution: ACTION_EXECUTION.link_notes,
    configFields: [
      { key: "sourceNoteId", label: "Source note ID", kind: "template", required: true },
      { key: "targetNoteId", label: "Target note ID", kind: "template", required: true },
    ],
  },
  submit_for_approval: {
    id: "submit_for_approval",
    label: "Submit For Approval",
    description: "Move a draft or change-requested note into review.",
    icon: "Send",
    execution: ACTION_EXECUTION.submit_for_approval,
    configFields: [
      { key: "noteId", label: "Note ID", kind: "template", required: true },
      { key: "priority", label: "Priority", kind: "select", options: approvalPriorityOptions(), defaultValue: "normal" },
      { key: "comment", label: "Comment", kind: "template" },
    ],
  },
  approve_note: {
    id: "approve_note",
    label: "Approve Note",
    description: "Approve a submitted note.",
    icon: "ShieldCheck",
    execution: ACTION_EXECUTION.approve_note,
    configFields: [
      { key: "noteId", label: "Note ID", kind: "template", required: true },
      { key: "comment", label: "Comment", kind: "template" },
    ],
  },
  reject_note: {
    id: "reject_note",
    label: "Reject Note",
    description: "Reject a submitted note with a required reason.",
    icon: "XCircle",
    execution: ACTION_EXECUTION.reject_note,
    configFields: [
      { key: "noteId", label: "Note ID", kind: "template", required: true },
      { key: "comment", label: "Reason", kind: "template", required: true },
    ],
  },
  request_approval_changes: {
    id: "request_approval_changes",
    label: "Request Changes",
    description: "Request changes on a submitted note.",
    icon: "MessageSquareWarning",
    execution: ACTION_EXECUTION.request_approval_changes,
    configFields: [
      { key: "noteId", label: "Note ID", kind: "template", required: true },
      { key: "comment", label: "Requested changes", kind: "template", required: true },
    ],
  },
  call_webhook: {
    id: "call_webhook",
    label: "Call Webhook",
    description: "Send a templated HTTP request to an external service.",
    icon: "Globe2",
    execution: ACTION_EXECUTION.call_webhook,
    configFields: [
      { key: "url", label: "URL", kind: "template", required: true },
      { key: "method", label: "Method", kind: "select", required: true, options: httpMethodOptions(), defaultValue: "POST" },
      { key: "headers", label: "Headers", kind: "json" },
      { key: "body", label: "JSON body", kind: "template" },
    ],
  },
  send_email: {
    id: "send_email",
    label: "Send Email",
    description: "Send an email through the configured automation email webhook.",
    icon: "Mail",
    execution: ACTION_EXECUTION.send_email,
    configFields: [
      { key: "to", label: "Recipients", kind: "string_array", required: true, templateEnabled: true },
      { key: "subject", label: "Subject", kind: "template", required: true },
      { key: "body", label: "Body", kind: "template", required: true },
    ],
  },
});

function noteTypeOptions() {
  return NOTE_TYPE_IDS.map((value) => ({ label: humanizeRegistryValue(value), value }));
}

function approvalPriorityOptions() {
  return APPROVAL_PRIORITY_IDS.map((value) => ({ label: humanizeRegistryValue(value), value }));
}

function httpMethodOptions() {
  return HTTP_METHOD_IDS.map((value) => ({ label: value, value }));
}

export const SUPPORTED_TRIGGER_TYPES = [...TRIGGER_TYPE_IDS];
export const SUPPORTED_ACTION_TYPES = [...ACTION_TYPE_IDS];
export const AUTOMATION_EVENT_NAMES = SUPPORTED_TRIGGER_TYPES.map(eventNameForTrigger);

export function isAutomationTriggerType(value: unknown): value is AutomationTriggerType {
  return typeof value === "string" && value in TRIGGER_TYPES;
}

export function isAutomationActionType(value: unknown): value is AutomationActionType {
  return typeof value === "string" && value in ACTION_TYPES;
}

export function triggerTypeFromEventName(eventName: string): AutomationTriggerType | null {
  if (!eventName.startsWith("automation/")) {
    return null;
  }
  const triggerType = eventName.slice("automation/".length);
  return isAutomationTriggerType(triggerType) ? triggerType : null;
}

export const noteTypeSchema = z.enum(NOTE_TYPE_IDS);
export const approvalPrioritySchema = z.enum(APPROVAL_PRIORITY_IDS);
export const httpMethodSchema = z.enum(HTTP_METHOD_IDS);

function humanizeRegistryValue(value: string): string {
  return value
    .split(/[-_]/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}
