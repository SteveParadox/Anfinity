import { getServerByName } from "partyserver";
import NoteCollaborationServer from "./collaboration-server";
import ThinkingSessionServer from "./thinking-session-server";
import { backendUrl, parseRoomPath, type RealtimeEnv } from "./security";

export { NoteCollaborationServer, ThinkingSessionServer };

export async function handleRequest(request: Request, env: RealtimeEnv): Promise<Response> {
  const url = new URL(request.url);
  if (url.pathname === "/health" && request.method === "GET") {
    return Response.json({ status: "ok", service: "anfinity-realtime" }, { headers: { "Cache-Control": "no-store" } });
  }
  const route = parseRoomPath(url.pathname);
  if (route) console.log(JSON.stringify({ event: "connection_attempt", kind: route.kind }));
  if (!route) return new Response("Not found", { status: 404 });
  if (request.method !== "GET") return new Response("Method not allowed", { status: 405 });
  const origins = (env.REALTIME_ALLOWED_ORIGINS ?? "").split(",").map((s) => s.trim()).filter(Boolean);
  if (!origins.includes(request.headers.get("Origin") ?? "")) return new Response("Forbidden origin", { status: 403 });
  if (request.headers.get("Upgrade")?.toLowerCase() !== "websocket") return new Response("WebSocket upgrade required", { status: 426 });
  const ticket = url.searchParams.get("ticket");
  if (!ticket || ticket.length > 128) return new Response("Unauthorized", { status: 401 });
  if (!env.REALTIME_SERVICE_SECRET) return new Response("Service unavailable", { status: 503 });
  try {
    const redemption = await fetch(`${backendUrl(env)}/realtime/redeem`, {
      method: "POST", signal: AbortSignal.timeout(5000),
      headers: { "Content-Type": "application/json", "X-Realtime-Secret": env.REALTIME_SERVICE_SECRET },
      body: JSON.stringify({ ticket, room: route.room }),
    });
    if (!redemption.ok) return new Response("Unauthorized", { status: redemption.status === 503 ? 503 : 401 });
    const { token } = await redemption.json() as { token: string };
    // Never forward browser-supplied trusted identity headers.
    const headers = new Headers(request.headers);
    for (const name of [...headers.keys()]) {
      if (/^x-(collab|thinking)-/i.test(name)) headers.delete(name);
    }
    const internalUrl = new URL(request.url);
    internalUrl.pathname = `/parties/main/${route.room}`;
    internalUrl.searchParams.delete("ticket");
    internalUrl.searchParams.delete("token");
    // The legacy authorization helper consumes this private, server-only URL.
    internalUrl.searchParams.set("token", token);
    const internal = new Request(internalUrl, { headers });
    const ServerClass = route.kind === "note" ? NoteCollaborationServer : ThinkingSessionServer;
    const authorized = await ServerClass.onBeforeConnect(internal, { env });
    if (authorized instanceof Response) return authorized;
    // Remove bearer credentials from connection URI and request-line logs.
    internalUrl.searchParams.delete("token");
    const clean = new Request(internalUrl, { headers: authorized.headers });
    const namespace = route.kind === "note" ? env.NOTES : env.THINKING;
    const server = await getServerByName(namespace, route.room);
    console.log(JSON.stringify({ event: "connection_authorized", kind: route.kind }));
    return await server.fetch(clean);
  } catch {
    console.error(JSON.stringify({ event: "realtime_handshake_failed" }));
    return new Response("Service unavailable", { status: 503 });
  }
}

export default { fetch: handleRequest } satisfies ExportedHandler<RealtimeEnv>;
