/**
 * Invite Members Component
 * UI for inviting members to a workspace with role selection
 */

import { useState } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Loader2, UserPlus, AlertCircle, CheckCircle } from 'lucide-react';
import { api } from '@/lib/api';

export interface InviteMembersDialogProps {
  workspaceId: string;
  onInviteSent?: () => void;
}

export function InviteMembersDialog({ workspaceId, onInviteSent }: InviteMembersDialogProps) {
  const { currentWorkspaceId, hasRole } = useAuth();
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState('member');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  // Only allow admins and owners to invite
  const canInvite = hasRole(workspaceId || currentWorkspaceId || '', 'admin');

  if (!canInvite) {
    return null;
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSuccess(false);
    setIsLoading(true);

    try {
      if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
        setError('Please enter a valid email address');
        setIsLoading(false);
        return;
      }

      await api.inviteToWorkspace(
        workspaceId,
        email,
        role as 'owner' | 'admin' | 'member' | 'viewer'
      );

      setSuccess(true);
      setEmail('');
      setRole('member');

      setTimeout(() => {
        setSuccess(false);
        setOpen(false);
        onInviteSent?.();
      }, 2000);
    } catch (err: any) {
      setError(err.message || 'Failed to send invitation');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="gap-2">
          <UserPlus className="w-4 h-4" />
          Invite Members
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Invite Members</DialogTitle>
          <DialogDescription>
            Add new members to your workspace with their preferred role
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          {error && (
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          {success && (
            <Alert className="border-green-500/20 bg-green-500/10">
              <CheckCircle className="h-4 w-4 text-green-600" />
              <AlertDescription className="text-green-600">
                Invitation sent successfully!
              </AlertDescription>
            </Alert>
          )}

          <div className="space-y-2">
            <label htmlFor="invite-email" className="text-sm font-medium">
              Email Address
            </label>
            <Input
              id="invite-email"
              type="email"
              placeholder="user@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={isLoading}
            />
          </div>

          <div className="space-y-2">
            <label htmlFor="invite-role" className="text-sm font-medium">
              Role
            </label>
            <Select value={role} onValueChange={setRole} disabled={isLoading}>
              <SelectTrigger id="invite-role">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="viewer">Viewer (Read-only)</SelectItem>
                <SelectItem value="member">Member (Can contribute)</SelectItem>
                <SelectItem value="admin">Admin (Full control)</SelectItem>
                <SelectItem value="owner">Owner (All permissions)</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-slate-500">
              {role === 'viewer' && 'Can view documents and workspace content'}
              {role === 'member' && 'Can upload documents and create notes'}
              {role === 'admin' && 'Can manage members and workspace settings'}
              {role === 'owner' && 'Full control over workspace'}
            </p>
          </div>

          <div className="flex gap-2 justify-end pt-4">
            <Button
              type="button"
              variant="outline"
              onClick={() => setOpen(false)}
              disabled={isLoading}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={isLoading || !email}>
              {isLoading ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Sending...
                </>
              ) : (
                'Send Invitation'
              )}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
