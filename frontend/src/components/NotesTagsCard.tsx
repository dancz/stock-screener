import React, { useEffect, useState } from "react";
import { Plus, Save, StickyNote, X } from "lucide-react";
import { useScreenerStore } from "../store/useScreenerStore";

export const NotesTagsCard: React.FC = () => {
  const detail = useScreenerStore((s) => s.selectedDetail);
  const saveNotes = useScreenerStore((s) => s.saveNotes);

  const [noteText, setNoteText] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [savedNotice, setSavedNotice] = useState(false);

  useEffect(() => {
    if (detail) {
      setNoteText(detail.note_text || "");
      setTags(detail.tags || []);
      setSavedNotice(false);
    }
  }, [detail?.symbol]);

  if (!detail) return null;

  const persist = async (nextText: string, nextTags: string[]) => {
    setSaving(true);
    try {
      await saveNotes(detail.symbol, nextText, nextTags);
      setSavedNotice(true);
      setTimeout(() => setSavedNotice(false), 1800);
    } finally {
      setSaving(false);
    }
  };

  const addTag = async (e: React.FormEvent) => {
    e.preventDefault();
    const t = tagInput.trim();
    if (!t) return;
    if (!tags.includes(t)) {
      const nextTags = [...tags, t];
      setTags(nextTags);
      setTagInput("");
      await persist(noteText, nextTags);
    } else {
      setTagInput("");
    }
  };

  const removeTag = async (tag: string) => {
    const nextTags = tags.filter((t) => t !== tag);
    setTags(nextTags);
    await persist(noteText, nextTags);
  };

  return (
    <section className="rounded-xl border border-border bg-panel p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <StickyNote className="h-4 w-4 text-sky-400" />
          <h2 className="text-sm font-bold text-white">Notes & Custom Tags ({detail.symbol})</h2>
        </div>
        <button
          type="button"
          onClick={() => persist(noteText, tags)}
          disabled={saving}
          className="inline-flex items-center gap-1 rounded bg-sky-600 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-sky-500 disabled:opacity-50"
        >
          <Save className="h-3 w-3" />
          <span>{savedNotice ? "Saved" : saving ? "Saving..." : "Save Note"}</span>
        </button>
      </div>

      {/* Tags */}
      <div className="flex flex-wrap items-center gap-1.5">
        {tags.map((tag) => (
          <span
            key={tag}
            className="inline-flex items-center gap-1 rounded-full border border-sky-500/30 bg-sky-500/10 px-2.5 py-0.5 text-[11px] font-medium text-sky-300"
          >
            {tag}
            <button type="button" onClick={() => removeTag(tag)} className="hover:text-white">
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
        <form onSubmit={addTag} className="inline-flex items-center gap-1">
          <input
            type="text"
            value={tagInput}
            onChange={(e) => setTagInput(e.target.value)}
            placeholder="+ add tag"
            className="w-24 rounded border border-border bg-canvas px-2 py-0.5 text-[11px] text-slate-200 placeholder-slate-500 focus:w-32 focus:border-sky-500 focus:outline-none transition-all"
          />
          <button type="submit" className="rounded bg-card p-1 text-slate-300 hover:text-white">
            <Plus className="h-3 w-3" />
          </button>
        </form>
      </div>

      <textarea
        value={noteText}
        onChange={(e) => setNoteText(e.target.value)}
        rows={3}
        placeholder="Write entry thesis, catalysts, invalidation notes..."
        className="w-full rounded-lg border border-border bg-canvas p-2.5 text-xs text-slate-100 placeholder-slate-500 focus:border-sky-500 focus:outline-none"
      />
    </section>
  );
};
