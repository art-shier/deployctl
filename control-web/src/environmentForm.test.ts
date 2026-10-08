import { describe, it, expect } from "vitest";
import {
  draftRows,
  changes,
  editRow,
  toggleRemoval,
  inheritedRows,
  overrideVariable,
} from "./environmentForm";
describe("secret draft protocol", () => {
  it("removing an override restores the inherited value without sending it", () => {
    const inherited = [
      { key: "TEXT", secret: false, configured: true, value: "group" },
    ];
    const own = draftRows([{ ...inherited[0], value: "project" }]);
    expect(inheritedRows(own, inherited)).toEqual([]);
    const removed = toggleRemoval(own, 0);
    expect(inheritedRows(removed, inherited)).toEqual(inherited);
    expect(changes(removed)).toEqual([{ key: "TEXT", operation: "remove" }]);
  });
  it("requires entering an inherited secret override instead of using a mask", () => {
    expect(
      overrideVariable([], { key: "PASSWORD", secret: true, configured: true }),
    ).toEqual([{ key: "PASSWORD", value: "", secret: true, operation: "set" }]);
  });
  it("restores the edited operation after undoing deletion", () => {
    let rows = draftRows([
      { key: "TEXT", secret: false, configured: true, value: "old" },
    ]);
    rows = editRow(rows, 0, { value: "new", operation: "set" });
    rows = toggleRemoval(rows, 0);
    expect(changes(rows)).toEqual([{ key: "TEXT", operation: "remove" }]);
    rows = toggleRemoval(rows, 0);
    expect(changes(rows)).toEqual([
      { key: "TEXT", operation: "set", secret: false, value: "new" },
    ]);
  });
  it("preserves secret without sending placeholders", () => {
    const rows = draftRows([
      { key: "PASSWORD", secret: true, configured: true },
    ]);
    expect(changes(rows)).toEqual([
      { key: "PASSWORD", operation: "keep", secret: true },
    ]);
  });
  it("distinguishes empty string and removal", () => {
    let rows = draftRows([{ key: "PASSWORD", secret: true, configured: true }]);
    rows = editRow(rows, 0, { operation: "set", value: "" });
    expect(changes(rows)[0]).toEqual({
      key: "PASSWORD",
      operation: "set",
      secret: true,
      value: "",
    });
    rows = editRow(rows, 0, { operation: "remove" });
    expect(changes(rows)).toEqual([{ key: "PASSWORD", operation: "remove" }]);
  });
  it("keeps existing nonsecret values and rejects duplicate keys", () => {
    const rows = draftRows([
      { key: "TEXT", secret: false, configured: true, value: " $ = " },
    ]);
    expect(changes(rows)[0].operation).toBe("keep");
    expect(() => changes([...rows, ...rows])).toThrow("重复");
  });
});
