import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Badge, Card, CardContent, CardTitle } from "./card";

describe("Card", () => {
  it("renders its content and title", () => {
    render(
      <Card>
        <CardTitle>Subscriptions</CardTitle>
        <CardContent>Premier League</CardContent>
      </Card>,
    );
    expect(screen.getByRole("heading", { name: "Subscriptions" })).toBeInTheDocument();
    expect(screen.getByText("Premier League")).toBeInTheDocument();
  });

  it("applies the variant style to a badge", () => {
    render(<Badge variant="success">active</Badge>);
    const badge = screen.getByText("active");
    expect(badge.className).toContain("emerald");
  });
});
