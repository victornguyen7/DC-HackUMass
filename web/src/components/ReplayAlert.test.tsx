import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReplayAlert, type LiveAlert } from "./ReplayAlert";

afterEach(cleanup);
const queue: LiveAlert = { id: "queue:grill", station: "grill", kind: "queue", title: "Grill has a long line", message: "20 students are waiting." };
it("shows relevant alerts during playback and keeps a dismissed condition quiet", () => {
  const onInspect = vi.fn();
  const { rerender } = render(<ReplayAlert alerts={[queue]} time={1092} playing={false} onInspect={onInspect} />);
  expect(screen.queryByLabelText("Simulation alert")).toBeNull();
  rerender(<ReplayAlert alerts={[queue]} time={1093} playing onInspect={onInspect} />);
  expect(screen.getByText(queue.title)).toBeTruthy();
  fireEvent.click(screen.getByLabelText("Dismiss simulation alert"));
  rerender(<ReplayAlert alerts={[queue]} time={1110} playing onInspect={onInspect} />);
  expect(screen.queryByText(queue.title)).toBeNull();
  rerender(<ReplayAlert alerts={[]} time={1111} playing onInspect={onInspect} />);
  rerender(<ReplayAlert alerts={[queue]} time={1112} playing onInspect={onInspect} />);
  fireEvent.click(screen.getByRole("button", { name: /View station/ }));
  expect(onInspect).toHaveBeenCalledWith("grill");
});
