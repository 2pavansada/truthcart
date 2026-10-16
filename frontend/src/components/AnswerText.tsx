import React from "react";

interface AnswerTextProps {
  text: string;
  onOpenEvidence: (kind: "product" | "policy" | "search", id: string) => void;
}

export const AnswerText: React.FC<AnswerTextProps> = ({ text, onOpenEvidence }) => {
  if (!text) return null;

  // Regex to match citation brackets like [SH-BLK-001] or [policy:1.1] or [search:s_1] or comma-separated tokens
  const bracketRegex = /\[(.*?)\]/g;

  const elements: React.ReactNode[] = [];
  let lastIndex = 0;
  let match;

  while ((match = bracketRegex.exec(text)) !== null) {
    // Text before the bracket
    if (match.index > lastIndex) {
      elements.push(text.substring(lastIndex, match.index));
    }

    const inside = match[1];
    const tokens = inside.split(",").map((t) => t.trim());

    tokens.forEach((token, idx) => {
      let kind: "product" | "policy" | "search" = "product";
      let display = token;
      let targetId = token;

      if (token.startsWith("policy:")) {
        kind = "policy";
        targetId = token.substring(7);
        display = `§ ${targetId}`;
      } else if (token.startsWith("search:")) {
        kind = "search";
        targetId = token.substring(7);
        display = `search`;
      }

      elements.push(
        <button
          key={`chip-${match.index}-${idx}`}
          className="citation-chip"
          onClick={() => onOpenEvidence(kind, targetId)}
          title={`Click to inspect evidence for ${token}`}
        >
          {display}
        </button>
      );
    });

    lastIndex = bracketRegex.lastIndex;
  }

  if (lastIndex < text.length) {
    elements.push(text.substring(lastIndex));
  }

  return <div className="answer-text">{elements}</div>;
};
