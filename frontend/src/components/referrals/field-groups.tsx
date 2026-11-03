"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { FieldStatusBadge } from "@/components/referrals/field-status";
import { fieldValueType, groupFields, PRIORITY_OPTIONS } from "@/lib/catalog";
import { formatClinicalDate } from "@/lib/format";
import type { HighlightTarget } from "@/components/referrals/document-viewer";
import type { ReferralField } from "@/lib/api/types";

export function FieldGroups({
  fields,
  readOnly,
  saving,
  onSave,
  onJump,
}: {
  fields: ReferralField[];
  readOnly: boolean;
  saving: boolean;
  onSave: (key: string, value: string) => void;
  onJump: (target: HighlightTarget, quote: string | null) => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  return (
    <div className="space-y-6">
      {groupFields(fields).map((group) => (
        <section key={group.id} aria-labelledby={`group-${group.id}`}>
          <h3 id={`group-${group.id}`} className="mb-2 text-base font-semibold leading-6">
            {group.label}
          </h3>
          <div className="divide-y divide-border rounded-panel border border-border bg-surface">
            {group.fields.map((field) => {
              const unreadable = field.readability === "unreadable";
              const missing = !field.effective_value;
              const shown =
                unreadable || missing
                  ? null
                  : fieldValueType(field.key) === "date"
                    ? formatClinicalDate(field.effective_value ?? "")
                    : field.effective_value;
              return (
                <div key={field.key} id={`field-${field.key}`} className="space-y-2 px-3 py-2">
                  <div className="grid grid-cols-[minmax(0,1fr)_auto_auto_auto] items-center gap-3">
                    <div>
                      <p className="text-xs font-medium leading-4 text-text-muted">{field.label}</p>
                      <p className={shown ? "text-sm" : "text-sm italic text-text-muted"}>{shown ?? (unreadable ? "" : "Not found")}</p>
                    </div>
                    <FieldStatusBadge status={field.field_status} edited={field.edited} readability={field.readability} />
                    {field.source ? (
                      <button
                        type="button"
                        className="text-sm text-primary"
                        onClick={() =>
                          onJump(
                            { page: field.source!.page, quote: field.ocr_value, bbox: field.source!.bbox },
                            field.ocr_value,
                          )
                        }
                      >
                        p.{field.source.page}
                      </button>
                    ) : (
                      <span className="text-sm text-text-muted">—</span>
                    )}
                    {readOnly ? null : (
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => {
                          setEditing(field.key);
                          setDraft(field.effective_value ?? "");
                        }}
                      >
                        {missing ? "Add" : "Edit"}
                      </Button>
                    )}
                  </div>
                  {field.readability === "verify" ? (
                    <p className="text-xs text-attention">Verify against the document.</p>
                  ) : null}
                  {field.readability === "unreadable" ? (
                    <p className="text-xs text-danger">Unreadable. This value was left blank.</p>
                  ) : null}
                  {field.ocr_value ? <p className="text-sm text-text-muted">Evidence: {field.ocr_value}</p> : null}
                  {field.edited && field.suggested_value ? (
                    <p className="text-sm text-text-muted">Extracted value: {field.suggested_value}</p>
                  ) : null}
                  {editing === field.key ? (
                    <form
                      className="flex flex-wrap items-end gap-2"
                      onSubmit={(event) => {
                        event.preventDefault();
                        onSave(field.key, draft);
                        setEditing(null);
                      }}
                    >
                      {fieldValueType(field.key) === "multiline" ? (
                        <Textarea value={draft} onChange={(event) => setDraft(event.target.value)} aria-label={field.label} />
                      ) : field.key === "referral.sender_priority" ? (
                        <select
                          className="h-9 rounded-control border border-border-control bg-surface px-2"
                          value={draft}
                          aria-label={field.label}
                          onChange={(event) => setDraft(event.target.value)}
                        >
                          <option value="">Choose</option>
                          {PRIORITY_OPTIONS.map(([value, label]) => (
                            <option key={value} value={value}>
                              {label}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <Input
                          type={fieldValueType(field.key) === "date" ? "date" : "text"}
                          value={draft}
                          aria-label={field.label}
                          onChange={(event) => setDraft(event.target.value)}
                        />
                      )}
                      <Button type="submit" disabled={saving || !draft.trim()}>
                        Save
                      </Button>
                      <Button type="button" variant="outline" onClick={() => setEditing(null)}>
                        Cancel
                      </Button>
                      {field.edited && field.suggested_value ? (
                        <Button
                          type="button"
                          variant="outline"
                          onClick={() => {
                            onSave(field.key, field.suggested_value ?? "");
                            setEditing(null);
                          }}
                        >
                          Revert to extracted
                        </Button>
                      ) : null}
                    </form>
                  ) : null}
                </div>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
