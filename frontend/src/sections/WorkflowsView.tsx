import { useCallback, useContext, useEffect, useMemo, useState, type ComponentType } from 'react';
import { format, formatDistanceToNow } from 'date-fns';
import {
  AlertTriangle,
  CheckCircle2,
  CircleDot,
  Clock3,
  Loader2,
  RefreshCw,
  Send,
  ShieldCheck,
  XCircle,
} from 'lucide-react';

import { ApprovalPriorityBadge } from '@/components/approvals/ApprovalPriorityBadge';
import { ApprovalReviewDialog } from '@/components/approvals/ApprovalReviewDialog';
import { ApprovalStatusBadge } from '@/components/approvals/ApprovalStatusBadge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { AuthContext } from '@/contexts/AuthContext';
import { api } from '@/lib/api';
import type {
  ApprovalWorkflowItem,
  ApprovalWorkflowPriority,
  ApprovalWorkflowStatus,
  ApprovalWorkflowSummary,
} from '@/types';


type TabValue = 'all' | ApprovalWorkflowStatus;
type DialogMode = 'submit' | 'resubmit' | 'reject' | 'request_changes';

const TAB_ORDER: TabValue[] = [
  'all',
  'submitted',
  'needs_changes',
  'draft',
  'approved',
  'rejected',
  'cancelled',
];

const TAB_LABELS: Record<TabValue, string> = {
  all: 'All',
  draft: 'Draft',
  submitted: 'Submitted',
  needs_changes: 'Needs Changes',
  approved: 'Approved',
  rejected: 'Rejected',
  cancelled: 'Cancelled',
};


function buildEmptySummary(): ApprovalWorkflowSummary {
  return {
    countsByStatus: {
      draft: 0,
      submitted: 0,
      needs_changes: 0,
      approved: 0,
      rejected: 0,
      cancelled: 0,
    },
    total: 0,
    overdue: 0,
  };
}

function formatDueDate(value?: Date, overdue?: boolean): string {
  if (!value) return 'No due date';
  const label = format(value, 'MMM d, yyyy');
  return overdue ? `${label} - overdue` : label;
}

function formatRelativeDate(value?: Date): string {
  if (!value) return 'Not set';
  return formatDistanceToNow(value, { addSuffix: true });
}

function DashboardStat({
  label,
  value,
  icon: Icon,
  accentClass,
}: {
  label: string;
  value: string | number;
  icon: ComponentType<{ className?: string }>;
  accentClass: string;
}) {
  return (
    <Card className="border-zinc-800 bg-zinc-950">
      <CardContent className="flex items-center justify-between px-6 py-5">
        <div className="space-y-1">
          <div className="text-2xl font-semibold text-zinc-50">{value}</div>
          <div className="text-sm text-zinc-400">{label}</div>
        </div>
        <div className={`rounded-lg border p-2 ${accentClass}`}>
          <Icon className="size-5" />
        </div>
      </CardContent>
    </Card>
  );
}

