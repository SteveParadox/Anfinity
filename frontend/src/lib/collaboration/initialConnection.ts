export function isPermanentConnectionFailure(error: unknown): boolean {
  const status = (error as { status?: number } | null)?.status;
  return status === 401 || status === 403 || status === 404;
}

function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const finish = () => { clearTimeout(timer); signal.removeEventListener("abort", finish); resolve(); };
    const timer = setTimeout(finish, ms);
    signal.addEventListener("abort", finish, { once: true });
    if (signal.aborted) finish();
  });
}

/** The provider does not schedule a retry when its first ticket request fails. */
export async function connectInitially(connect: () => Promise<void>, signal: AbortSignal, onFailure: (error: unknown) => void): Promise<void> {
  for (let attempt = 0; attempt < 12 && !signal.aborted; attempt++) {
    try { await connect(); return; }
    catch (error) {
      if (signal.aborted) return;
      if (isPermanentConnectionFailure(error) || attempt === 11) { onFailure(error); return; }
      await pause(Math.min(100 * 2 ** attempt, 5000) + Math.random() * 250, signal);
    }
  }
}
