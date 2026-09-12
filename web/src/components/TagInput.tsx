import { KeyboardEvent, useState } from 'react';

/**
 * Chip editor for a list of terms.
 *
 * Enter or comma commits, Backspace on an empty field removes the last chip,
 * and pasting a comma- or newline-separated list adds all of it at once, which
 * is how anyone actually moves a stack list in from somewhere else.
 */
export function TagInput({
  value,
  onChange,
  placeholder,
  tone = 'muted',
  suggestions = [],
}: {
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  tone?: 'muted' | 'accent' | 'warn' | 'stop';
  suggestions?: string[];
}) {
  const [draft, setDraft] = useState('');

  const chipCls = {
    muted: 'border-line text-slate-300 bg-panel2',
    accent: 'border-accent/50 text-accent bg-accent/10',
    warn: 'border-warn/50 text-warn bg-warn/10',
    stop: 'border-stop/50 text-stop bg-stop/10',
  }[tone];

  function add(raw: string) {
    const parts = raw
      .split(/[,\n;]/)
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean);
    if (!parts.length) return;
    const next = [...value];
    for (const part of parts) if (!next.includes(part)) next.push(part);
    onChange(next);
    setDraft('');
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      add(draft);
    } else if (e.key === 'Backspace' && !draft && value.length) {
      onChange(value.slice(0, -1));
    }
  }

  const unused = suggestions.filter((s) => !value.includes(s)).slice(0, 8);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-1.5 items-center bg-panel2 border border-line rounded-md p-2 min-h-[2.6rem]">
        {value.map((tag) => (
          <span
            key={tag}
            className={`chip ${chipCls} normal-case tracking-normal text-[11px]`}
          >
            {tag}
            <button
              type="button"
              aria-label={`убрать ${tag}`}
              className="opacity-60 hover:opacity-100 ml-0.5"
              onClick={() => onChange(value.filter((t) => t !== tag))}
            >
              ×
            </button>
          </span>
        ))}
        <input
          className="flex-1 min-w-[8rem] bg-transparent outline-none text-sm py-0.5"
          value={draft}
          placeholder={value.length ? '' : placeholder}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          onBlur={() => add(draft)}
          onPaste={(e) => {
            const text = e.clipboardData.getData('text');
            if (/[,\n;]/.test(text)) {
              e.preventDefault();
              add(text);
            }
          }}
        />
      </div>
      {unused.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {unused.map((s) => (
            <button
              key={s}
              type="button"
              className="text-[11px] px-1.5 py-0.5 rounded border border-line text-muted hover:text-accent hover:border-accent/50"
              onClick={() => add(s)}
            >
              + {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
