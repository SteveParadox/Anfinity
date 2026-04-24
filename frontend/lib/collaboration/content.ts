import { getSchema, type JSONContent } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import { prosemirrorJSONToYDoc, yXmlFragmentToProsemirrorJSON } from "y-prosemirror";
import type { Doc } from "yjs";

const TIPTAP_EXTENSIONS = [StarterKit];
const TIPTAP_SCHEMA = getSchema(TIPTAP_EXTENSIONS);
const DEFAULT_COLLABORATION_FIELD = "default";

function normalizePlainText(text: string): string {
  return text.replace(/\r\n/g, "\n");
}

function flattenNodeText(node: JSONContent | null | undefined): string {
  if (!node) {
    return "";
  }

  if (node.type === "text") {
    return node.text ?? "";
  }

  if (node.type === "hardBreak") {
    return "\n";
  }

  const childText = (node.content ?? []).map((child) => flattenNodeText(child)).join("");

  if (node.type === "doc") {
    return (node.content ?? []).map((child) => flattenNodeText(child)).join("\n");
  }

  if (node.type === "bulletList" || node.type === "orderedList") {
    return (node.content ?? []).map((child) => flattenNodeText(child)).join("\n");
  }

  return childText;
}

export function plainTextToTiptapDocument(text: string): JSONContent {
  const normalizedText = normalizePlainText(text);
  const lines = normalizedText.split("\n");
  const content = lines.map<JSONContent>((line) => {
    if (!line) {
      return {
        type: "paragraph",
      };
    }

    return {
      type: "paragraph",
      content: [
        {
          type: "text",
          text: line,
        },
      ],
    };
  });

  return {
    type: "doc",
    content: content.length > 0 ? content : [{ type: "paragraph" }],
  };
}

export function createYDocFromPlainText(
  text: string,
  field = DEFAULT_COLLABORATION_FIELD,
): Doc | null {
  if (!text.trim()) {
    return null;
  }

  return prosemirrorJSONToYDoc(TIPTAP_SCHEMA, plainTextToTiptapDocument(text), field);
}

export function yDocToPlainText(
  doc: Doc,
  field = DEFAULT_COLLABORATION_FIELD,
): string {
  const json = yXmlFragmentToProsemirrorJSON(doc.getXmlFragment(field)) as JSONContent;
  return flattenNodeText(json)
    .replace(/\n{3,}/g, "\n\n")
    .trimEnd();
}
