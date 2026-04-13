/**
 * React Context for managing real-time event streaming.
 * Provides global access to the event client and event state.
 */

import React, {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react';
import {
  EventClient,
  createEventClient,
  resetEventClient,
} from '../lib/eventClient';
import type { Event } from '../types/events';
import { EventType } from '../types/events';

interface EventsContextType {
  client: EventClient | null;
  connected: boolean;
  error: Error | null;
  lastEvent: Event | null;
}

const EventsContext = createContext<EventsContextType | undefined>(undefined);

interface EventsProviderProps {
  children: ReactNode;
  workspaceId?: string;
  token?: string;
}

/**
 * Provider component for events context.
 *
 * Should be placed high in the component tree, typically around the workspace view.
 *
 * Usage:
 * ```tsx
 * <EventsProvider workspaceId={workspaceId} token={token}>
 *   <YourApp />
 * </EventsProvider>
 * ```
 */
export function EventsProvider({
  children,
  workspaceId,
  token,
}: EventsProviderProps) {
  const [client, setClient] = useState<EventClient | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [lastEvent, setLastEvent] = useState<Event | null>(null);

  useEffect(() => {
    if (!workspaceId) {
      console.warn(
        'EventsProvider: workspaceId not provided, events disabled'
      );
      setClient(null);
      setConnected(false);
      return;
    }

    if (!token) {
      console.warn('⚠️  [EVENTS INIT] Waiting for authorization token...');
      setClient(null);
      setConnected(false);
      return;
    }

    let activeClient: EventClient | null = null;

    const initializeClient = async () => {
      try {
        console.log('📡 [EVENTS INIT START] Initializing EventClient - Workspace:', workspaceId);
        console.log('🔐 [TOKEN CHECK] Token available:', token ? '****' : 'MISSING');
        
        // Reset any existing client
        console.debug('🧹 [RESET] Clearing any existing event clients');
        resetEventClient();

        // Create new client
        console.debug('🔧 [CREATE CLIENT] Creating new EventClient with WebSocket method');
        const newClient = createEventClient({
          workspaceId,
          token: token,  // Guaranteed to be defined at this point
          method: 'websocket', // Try WebSocket first
          onConnected: () => {
            setConnected(true);
            setError(null);
            console.log('✅ [EVENTS CONNECTED] WebSocket connection established - Workspace:', workspaceId);
          },
          onDisconnected: () => {
            setConnected(false);
            console.log('❌ [EVENTS DISCONNECTED] WebSocket connection closed - Workspace:', workspaceId);
          },
          onError: (err) => {
            setError(err);
            console.error('❌ [EVENTS ERROR] Event streaming error:', err);
          },
        });
        activeClient = newClient;

        // Listen to all events and track the last one
        console.debug('🎧 [LISTENER] Registering event listener for all event types');
        newClient.onAll((event) => {
          console.debug('📨 [EVENT RECEIVED] Event type:', event.event_type, '- Document:', event.document_id, '- Timestamp:', new Date(event.timestamp).toISOString());
          setLastEvent(event);
        });

        setClient(newClient);
        console.debug('💾 [STATE UPDATE] EventClient state updated');

        // Connect
        console.debug('🔌 [CONNECT] Attempting WebSocket connection');
        await newClient.connect();
        console.log('✅ [EVENTS INIT SUCCESS] EventClient initialized and connected');
      } catch (err) {
        const error = err instanceof Error ? err : new Error(String(err));
        console.error('❌ [EVENTS INIT FAILED] Failed to initialize EventClient:', error);
        setError(error);
        setClient(null);
      }
    };

    initializeClient();

    return () => {
      // Cleanup on unmount
      activeClient?.disconnect();
      resetEventClient();
    };
  }, [workspaceId, token]);

  return (
    <EventsContext.Provider
      value={{
        client,
        connected,
        error,
        lastEvent,
      }}
    >
      {children}
    </EventsContext.Provider>
  );
}

/**
 * Hook to access the events context.
 */
export function useEvents(): EventsContextType {
  const context = useContext(EventsContext);
  if (!context) {
    throw new Error('useEvents must be used within EventsProvider');
  }
  return context;
}

/**
 * Hook to listen for specific event types.
 *
 * Usage:
 * ```tsx
 * useEventListener(EventType.DOCUMENT_COMPLETED, (event) => {
 *   console.log('Document completed:', event);
 * });
 * ```
 */
export function useEventListener(
  eventType: EventType | EventType[],
  callback: (event: Event) => void
) {
  const { client } = useEvents();

  useEffect(() => {
    if (!client) return;

    const eventTypes = Array.isArray(eventType) ? eventType : [eventType];

    // Register listeners
    const unsubscribers = eventTypes.map((type) => {
      client.on(type, callback);
      return () => client.off(type, callback);
    });

    // Cleanup
    return () => {
      unsubscribers.forEach((unsub) => unsub());
    };
  }, [client, eventType, callback]);
}

/**
 * Hook to listen for all events.
 *
 * Usage:
 * ```tsx
 * useAllEvents((event) => {
 *   console.log('Event received:', event);
 * });
 * ```
 */
export function useAllEvents(callback: (event: Event) => void) {
  const { client } = useEvents();

  useEffect(() => {
    if (!client) return;

    client.onAll(callback);

    return () => {
      client.off('*', callback);
    };
  }, [client, callback]);
}

/**
 * Hook to get the current connection status.
 */
export function useEventConnection() {
  const { client, connected, error } = useEvents();

  return {
    connected,
    error,
    isConnected: () => connected,
    method: client?.getMethod(),
  };
}

/**
 * Hook to track document processing progress.
 *
 * Usage:
 * ```tsx
 * const progress = useDocumentProgress(documentId, workspaceId);
 * console.log(progress);
 * // { status: 'processing', stage: 'chunking', progress: {...} }
 * ```
 */
export function useDocumentProgress(
  documentId: string,
  workspaceId: string
): {
  status: 'idle' | 'started' | 'processing' | 'completed' | 'failed';
  stage?: string;
  progress?: Record<string, any>;
  error?: string;
} {
  const [state, setState] = useState<{
    status: 'idle' | 'started' | 'processing' | 'completed' | 'failed';
    stage?: string;
    progress?: Record<string, any>;
    error?: string;
  }>({ status: 'idle' });

  useEventListener(
    [
      EventType.DOCUMENT_STARTED,
      EventType.STAGE_STARTED,
      EventType.STAGE_COMPLETED,
      EventType.PROGRESS_UPDATE,
      EventType.DOCUMENT_COMPLETED,
      EventType.DOCUMENT_FAILED,
    ],
    (event) => {
      // Only handle events for this document
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
    }
  );

  return state;
}
