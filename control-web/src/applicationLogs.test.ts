import { test, expect } from "vitest";
import { parseRule, logQuery } from "./logEventsModel";
test("specified values are trimmed and deduplicated, grey percentage is validated", () => {
  expect(
    parseRule({
      id: "r",
      name: "test",
      kind: "specified",
      attribute: "model",
      valuesText: "Pixel 8\nPixel 8, Xiaomi 14",
      percentage: 10,
      enabled: true,
    }).values,
  ).toEqual(["Pixel 8", "Xiaomi 14"]);
  expect(() =>
    parseRule({
      id: "r",
      name: "test",
      kind: "percentage",
      attribute: "device",
      valuesText: "",
      percentage: 101,
      enabled: true,
    }),
  ).toThrow();
});
test("log filters encode IDs without URL injection", () => {
  expect(logQuery({ user_id: "a&level=ERROR" }, 123)).toBe(
    "limit=100&user_id=a%26level%3DERROR&before=123",
  );
});
