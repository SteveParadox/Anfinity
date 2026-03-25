/**
 * Workspace Switcher Component
 * Allows users to switch between workspaces they have access to
 */

import { useAuth } from '@/contexts/AuthContext';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Briefcase } from 'lucide-react';

export function WorkspaceSwitcher() {
  const { workspaces, currentWorkspaceId, setCurrentWorkspace } = useAuth();

  if (!workspaces || workspaces.length === 0) {
    return null;
  }

  const currentWorkspace = workspaces.find(w => w.id === currentWorkspaceId);

  return (
    <div className="flex items-center gap-2">
      <Briefcase className="w-4 h-4 text-slate-400" />
      <Select value={currentWorkspaceId || ''} onValueChange={setCurrentWorkspace}>
        <SelectTrigger className="w-48">
          <SelectValue placeholder="Select workspace" />
        </SelectTrigger>
        <SelectContent>
          {workspaces.map(workspace => (
            <SelectItem key={workspace.id} value={workspace.id}>
              <div className="flex items-center gap-2">
                <span>{workspace.name}</span>
                <span className="text-xs text-slate-500">({workspace.role})</span>
              </div>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
