import { ReactNode } from 'react';

export function Panel({ title, right, children, className = '' }: {
  title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`card p-4 ${className}`}>
      {(title || right) && (
        <header className="flex items-baseline justify-between gap-3 mb-3">
          {title && <h2 className="label">{title}</h2>}
          {right}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({ value, label, hint, tone = 'default' }: {
  value: ReactNode; label: string; hint?: string;
  tone?: 'default' | 'accent' | 'warn' | 'stop';
}) {
  const color = {
    default: 'text-slate-100', accent: 'text-accent',
    warn: 'text-warn', stop: 'text-stop',
  }[tone];
  return (
    <div className="card p-4 flex flex-col gap-1">
      <div className={`text-3xl font-semibold tnum ${color}`}>{value}</div>
      <div className="text-sm text-slate-300">{label}</div>
      {hint && <div className="label">{hint}</div>}
    </div>
  );
}

export function Chip({ children, tone = 'muted' }: {
  children: ReactNode; tone?: 'muted' | 'accent' | 'warn' | 'stop';
}) {
  const cls = {
    muted: 'border-line text-muted',
    accent: 'border-accent/50 text-accent bg-accent/10',
    warn: 'border-warn/50 text-warn bg-warn/10',
    stop: 'border-stop/50 text-stop bg-stop/10',
  }[tone];
  return <span className={`chip ${cls}`}>{children}</span>;
}

export function Bar({ value, label }: { value: number; label: string }) {
  const width = Math.max(0, Math.min(100, value * 100));
  return (
    <div className="flex items-center gap-3">
      <span className="label w-32 shrink-0">{label}</span>
      <div className="h-1.5 flex-1 bg-line rounded overflow-hidden">
        <div className="h-full bg-accent" style={{ width: `${width}%` }} />
      </div>
      <span className="font-mono text-[11px] tnum w-10 text-right text-slate-300">
        {value.toFixed(2)}
      </span>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="text-sm text-muted py-8 text-center">{children}</p>;
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <div className="card border-stop/40 bg-stop/10 p-3 text-sm text-stop">{error}</div>
  );
}
