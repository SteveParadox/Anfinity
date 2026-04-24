import { describe, expect, it } from "vitest";
import { createYDocFromPlainText, plainTextToTiptapDocument, yDocToPlainText } from "./content";

describe("collaboration content helpers", () => {
  it("creates a paragraph-based Tiptap document from plain text", () => {
    expect(plainTextToTiptapDocument("First line\n\nThird line")).toEqual({
      type: "doc",
      content: [
        {
          type: "paragraph",
          content: [{ type: "text", text: "First line" }],
        },
        {
          type: "paragraph",
        },
        {
          type: "paragraph",
          content: [{ type: "text", text: "Third line" }],
        },
      ],
    });
  });

  it("round-trips plain text through a Yjs document", () => {
    const doc = createYDocFromPlainText("Alpha\nBeta\n\nGamma");
    expect(doc).not.toBeNull();
    expect(yDocToPlainText(doc!)).toBe("Alpha\nBeta\n\nGamma");
  });

  it("does not create a seed document for empty content", () => {
    expect(createYDocFromPlainText("   ")).toBeNull();
  });
});
