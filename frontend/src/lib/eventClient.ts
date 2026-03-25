/**
 * Production-ready Event Client for real-time event streaming.
 * Supports both WebSocket and Server-Sent Events (SSE).
 */

import type { Event, EventListener, EventFilter } from '../types/events';
import { EventType } from '../types/events';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
const WS_BASE_URL = API_BASE_URL.replace(/^http/, 'ws');
const RECONNECT_DELAY_MS = 3000;
const MAX_RECONNECT_ATTEMPTS = 10;
const HEARTBEAT_INTERVAL_MS = 30000; // 30 seconds
const CONNECTION_TIMEOUT_MS = 10000; // 10 seconds

export type ConnectionMethod = 'websocket' | 'sse';

export interface EventClientConfig {
  method?: ConnectionMethod;
  token?: string;
  workspaceId: string;
  onConnected?: () => void;
  onDisconnected?: () => void;
  onError?: (error: Error) => void;
}

/**
 * EventClient provides real-time event streaming.
 *
 * Usage:
 * ```typescript
 * const client = new EventClient({
 *   workspaceId: 'workspace-id',
 *   token: 'jwt-token',
 *   method: 'websocket'
 * });
 *
 * client.on(EventType.DOCUMENT_COMPLETED, (event) => {
 *   console.log('Document completed:', event);
 * });
 *
 * await client.connect();
 * ```
 */
export class EventClient {
  private workspaceId: string;
  private token?: string;
  private method: ConnectionMethod = 'websocket';
  private listeners: Map<string, Set<EventListener>> = new Map();
  private ws: WebSocket | null = null;
  private eventSource: EventSource | null = null;
  private connected: boolean = false;
  private reconnectAttempts: number = 0;
  private heartbeatTimer?: NodeJS.Timeout;
  private reconnectTimer?: NodeJS.Timeout;
  private config: Partial<EventClientConfig>;

  constructor(config: EventClientConfig) {
    this.workspaceId = config.workspaceId;
    this.token = config.token;
    this.method = config.method || 'websocket';
    this.config = config;

    if (!this.workspaceId) {
      throw new Error('workspaceId is required');
    }
  }

  /**
   * Connect to the event stream.
   */
  async connect(): Promise<void> {
    if (this.connected) {
      console.warn('Already connected');
      return;
    }

    try {
      if (this.method === 'websocket') {
        await this.connectWebSocket();
      } else {
        await this.connectSSE();
      }

      this.reconnectAttempts = 0;
      this.config.onConnected?.();
      console.log(`✅ Event client connected (${this.method})`);
    } catch (error) {
      const err = error instanceof Error ? error : new Error(String(error));
      console.error('Failed to connect:', err);
      this.config.onError?.(err);
      this.scheduleReconnect();
    }
  }

