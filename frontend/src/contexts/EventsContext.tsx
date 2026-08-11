/**
 * React Context for managing real-time event streaming.
 * Provides global access to the event client and event state.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react';
import {
  createEventClient,
  resetEventClient,
  type EventClient,
} from '../lib/eventClient';
import type { Event } from '../types/events';
import { EventType } from '../types/events';

interface EventsClientContextType {
  client: EventClient | null;
  connected: boolean;
  error: Error | null;
}

interface EventsContextType extends EventsClientContextType {
  lastEvent: Event | null;
}

const EventsContext = createContext<EventsClientContextType | undefined>(undefined);
const DOCUMENT_PROGRESS_EVENT_TYPES = [
  EventType.DOCUMENT_STARTED,
  EventType.STAGE_STARTED,
  EventType.STAGE_COMPLETED,
  EventType.PROGRESS_UPDATE,
  EventType.DOCUMENT_COMPLETED,
  EventType.DOCUMENT_FAILED,
] as const;

let lastEventSnapshot: Event | null = null;
const lastEventSubscribers = new Set<() => void>();

function publishLastEvent(event: Event) {
  lastEventSnapshot = event;
  lastEventSubscribers.forEach((notify) => notify());
}

function subscribeLastEvent(notify: () => void) {
  lastEventSubscribers.add(notify);
  return () => {
    lastEventSubscribers.delete(notify);
  };
}

interface EventsProviderProps {
  children: ReactNode;
  workspaceId?: string;
  token?: string;
}

export function EventsProvider({
  children,
  workspaceId,
  token,
}: EventsProviderProps) {
  const [client, setClient] = useState<EventClient | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let disposed = false;
    let activeClient: EventClient | null = null;

    if (!workspaceId) {
      if (import.meta.env.DEV) {
        console.warn('EventsProvider: workspaceId not provided, events disabled');
      }
      setClient(null);
      setConnected(false);
      return;
    }

    if (!token) {
      if (import.meta.env.DEV) {
        console.warn('[EVENTS INIT] Waiting for authorization token...');
      }
      setClient(null);
      setConnected(false);
      return;
    }

    const initializeClient = async () => {
      try {
        if (import.meta.env.DEV) {
          console.debug('[EVENTS INIT START] Initializing EventClient - Workspace:', workspaceId);
          console.debug('[TOKEN CHECK] Token available:', token ? '****' : 'MISSING');
          console.debug('[RESET] Clearing any existing event clients');
        }

        resetEventClient();

        if (import.meta.env.DEV) {
          console.debug('[CREATE CLIENT] Creating new EventClient with WebSocket method');
        }

        const newClient = createEventClient({
          workspaceId,
          token,
          method: 'websocket',
          onConnected: () => {
            setConnected(true);
            setError(null);
            if (import.meta.env.DEV) {
              console.debug('[EVENTS CONNECTED] WebSocket connection established - Workspace:', workspaceId);
            }
          },
          onDisconnected: () => {
            setConnected(false);
            if (import.meta.env.DEV) {
              console.debug('[EVENTS DISCONNECTED] WebSocket connection closed - Workspace:', workspaceId);
            }
          },
          onError: (err) => {
            setError(err);
            console.error('[EVENTS ERROR] Event streaming error:', err);
          },
        });

        if (disposed) {
          newClient.disconnect();
          return;
        }

        activeClient = newClient;

        if (import.meta.env.DEV) {
          console.debug('[LISTENER] Registering event listener for all event types');
        }

        newClient.onAll((event) => {
          if (import.meta.env.DEV) {
            console.debug(
              '[EVENT RECEIVED] Event type:',
              event.event_type,
              '- Document:',
              event.document_id,
              '- Timestamp:',
              new Date(event.timestamp).toISOString(),
            );
          }
          publishLastEvent(event);
        });

        setClient(newClient);

        if (import.meta.env.DEV) {
          console.debug('[CONNECT] Attempting WebSocket connection');
        }

        await newClient.connect();

        if (disposed) {
          newClient.disconnect();
          return;
        }

        if (import.meta.env.DEV) {
          console.debug('[EVENTS INIT SUCCESS] EventClient initialized and connected');
        }
      } catch (err) {
        if (disposed) {
          return;
        }

        const nextError = err instanceof Error ? err : new Error(String(err));
        console.error('[EVENTS INIT FAILED] Failed to initialize EventClient:', nextError);
        setError(nextError);
        setClient(null);
      }
    };

    void initializeClient();

    return () => {
      disposed = true;
      activeClient?.disconnect();
      resetEventClient();
    };
  }, [workspaceId, token]);

  const value = useMemo<EventsClientContextType>(() => ({
    client,
    connected,
    error,
  }), [client, connected, error]);

  return (
    <EventsContext.Provider value={value}>
      {children}
    </EventsContext.Provider>
  );
}

export function useEvents(): EventsContextType {
  const context = useContext(EventsContext);
  if (!context) {
    throw new Error('useEvents must be used within EventsProvider');
  }
  const lastEvent = useSyncExternalStore(
    subscribeLastEvent,
    () => lastEventSnapshot,
    () => null,
  );
  return {
    ...context,
    lastEvent,
  };
}

function useEventClientContext(): EventsClientContextType {
  const context = useContext(EventsContext);
  if (!context) {
    throw new Error('useEventClientContext must be used within EventsProvider');
  }
  return context;
}

export function useEventListener(
  eventType: EventType | readonly EventType[],
  callback: (event: Event) => void
) {
  const { client } = useEventClientContext();
  const eventTypes = useMemo<EventType[]>(
    () => (Array.isArray(eventType) ? [...eventType] : [eventType]),
    [eventType],
  );

  useEffect(() => {
    if (!client) return;

    const unsubscribers = eventTypes.map((type) => {
      client.on(type, callback);
      return () => client.off(type, callback);
    });

    return () => {
      unsubscribers.forEach((unsub) => unsub());
    };
  }, [client, eventTypes, callback]);
}

export function useAllEvents(callback: (event: Event) => void) {
  const { client } = useEventClientContext();

  useEffect(() => {
    if (!client) return;

    client.onAll(callback);

    return () => {
      client.off('*', callback);
    };
  }, [client, callback]);
}

export function useEventConnection() {
  const { client, connected, error } = useEventClientContext();

  return {
    connected,
    error,
    isConnected: () => connected,
    method: client?.getMethod(),
  };
}

export function useDocumentProgress(
  documentId: string,
  workspaceId: string
): {
  status: 'idle' | 'started' | 'processing' | 'completed' | 'failed';
  stage?: string;
  progress?: Record<string, unknown>;
  error?: string;
} {
  const [state, setState] = useState<{
    status: 'idle' | 'started' | 'processing' | 'completed' | 'failed';
    stage?: string;
    progress?: Record<string, unknown>;
    error?: string;
  }>({ status: 'idle' });

  const handleProgressEvent = useCallback((event: Event) => {
    if (
      event.document_id !== documentId ||
      event.workspace_id !== workspaceId
    ) {
      return;
    }

    switch (event.event_type) {
      case EventType.DOCUMENT_STARTED:
        setState({
          status: 'started',
          progress: event.data,
        });
        break;

      case EventType.STAGE_STARTED:
        setState((prev) => ({
          ...prev,
          status: 'processing',
          stage: event.stage,
          progress: event.data.progress,
        }));
        break;

      case EventType.STAGE_COMPLETED:
        setState((prev) => ({
          ...prev,
          stage: event.stage,
          progress: event.data.progress,
        }));
        break;

      case EventType.PROGRESS_UPDATE:
        setState((prev) => ({
          ...prev,
          progress: event.data,
        }));
        break;

      case EventType.DOCUMENT_COMPLETED:
        setState({
          status: 'completed',
          progress: event.data,
        });
        break;

      case EventType.DOCUMENT_FAILED:
        setState({
          status: 'failed',
          stage: event.data.stage,
          error: event.data.error_message,
        });
        break;
    }
  }, [documentId, workspaceId]);

  useEventListener(DOCUMENT_PROGRESS_EVENT_TYPES, handleProgressEvent);

  return state;
}
