/**
 * Event types and interfaces for the real-time event system.
 */

export enum EventType {
  // Document lifecycle
  DOCUMENT_CREATED = 'document.created',
  DOCUMENT_STARTED = 'document.started',
  DOCUMENT_PROCESSING = 'document.processing',
  DOCUMENT_COMPLETED = 'document.completed',
  DOCUMENT_FAILED = 'document.failed',

  // Stage events
  STAGE_STARTED = 'stage.started',
  STAGE_COMPLETED = 'stage.completed',
  STAGE_FAILED = 'stage.failed',

  // Progress updates
  PROGRESS_UPDATE = 'progress.update',

  // Worker health
  WORKER_HEALTH = 'worker.health',

  // System events
  SYSTEM_ERROR = 'system.error',
  SYSTEM_NOTIFICATION = 'system.notification',

  // Connection events (client-side)
  CONNECTED = 'connected',
  DISCONNECTED = 'disconnected',
  RECONNECTING = 'reconnecting',
}

export enum EventPriority {
  LOW = 'low',
  NORMAL = 'normal',
  HIGH = 'high',
  CRITICAL = 'critical',
}

export interface Event {
  event_type: EventType | string;
  workspace_id: string;
  document_id?: string;
  user_id?: string;
  data: Record<string, any>;
  priority: EventPriority | string;
  stage?: string;
  timestamp: string;
}

export interface DocumentStartedEvent extends Event {
  event_type: EventType.DOCUMENT_STARTED;
  data: {
    document_title: string;
  };
}

export interface StageUpdateEvent extends Event {
  event_type: EventType.STAGE_STARTED | EventType.STAGE_COMPLETED;
  stage: string;
  data: {
    stage: string;
    status: string;
    progress?: Record<string, any>;
  };
}

export interface ProgressUpdateEvent extends Event {
  event_type: EventType.PROGRESS_UPDATE;
  data: {
    chunks_created?: number;
    embeddings_created?: number;
    total_tokens?: number;
    [key: string]: any;
  };
}

export interface DocumentCompletedEvent extends Event {
  event_type: EventType.DOCUMENT_COMPLETED;
  data: {
    token_count: number;
    chunk_count: number;
    embedding_count: number;
  };
}

export interface DocumentFailedEvent extends Event {
  event_type: EventType.DOCUMENT_FAILED;
  data: {
    error_message: string;
    stage: string;
  };
}

export interface WorkerHealthEvent extends Event {
  workspace_id: 'system';
  data: {
    worker_id: string;
    status: string;
    tasks_active: number;
    tasks_processed: number;
    uptime_seconds: number;
  };
}

// Union type for all events
export type AllEvents =
  | DocumentStartedEvent
  | StageUpdateEvent
  | ProgressUpdateEvent
  | DocumentCompletedEvent
  | DocumentFailedEvent
  | WorkerHealthEvent
  | Event;

// Event listener callback
export type EventListener = (event: AllEvents) => void;

// Event filter for selective listening
export interface EventFilter {
  eventTypes?: EventType[];
  workspaceId?: string;
  documentId?: string;
  userId?: string;
}
