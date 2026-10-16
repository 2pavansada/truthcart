import React from "react";
import type { ConstraintOpIn, ConstraintView } from "../api/types";

interface ActiveConstraintsBarProps {
  constraints: ConstraintView[];
  onRemove: (op: ConstraintOpIn) => void;
}

export const ActiveConstraintsBar: React.FC<ActiveConstraintsBarProps> = ({
  constraints,
  onRemove,
}) => {
  if (!constraints || constraints.length === 0) return null;

  return (
    <div className="constraints-bar" role="region" aria-label="Active Search Constraints">
      <span className="constraints-label">Active Filters:</span>
      {constraints.map((c, i) => (
        <span key={`${c.key}-${i}`} className="constraint-chip">
          <span>{c.label}</span>
          <button
            onClick={() => onRemove(c.op_to_remove)}
            title={`Remove ${c.label}`}
            aria-label={`Remove filter ${c.label}`}
          >
            ✕
          </button>
        </span>
      ))}
    </div>
  );
};
