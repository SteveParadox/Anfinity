import type { ProductWorkspaceSettings } from './api';
import type { ApprovalWorkflowPriority } from '@/types';

export function clampSearchTopK(value: unknown, fallback = 6): number {
  const numeric = typeof value === 'number' && Number.isFinite(value) ? value : fallback;
  return Math.max(3, Math.min(12, Math.round(numeric)));
}

export function buildDefaultApprovalDueDate(defaultDueDays: unknown, now: Date = new Date()): Date {
  const parsed = typeof defaultDueDays === 'number' && Number.isFinite(defaultDueDays)
    ? Math.round(defaultDueDays)
    : 5;
  const days = Math.max(1, Math.min(30, parsed));
  const dueAt = new Date(now);
  dueAt.setDate(dueAt.getDate() + days);
  dueAt.setHours(23, 59, 59, 0);
  return dueAt;
}

export function approvalPriorityOrDefault(
  priority: ApprovalWorkflowPriority | undefined,
  workspaceSettings?: ProductWorkspaceSettings | null,
): ApprovalWorkflowPriority {
  return priority || workspaceSettings?.approvals.default_priority || 'normal';
}

export function describeIntegrationSyncPolicy(workspaceSettings?: ProductWorkspaceSettings | null): string {
  const integrations = workspaceSettings?.integrations;
  if (!integrations) {
    return 'Scheduled sync policy unavailable.';
  }

  if (!integrations.auto_sync_enabled) {
    return 'Automatic sync is paused for this workspace. Manual sync still works.';
  }

  if (integrations.sync_frequency === 'manual') {
    return 'Automatic sync is enabled, but cadence is set to manual-only right now.';
  }

  return `Automatic sync runs ${integrations.sync_frequency} for this workspace.`;
}
