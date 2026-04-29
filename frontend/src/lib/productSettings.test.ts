import { describe, expect, it } from 'vitest';

import {
  approvalPriorityOrDefault,
  buildDefaultApprovalDueDate,
  clampSearchTopK,
  describeIntegrationSyncPolicy,
} from './productSettings';

describe('productSettings helpers', () => {
  it('clamps search top-k into supported range', () => {
    expect(clampSearchTopK(2)).toBe(3);
    expect(clampSearchTopK(7)).toBe(7);
    expect(clampSearchTopK(50)).toBe(12);
  });

  it('builds approval due dates from workspace defaults', () => {
    const dueAt = buildDefaultApprovalDueDate(5, new Date('2026-04-28T10:00:00.000Z'));
    expect(dueAt.getHours()).toBe(23);
    expect(dueAt.getMinutes()).toBe(59);
    expect(dueAt.getSeconds()).toBe(59);
    expect(dueAt.getDate()).toBe(3);
  });

  it('describes integration sync policy and approval fallback defaults', () => {
    expect(
      describeIntegrationSyncPolicy({
        ai_search: { ask_past_self_enabled: true, min_note_similarity: 0.55, source_cards_default: true },
        notes: {
          default_visibility: 'private',
          auto_tagging_enabled: true,
          summary_generation_enabled: true,
          connection_suggestions_enabled: true,
          decay_classification_enabled: true,
        },
        collaboration: {
          comment_threads_enabled: true,
          mentions_enabled: true,
          invite_policy: 'members',
        },
        integrations: {
          auto_sync_enabled: false,
          sync_frequency: 'daily',
        },
        automations: {
          enabled: true,
          notify_on_failure: true,
        },
        approvals: {
          enabled: true,
          default_priority: 'high',
          default_due_days: 7,
        },
      }),
    ).toContain('Manual sync still works');

    expect(
      approvalPriorityOrDefault(undefined, {
        ai_search: { ask_past_self_enabled: true, min_note_similarity: 0.55, source_cards_default: true },
        notes: {
          default_visibility: 'private',
          auto_tagging_enabled: true,
          summary_generation_enabled: true,
          connection_suggestions_enabled: true,
          decay_classification_enabled: true,
        },
        collaboration: {
          comment_threads_enabled: true,
          mentions_enabled: true,
          invite_policy: 'members',
        },
        integrations: {
          auto_sync_enabled: true,
          sync_frequency: 'hourly',
        },
        automations: {
          enabled: true,
          notify_on_failure: true,
        },
        approvals: {
          enabled: true,
          default_priority: 'high',
          default_due_days: 7,
        },
      }),
    ).toBe('high');
  });
});