export function WorkflowsView() {
  const authContext = useContext(AuthContext);
  const currentWorkspaceId = authContext?.currentWorkspaceId ?? null;
  const hasPermission = authContext?.hasPermission ?? (() => false);
  const canViewDashboard = Boolean(
    currentWorkspaceId && hasPermission(currentWorkspaceId, 'workflows', 'view'),
  );

  const [selectedTab, setSelectedTab] = useState<TabValue>('submitted');
  const [items, setItems] = useState<ApprovalWorkflowItem[]>([]);
  const [summary, setSummary] = useState<ApprovalWorkflowSummary>(buildEmptySummary);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyNoteId, setBusyNoteId] = useState<string | null>(null);
  const [dialogState, setDialogState] = useState<{
    mode: DialogMode;
    item: ApprovalWorkflowItem;
  } | null>(null);

  const loadData = useCallback(async () => {
    if (!currentWorkspaceId || !canViewDashboard) {
      setItems([]);
      setSummary(buildEmptySummary());
      return;
    }

    try {
      setLoading(true);
      setError(null);
      const [summaryResult, itemsResult] = await Promise.all([
        api.getApprovalWorkflowSummary(currentWorkspaceId),
        api.listApprovalWorkflows(currentWorkspaceId, {
          status: selectedTab === 'all' ? undefined : selectedTab,
          limit: 100,
        }),
      ]);
      setSummary(summaryResult);
      setItems(itemsResult);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load approval workflows';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [canViewDashboard, currentWorkspaceId, selectedTab]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const stats = useMemo(
    () => [
      {
        label: 'Total Items',
        value: summary.total,
        icon: CircleDot,
        accentClass: 'border-zinc-800 bg-zinc-900 text-zinc-200',
      },
      {
        label: 'Awaiting Review',
        value: summary.countsByStatus.submitted,
        icon: ShieldCheck,
        accentClass: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
      },
      {
        label: 'Needs Changes',
        value: summary.countsByStatus.needs_changes,
        icon: AlertTriangle,
        accentClass: 'border-orange-500/30 bg-orange-500/10 text-orange-300',
      },
      {
        label: 'Overdue',
        value: summary.overdue,
        icon: Clock3,
        accentClass: 'border-rose-500/30 bg-rose-500/10 text-rose-300',
      },
    ],
    [summary],
  );

  const handleRefresh = async () => {
    await loadData();
  };

  const runAction = useCallback(
    async (
      item: ApprovalWorkflowItem,
      action: () => Promise<ApprovalWorkflowItem>,
    ): Promise<boolean> => {
      try {
        setBusyNoteId(item.noteId);
        setError(null);
        await action();
        await loadData();
        return true;
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Workflow action failed';
        setError(message);
        try {
          await loadData();
        } catch {
          // Keep the original action error visible when refresh also fails.
        }
        return false;
      } finally {
        setBusyNoteId(null);
      }
    },
    [loadData],
  );

  const handleApprove = async (item: ApprovalWorkflowItem) => {
    await runAction(item, () =>
      api.approveApprovalWorkflow(item.noteId, {
        currentStatus: item.approvalStatus,
      }),
    );
  };

  const handleCancel = async (item: ApprovalWorkflowItem) => {
    await runAction(item, () =>
      api.cancelApprovalWorkflow(item.noteId, {
        currentStatus: item.approvalStatus,
      }),
    );
  };

  const openDialog = (mode: DialogMode, item: ApprovalWorkflowItem) => {
    setDialogState({ mode, item });
  };

  const handleDialogConfirm = async (payload: {
    comment?: string;
    priority?: ApprovalWorkflowPriority;
    dueAt?: Date | null;
  }) => {
    if (!dialogState) return;
    const { item, mode } = dialogState;
    const succeeded = await runAction(item, async () => {
      if (mode === 'submit') {
        return api.submitApprovalWorkflow(item.noteId, {
          currentStatus: item.approvalStatus,
          priority: payload.priority,
          dueAt: payload.dueAt,
          comment: payload.comment,
        });
      }
      if (mode === 'resubmit') {
        return api.resubmitApprovalWorkflow(item.noteId, {
          currentStatus: item.approvalStatus,
          priority: payload.priority,
          dueAt: payload.dueAt,
          comment: payload.comment,
        });
      }
      if (mode === 'reject') {
        return api.rejectApprovalWorkflow(item.noteId, {
          currentStatus: item.approvalStatus,
          comment: payload.comment || '',
        });
      }
      return api.requestChangesApprovalWorkflow(item.noteId, {
        currentStatus: item.approvalStatus,
        comment: payload.comment || '',
      });
    });
    if (succeeded) {
      setDialogState(null);
    }
  };

  if (!currentWorkspaceId) {
    return (
      <div className="min-h-screen bg-zinc-950 p-8">
        <Empty className="border-zinc-800 bg-zinc-950 text-zinc-50">
          <EmptyHeader>
            <EmptyMedia variant="icon" className="bg-zinc-900 text-zinc-200">
              <CircleDot />
            </EmptyMedia>
            <EmptyTitle>Select a workspace</EmptyTitle>
            <EmptyDescription>
              Approval workflows are scoped to a workspace. Pick one to load the dashboard.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      </div>
    );
  }

  if (!canViewDashboard) {
    return (
      <div className="min-h-screen bg-zinc-950 p-8">
        <Empty className="border-zinc-800 bg-zinc-950 text-zinc-50">
          <EmptyHeader>
            <EmptyMedia variant="icon" className="bg-zinc-900 text-zinc-200">
              <ShieldCheck />
            </EmptyMedia>
            <EmptyTitle>You do not have workflow access</EmptyTitle>
            <EmptyDescription>
              Ask a workspace admin to grant `workflows:view` if you need access to the approvals dashboard.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-zinc-950 px-6 py-8 text-zinc-50 md:px-8">
      <div className="mx-auto flex max-w-7xl flex-col gap-6">
        <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div className="space-y-2">
            <div className="text-xs uppercase tracking-[0.22em] text-zinc-400">Approval Workflows</div>
            <div className="flex items-center gap-3">
              <h1 className="text-4xl font-semibold tracking-tight text-zinc-50">Reviews Dashboard</h1>
              {summary.overdue > 0 ? (
                <div className="rounded-full border border-rose-500/30 bg-rose-500/10 px-3 py-1 text-xs font-medium text-rose-300">
                  {summary.overdue} overdue
                </div>
              ) : null}
            </div>
            <p className="max-w-3xl text-sm text-zinc-400">
              Track every workspace note through draft, submission, review, and final decision with strict server-side transition checks.
            </p>
          </div>

          <Button
            type="button"
            variant="outline"
            onClick={() => {
              void handleRefresh();
            }}
            disabled={loading}
            className="border-zinc-800 bg-zinc-950 text-zinc-100 hover:bg-zinc-900"
          >
            {loading ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
            Refresh
          </Button>
        </div>

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {stats.map((stat) => (
            <DashboardStat key={stat.label} {...stat} />
          ))}
        </div>

        <Card className="border-zinc-800 bg-zinc-950">
          <CardHeader className="gap-4 border-b border-zinc-800 pb-4">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
              <div>
                <CardTitle className="text-zinc-50">Review Queue</CardTitle>
                <CardDescription className="text-zinc-400">
                  Filter by workflow state, then review items inline without leaving the dashboard.
                </CardDescription>
              </div>

              <Tabs value={selectedTab} onValueChange={(value) => setSelectedTab(value as TabValue)}>
                <TabsList className="h-auto flex-wrap gap-2 rounded-xl border border-zinc-800 bg-zinc-900 p-2">
                  {TAB_ORDER.map((tab) => {
                    const count = tab === 'all'
                      ? summary.total
                      : summary.countsByStatus[tab];
                    return (
                      <TabsTrigger
                        key={tab}
                        value={tab}
                        className="rounded-lg border border-transparent px-3 py-1.5 text-xs font-medium uppercase tracking-[0.14em] data-[state=active]:border-amber-400/40 data-[state=active]:bg-amber-500/10 data-[state=active]:text-amber-200"
                      >
                        {TAB_LABELS[tab]} ({count})
                      </TabsTrigger>
                    );
                  })}
                </TabsList>
              </Tabs>
            </div>
          </CardHeader>

          <CardContent className="space-y-4 px-6 py-6">
            {error ? (
              <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
                {error}
              </div>
            ) : null}

            {loading ? (
              <div className="flex min-h-[240px] items-center justify-center rounded-lg border border-dashed border-zinc-800 bg-zinc-950">
                <div className="flex items-center gap-3 text-sm text-zinc-400">
                  <Loader2 className="size-4 animate-spin" />
                  Loading approval items...
                </div>
              </div>
            ) : items.length === 0 ? (
              <Empty className="border-zinc-800 bg-zinc-950 text-zinc-50">
                <EmptyHeader>
                  <EmptyMedia variant="icon" className="bg-zinc-900 text-zinc-200">
                    <Send />
                  </EmptyMedia>
                  <EmptyTitle>No {TAB_LABELS[selectedTab].toLowerCase()} items</EmptyTitle>
                  <EmptyDescription>
                    {selectedTab === 'draft'
                      ? 'Draft notes will appear here when they are ready to be submitted for review.'
                      : 'There are no items in this workflow state right now.'}
                  </EmptyDescription>
                </EmptyHeader>
                <EmptyContent className="text-zinc-400">
                  Use the draft tab to submit notes, or switch filters to inspect past decisions.
                </EmptyContent>
              </Empty>
            ) : (
              <div className="space-y-3">
                {items.map((item) => {
                  const isBusy = busyNoteId === item.noteId;
                  return (
                    <div
                      key={item.noteId}
                      className="grid gap-4 rounded-xl border border-zinc-800 bg-zinc-950 p-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)_auto]"
                    >
                      <div className="space-y-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <ApprovalStatusBadge status={item.approvalStatus} />
                          <ApprovalPriorityBadge priority={item.approvalPriority} />
                          {item.isOverdue ? (
                            <span className="rounded-full border border-rose-500/30 bg-rose-500/10 px-2.5 py-0.5 text-xs font-medium text-rose-300">
                              Overdue
                            </span>
                          ) : null}
                        </div>

                        <div className="space-y-1">
                          <div className="text-lg font-semibold text-zinc-50">{item.title}</div>
                          <div className="text-sm text-zinc-400">
                            {item.summary?.trim() || 'No summary available yet.'}
                          </div>
                        </div>

                        <div className="grid gap-2 text-sm text-zinc-400 sm:grid-cols-2">
                          <div>
                            <span className="text-zinc-500">Author:</span>{' '}
                            {item.author?.name || item.author?.email || item.authorUserId}
                          </div>
                          <div>
                            <span className="text-zinc-500">Submitted:</span>{' '}
                            {formatRelativeDate(item.approvalSubmittedAt)}
                          </div>
                          <div>
                            <span className="text-zinc-500">Due:</span>{' '}
                            <span className={item.isOverdue ? 'text-rose-300' : 'text-zinc-300'}>
                              {formatDueDate(item.approvalDueAt, item.isOverdue)}
                            </span>
                          </div>
                          <div>
                            <span className="text-zinc-500">Last decision:</span>{' '}
                            {item.approvalDecidedAt ? formatRelativeDate(item.approvalDecidedAt) : 'Pending'}
                          </div>
                        </div>
                      </div>

                      <div className="grid gap-2 rounded-lg border border-zinc-800 bg-zinc-900/40 p-3 text-sm text-zinc-400">
                        <div>
                          <div className="text-xs uppercase tracking-[0.14em] text-zinc-500">Submitted By</div>
                          <div className="mt-1 text-zinc-200">
                            {item.submittedBy?.name || item.submittedBy?.email || 'Not submitted'}
                          </div>
                        </div>
                        <div>
                          <div className="text-xs uppercase tracking-[0.14em] text-zinc-500">Decided By</div>
                          <div className="mt-1 text-zinc-200">
                            {item.decidedBy?.name || item.decidedBy?.email || 'Awaiting review'}
                          </div>
                        </div>
                        <div>
                          <div className="text-xs uppercase tracking-[0.14em] text-zinc-500">Due Date</div>
                          <div className="mt-1 text-zinc-200">
                            {item.approvalDueAt ? format(item.approvalDueAt, 'MMM d, yyyy') : 'Not set'}
                          </div>
                        </div>
                      </div>

                      <div className="flex min-w-[220px] flex-col gap-2">
                        {item.availableActions.submit ? (
                          <Button
                            type="button"
                            onClick={() => openDialog('submit', item)}
                            disabled={isBusy}
                            className="justify-start"
                          >
                            <Send className="size-4" />
                            Submit for review
                          </Button>
                        ) : null}

                        {item.availableActions.resubmit ? (
                          <Button
                            type="button"
                            onClick={() => openDialog('resubmit', item)}
                            disabled={isBusy}
                            className="justify-start"
                          >
                            <RefreshCw className="size-4" />
                            Resubmit
                          </Button>
                        ) : null}

                        {item.availableActions.approve ? (
                          <Button
                            type="button"
                            onClick={() => {
                              void handleApprove(item);
                            }}
                            disabled={isBusy}
                            className="justify-start bg-emerald-600 text-white hover:bg-emerald-500"
                          >
                            {isBusy ? <Loader2 className="size-4 animate-spin" /> : <CheckCircle2 className="size-4" />}
                            Approve
                          </Button>
                        ) : null}

                        {item.availableActions.request_changes ? (
                          <Button
                            type="button"
                            variant="outline"
                            onClick={() => openDialog('request_changes', item)}
                            disabled={isBusy}
                            className="justify-start border-orange-500/30 bg-orange-500/10 text-orange-200 hover:bg-orange-500/20"
                          >
                            <AlertTriangle className="size-4" />
                            Request changes
                          </Button>
                        ) : null}

                        {item.availableActions.reject ? (
                          <Button
                            type="button"
                            variant="outline"
                            onClick={() => openDialog('reject', item)}
                            disabled={isBusy}
                            className="justify-start border-rose-500/30 bg-rose-500/10 text-rose-200 hover:bg-rose-500/20"
                          >
                            <XCircle className="size-4" />
                            Reject
                          </Button>
                        ) : null}

                        {item.availableActions.cancel ? (
                          <Button
                            type="button"
                            variant="ghost"
                            onClick={() => {
                              void handleCancel(item);
                            }}
                            disabled={isBusy}
                            className="justify-start text-zinc-300 hover:bg-zinc-900 hover:text-zinc-50"
                          >
                            {isBusy ? <Loader2 className="size-4 animate-spin" /> : <XCircle className="size-4" />}
                            Cancel
                          </Button>
                        ) : null}

                        {!Object.values(item.availableActions).some(Boolean) ? (
                          <div className="rounded-lg border border-dashed border-zinc-800 px-3 py-4 text-sm text-zinc-500">
                            No actions available in the current state.
                          </div>
                        ) : null}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <ApprovalReviewDialog
        open={dialogState !== null}
        onOpenChange={(open) => {
          if (!open) setDialogState(null);
        }}
        mode={dialogState?.mode || 'submit'}
        isSubmitting={Boolean(dialogState && busyNoteId === dialogState.item.noteId)}
        initialPriority={dialogState?.item.approvalPriority}
        initialDueAt={dialogState?.item.approvalDueAt}
        onConfirm={handleDialogConfirm}
      />
    </div>
  );
}
