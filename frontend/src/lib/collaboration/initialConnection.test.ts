import { afterEach, describe, expect, it, vi } from "vitest";
import { connectInitially } from "./initialConnection";

afterEach(() => vi.useRealTimers());
describe("initial collaboration connection", () => {
  it("recovers from a temporary first-ticket failure", async () => {
    vi.useFakeTimers();
    const connect = vi.fn().mockRejectedValueOnce({ status: 503 }).mockResolvedValue(undefined);
    const failure = vi.fn();
    const pending = connectInitially(connect, new AbortController().signal, failure);
    await vi.runAllTimersAsync(); await pending;
    expect(connect).toHaveBeenCalledTimes(2);
    expect(failure).not.toHaveBeenCalled();
  });
  it("does not retry a permanent denial", async () => {
    const connect = vi.fn().mockRejectedValue({ status: 403 });
    const failure = vi.fn();
    await connectInitially(connect, new AbortController().signal, failure);
    expect(connect).toHaveBeenCalledTimes(1);
    expect(failure).toHaveBeenCalledWith({ status: 403 });
  });
  it("cancels backoff when a user leaves the note", async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const connect = vi.fn().mockRejectedValue({ status: 503 });
    const pending = connectInitially(connect, controller.signal, vi.fn());
    await Promise.resolve(); controller.abort(); await pending;
    await vi.runAllTimersAsync();
    expect(connect).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });
});
