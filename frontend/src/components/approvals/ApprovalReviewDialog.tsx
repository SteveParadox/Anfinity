import { useEffect, useMemo, useState } from 'react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { ApprovalWorkflowPriority } from '@/types';


type ApprovalDialogMode = 'submit' | 'resubmit' | 'reject' | 'request_changes';

interface ApprovalReviewDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode: ApprovalDialogMode;
  isSubmitting?: boolean;
  initialPriority?: ApprovalWorkflowPriority;
  initialDueAt?: Date;
  onConfirm: (payload: {
    comment?: string;
    priority?: ApprovalWorkflowPriority;
    dueAt?: Date | null;
  }) => Promise<void> | void;
}

const TITLES: Record<ApprovalDialogMode, string> = {
  submit: 'Submit for Approval',
  resubmit: 'Resubmit for Review',
  reject: 'Reject Item',
  request_changes: 'Request Changes',
};

const DESCRIPTIONS: Record<ApprovalDialogMode, string> = {
  submit: 'Set the review priority and an optional due date before sending this note to reviewers.',
  resubmit: 'Update the priority or due date if needed, then send the revised note back to reviewers.',
  reject: 'Add a clear reason so the submitter understands why the item was rejected.',
  request_changes: 'Tell the submitter exactly what needs to change before approval.',
};

export function ApprovalReviewDialog({
  open,
  onOpenChange,
  mode,
  isSubmitting = false,
  initialPriority = 'normal',
  initialDueAt,
  onConfirm,
}: ApprovalReviewDialogProps) {
  const [comment, setComment] = useState('');
  const [priority, setPriority] = useState<ApprovalWorkflowPriority>(initialPriority);
  const [dueDate, setDueDate] = useState('');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setComment('');
    setPriority(initialPriority);
    setDueDate(initialDueAt ? initialDueAt.toISOString().slice(0, 10) : '');
    setError(null);
  }, [open, initialPriority, initialDueAt]);

  const requiresComment = mode === 'reject' || mode === 'request_changes';
  const showsSchedulingFields = mode === 'submit' || mode === 'resubmit';

  const confirmLabel = useMemo(() => {
    if (mode === 'submit') return 'Submit';
    if (mode === 'resubmit') return 'Resubmit';
    if (mode === 'reject') return 'Reject';
    return 'Request Changes';
  }, [mode]);

  const handleConfirm = async () => {
    if (requiresComment && !comment.trim()) {
      setError('A comment is required for this action.');
      return;
    }
    setError(null);
    await onConfirm({
      comment: comment.trim() || undefined,
      priority: showsSchedulingFields ? priority : undefined,
      dueAt: showsSchedulingFields && dueDate ? new Date(`${dueDate}T23:59:59`) : null,
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="border-zinc-800 bg-zinc-950 text-zinc-100">
        <DialogHeader>
          <DialogTitle>{TITLES[mode]}</DialogTitle>
          <DialogDescription>{DESCRIPTIONS[mode]}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {showsSchedulingFields && (
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="space-y-2 text-sm">
                <span className="text-zinc-300">Priority</span>
                <select
                  value={priority}
                  onChange={(event) => setPriority(event.target.value as ApprovalWorkflowPriority)}
                  className="flex h-10 w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 text-sm text-zinc-100 outline-none focus:border-amber-400"
                >
                  <option value="low">Low</option>
                  <option value="normal">Normal</option>
                  <option value="high">High</option>
                  <option value="critical">Critical</option>
                </select>
              </label>

              <label className="space-y-2 text-sm">
                <span className="text-zinc-300">Due date</span>
                <input
                  type="date"
                  value={dueDate}
                  onChange={(event) => setDueDate(event.target.value)}
                  className="flex h-10 w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 text-sm text-zinc-100 outline-none focus:border-amber-400"
                />
              </label>
            </div>
          )}

          <label className="space-y-2 text-sm">
            <span className="text-zinc-300">
              {requiresComment ? 'Review comment' : 'Comment'}
            </span>
            <textarea
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              rows={5}
              placeholder={requiresComment ? 'Explain the decision clearly…' : 'Optional context for reviewers…'}
              className="flex w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-amber-400"
            />
          </label>

          {error ? <p className="text-sm text-rose-400">{error}</p> : null}
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isSubmitting}
          >
            Cancel
          </Button>
          <Button
            type="button"
            onClick={() => {
              void handleConfirm();
            }}
            disabled={isSubmitting}
          >
            {isSubmitting ? 'Working…' : confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
