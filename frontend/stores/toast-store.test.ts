import { act } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { toast, useToastStore } from "./toast-store";

afterEach(() => {
  useToastStore.setState({ toasts: [] });
  vi.useRealTimers();
});

describe("toast store", () => {
  it("pushes and auto-dismisses after the timeout", () => {
    vi.useFakeTimers();
    act(() => toast.success("Saved"));
    const [first] = useToastStore.getState().toasts;
    if (!first) throw new Error("expected a toast to have been pushed");
    expect(first).toMatchObject({ message: "Saved", variant: "success" });

    act(() => vi.advanceTimersByTime(4000));
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });

  it("dismisses by id", () => {
    act(() => toast.error("Boom"));
    const [first] = useToastStore.getState().toasts;
    if (!first) throw new Error("expected a toast to have been pushed");
    act(() => useToastStore.getState().dismiss(first.id));
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });

  it("stacks multiple toasts", () => {
    act(() => {
      toast.info("one");
      toast.info("two");
    });
    expect(useToastStore.getState().toasts).toHaveLength(2);
  });
});
