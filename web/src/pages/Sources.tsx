import { useState } from 'react';
import { api } from '@/lib/api';
import { useAsync } from '@/lib/useAsync';
import { Chip, Empty, ErrorNote, Panel } from '@/components/ui';
import { dt, pct } from '@/lib/format';

export default function Sources() {
  const { data, error, loading, reload, setError } = useAsync(() => api.sources(), []);
  const [busy, setBusy] = useState<number | null>(null);

  async function toggle(id: number, enabled: boolean) {
    setBusy(id);
    try { await api.patchSource(id, { enabled }); reload(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(null); }
  }

  async function runNow(id: number) {
    setBusy(id);
    try {
      const res = await api.runSource(id);
      setError(res.error ? `ошибка: ${res.error}` : null);
      reload();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(null); }
  }

  const tierTone = (tier: number) =>
    tier === 0 ? 'accent' : tier === 1 ? 'accent' : tier === 2 ? 'warn' : 'stop';

  return (
    <div className="flex flex-col gap-4">
      <ErrorNote error={error} />
      <Panel title="Источники">
        {loading && !data ? (
          <Empty>загрузка…</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm min-w-[780px]">
              <thead>
                <tr className="text-left label border-b border-line">
                  <th className="py-2">источник</th>
                  <th className="py-2">семейство</th>
                  <th className="py-2">риск</th>
                  <th className="py-2 text-right">вакансий</th>
                  <th className="py-2 text-right">приор ответа</th>
                  <th className="py-2">последний прогон</th>
                  <th className="py-2"></th>
                </tr>
              </thead>
              <tbody>
                {data?.map((s) => (
                  <tr key={s.id} className="border-b border-line/50">
                    <td className="py-2">
                      <span className="font-mono text-[12px]">{s.key}</span>
                      {s.auto_discovered && (
                        <Chip tone="muted">найден автоматически</Chip>
                      )}
                      {s.disabled_reason && (
                        <div className="text-xs text-stop">{s.disabled_reason}</div>
                      )}
                    </td>
                    <td className="py-2 text-muted text-xs">{s.family}</td>
                    <td className="py-2">
                      <Chip tone={tierTone(s.legal_tier) as any}>tier {s.legal_tier}</Chip>
                    </td>
                    <td className="py-2 text-right tnum">{s.postings}</td>
                    <td className="py-2 text-right tnum">
                      {s.priors?.length
                        ? pct(Math.max(...s.priors.map((p: any) => p.reply_rate)), 1)
                        : '—'}
                    </td>
                    <td className="py-2 text-xs">
                      {s.last_run ? (
                        <>
                          <span className="text-muted">{dt(s.last_run.started_at)}</span>
                          <span className="ml-2 tnum">
                            {s.last_run.items_seen} / +{s.last_run.items_new}
                          </span>
                          {s.last_run.error && (
                            <div className="text-stop">{s.last_run.error.slice(0, 70)}</div>
                          )}
                          {s.last_run.drift && (
                            <div className="text-warn">{s.last_run.drift.slice(0, 70)}</div>
                          )}
                        </>
                      ) : '—'}
                    </td>
                    <td className="py-2 text-right whitespace-nowrap">
                      <button
                        className="btn text-xs px-2 py-1 mr-1"
                        disabled={busy === s.id}
                        onClick={() => runNow(s.id)}
                      >
                        прогнать
                      </button>
                      <button
                        className={`btn text-xs px-2 py-1 ${s.enabled ? '' : 'btn-accent'}`}
                        disabled={busy === s.id}
                        onClick={() => toggle(s.id, !s.enabled)}
                      >
                        {s.enabled ? 'выключить' : 'включить'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="text-xs text-muted mt-4 flex flex-col gap-1">
          <span>tier 0 — публичный API площадки: без авторизации, без трения с ToS, без риска бана</span>
          <span>tier 1 — ваш собственный почтовый ящик или сессия через штатный канал площадки</span>
          <span>tier 2 — публичный HTML, разрешённый robots.txt, всегда без логина</span>
          <span>tier 4 не реализован конструктивно: LinkedIn, скрейпинг UI, обход капч, прокси</span>
        </div>
      </Panel>
    </div>
  );
}
