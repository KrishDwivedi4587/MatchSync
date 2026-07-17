import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import HomePage from "./page";

// Foundation smoke test: the landing page renders and shadcn Button works.
describe("HomePage", () => {
  it("renders the product name", () => {
    render(<HomePage />);
    expect(screen.getByRole("heading", { name: "MatchSync" })).toBeInTheDocument();
  });

  it("renders a call-to-action linking to login", () => {
    render(<HomePage />);
    const cta = screen.getByRole("link", { name: "Get started" });
    expect(cta).toBeInTheDocument();
    expect(cta).toHaveAttribute("href", "/login");
  });
});