  /**
   * Connect via WebSocket.
   */
  private connectWebSocket(): Promise<void> {
    return new Promise((resolve, reject) => {
      try {
        const url = new URL(
          `/events/ws/ingestion/${this.workspaceId}`,
          WS_BASE_URL
        );

        // Pass token as query parameter if available
        if (this.token) {
          url.searchParams.set('token', this.token);
          console.debug('🔐 [WS TOKEN] Token set in query parameters');
        } else {
          console.warn('⚠️ [WS TOKEN] No token available for WebSocket connection');
        }

        console.debug('📡 [WS CONNECT] Connecting to:', url.toString().replace(/token=[^&]*/, 'token=****'));

        this.ws = new WebSocket(url.toString());

        const timeout = setTimeout(
          () => reject(new Error('WebSocket connection timeout')),
          CONNECTION_TIMEOUT_MS
        );

        this.ws.onopen = () => {
          clearTimeout(timeout);
          this.connected = true;
          this.startHeartbeat();
          resolve();
        };

        this.ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            this.handleEvent(data);
          } catch (error) {
            console.error('Failed to parse WebSocket message:', error);
          }
        };

        this.ws.onerror = (error) => {
          clearTimeout(timeout);
          console.error('WebSocket error:', error);
          reject(new Error('WebSocket error'));
        };

        this.ws.onclose = () => {
          this.connected = false;
          this.stopHeartbeat();
          this.config.onDisconnected?.();
          this.scheduleReconnect();
        };
      } catch (error) {
        reject(error);
      }
    });
  }

  /**
   * Connect via Server-Sent Events.
   */
  private connectSSE(): Promise<void> {
    return new Promise((resolve, reject) => {
      try {
        const url = new URL(
          `/events/sse/ingestion/${this.workspaceId}`,
          API_BASE_URL
        );

        const headers: Record<string, string> = {};
        if (this.token) {
          headers['Authorization'] = `Bearer ${this.token}`;
        }

        this.eventSource = new EventSource(url.toString());

        const timeout = setTimeout(
          () => reject(new Error('SSE connection timeout')),
          CONNECTION_TIMEOUT_MS
        );

        this.eventSource.onopen = () => {
          clearTimeout(timeout);
          this.connected = true;
          this.startHeartbeat();
          resolve();
        };

        this.eventSource.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            this.handleEvent(data);
          } catch (error) {
            console.error('Failed to parse SSE message:', error);
          }
        };

        this.eventSource.onerror = (error) => {
          clearTimeout(timeout);
          console.error('SSE error:', error);
          this.connected = false;
          this.stopHeartbeat();
          reject(new Error('SSE connection error'));
        };
      } catch (error) {
        reject(error);
      }
    });
  }

  /**
   * Disconnect from the event stream.
   */
  disconnect(): void {
    this.connected = false;
    this.stopHeartbeat();

    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }

    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }

    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
    }

    console.log('Event client disconnected');
  }

  /**
   * Register a listener for a specific event type.
   */
  on(eventType: EventType | string, listener: EventListener): void {
    if (!this.listeners.has(eventType)) {
      this.listeners.set(eventType, new Set());
    }
    this.listeners.get(eventType)!.add(listener);
  }

  /**
   * Unregister a listener.
   */
  off(eventType: EventType | string, listener: EventListener): void {
    const set = this.listeners.get(eventType);
    if (set) {
      set.delete(listener);
      if (set.size === 0) {
        this.listeners.delete(eventType);
      }
    }
  }

  /**
   * Listen to all events.
   */
  onAll(listener: EventListener): void {
    this.on('*', listener);
  }

  /**
   * Get connection status.
   */
  isConnected(): boolean {
    return this.connected;
  }

  /**
   * Get current connection method.
   */
  getMethod(): ConnectionMethod {
    return this.method;
  }

  /**
   * Switch connection method and reconnect.
   */
  async switchMethod(method: ConnectionMethod): Promise<void> {
    if (method === this.method) {
      return;
    }

    this.disconnect();
    this.method = method;
    await this.connect();
  }

  /**
   * Handle incoming event.
   */
  private handleEvent(eventData: any): void {
    if (!eventData.event_type) {
      return;
    }

    const event: Event = eventData;

    // Call specific event listeners
    const listeners = this.listeners.get(event.event_type);
    if (listeners) {
      listeners.forEach((listener) => listener(event));
    }

    // Call wildcard listeners
    const wildcardListeners = this.listeners.get('*');
    if (wildcardListeners) {
      wildcardListeners.forEach((listener) => listener(event));
    }
  }

  /**
   * Start sending heartbeat pings to keep connection alive.
   */
  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        try {
          this.ws.send(JSON.stringify({ type: 'ping' }));
        } catch (error) {
          console.error('Failed to send heartbeat:', error);
        }
      }
    }, HEARTBEAT_INTERVAL_MS);
  }

  /**
   * Stop sending heartbeat.
   */
  private stopHeartbeat(): void {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = undefined;
    }
  }

  /**
   * Schedule a reconnection attempt.
   */
  private scheduleReconnect(): void {
    if (this.reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      console.error('Max reconnection attempts reached');
      this.config.onError?.(
        new Error('Failed to reconnect after maximum attempts')
      );
      return;
    }

    this.reconnectAttempts++;
    const delay = RECONNECT_DELAY_MS * Math.pow(2, this.reconnectAttempts - 1);
    console.log(
      `Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`
    );

    this.reconnectTimer = setTimeout(() => {
      this.connect().catch((error) => {
        console.error('Reconnection failed:', error);
        this.scheduleReconnect();
      });
    }, Math.min(delay, 60000)); // Max 1 minute
  }
}

/**
 * Global event client singleton.
 */
let globalClient: EventClient | null = null;

/**
 * Create or get the global event client.
 */
export function createEventClient(
  config: EventClientConfig
): EventClient {
  if (!globalClient) {
    globalClient = new EventClient(config);
  }
  return globalClient;
}

/**
 * Get the global event client.
 */
export function getEventClient(): EventClient | null {
  return globalClient;
}

/**
 * Reset the global event client.
 */
export function resetEventClient(): void {
  globalClient?.disconnect();
  globalClient = null;
}
