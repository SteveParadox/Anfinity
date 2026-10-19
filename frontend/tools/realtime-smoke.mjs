import WebSocket from "ws";
const base = new URL(process.env.REALTIME_PUBLIC_URL);
if (base.protocol !== "https:" && base.hostname !== "localhost" && base.hostname !== "127.0.0.1") throw new Error("Use HTTPS for remote smoke tests");
const origin = process.env.REALTIME_ORIGIN;
if (!origin) throw new Error("REALTIME_ORIGIN required");
const health = await fetch(new URL("/health", base), { signal: AbortSignal.timeout(10000) });
if (!health.ok || (await health.json()).service !== "anfinity-realtime") throw new Error("Health check failed");
const missing = await fetch(new URL("/parties/main/unknown", base));
if (missing.status !== 404) throw new Error("Unknown room did not return 404");
if (process.env.REALTIME_SMOKE_BEARER && process.env.REALTIME_SMOKE_NOTE_ID) {
  const room = `note:${process.env.REALTIME_SMOKE_NOTE_ID}`;
  const response = await fetch(new URL("/realtime/ticket", process.env.REALTIME_BACKEND_URL), {
    method: "POST", headers: { Authorization: `Bearer ${process.env.REALTIME_SMOKE_BEARER}`, "Content-Type": "application/json" },
    body: JSON.stringify({ room }), signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) throw new Error("Smoke ticket issuance failed");
  const { ticket } = await response.json();
  const url = new URL(`/parties/main/${room}`, base);
  url.protocol = base.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("ticket", ticket);
  await new Promise((resolve, reject) => {
    const socket = new WebSocket(url, { headers: { Origin: origin } });
    const timer = setTimeout(() => { socket.terminate(); reject(new Error("WebSocket smoke timeout")); }, 10000);
    socket.once("message", () => { clearTimeout(timer); socket.close(); resolve(); });
    socket.once("error", () => { clearTimeout(timer); reject(new Error("WebSocket smoke handshake failed")); });
  });
  console.log("Health, routing and authenticated note WebSocket passed");
} else {
  console.log("Health and routing passed; authenticated WebSocket UNVERIFIED (smoke credentials absent)");
}
