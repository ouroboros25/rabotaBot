import { api } from '@/lib/api';
import { useAsync } from '@/lib/useAsync';
import { Chip, Empty, ErrorNote, Panel, Stat } from '@/components/ui';
import { GATE_LABEL, STAGE_LABEL, dt, pct } from '@/lib/format';

export default function Dashboard() {
  const { data, error, loading } = useAsync(() => api.dashboard(), []);
  const health = useAsync(() => api.health(), []);
  const activity = useAsync(() => api.activity(), []);

  if (loading && !data) return <Empty>загрузка…</Empty>;
  if (error) return <ErrorNote error={error} />;
  if (!data) return null;

  const gw = health.data?.llm_gateway;
  const funnel = data.pipeline;

  return (
    <div className="flex flex-col gap-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          value={data.queue_size}
          label="в очереди после отсева"
          hint={`из ${funnel.clusters} уникальных`}
        />
        <Stat
          value={`${data.sends.remaining}/${data.sends.cap}`}
          label="осталось отправок на неделе"
          hint="лимит это фича, не ограничение"
          tone={data.sends.remaining === 0 ? 'warn' : 'accent'}
        />
        <Stat
          value={data.replies.reply_rate === null ? '—' : pct(data.replies.reply_rate, 1)}
          label="доля ответов"
          hint={`${data.replies.total_replied} из ${data.replies.total_sent} · рынок 2-3%`}
          tone={
            data.replies.reply_rate === null ? 'default'
              : data.replies.reply_rate >= 0.08 ? 'accent'
              : data.replies.reply_rate < 0.02 ? 'stop' : 'warn'
          }
        />
        <Stat
          value={`${data.llm_calls_today.used}/${data.llm_calls_today.cap}`}
          label="вызовов LLM сегодня"
          hint={gw?.reachable ? 'гейтвей доступен' : 'гейтвей недоступен'}
          tone={gw?.reachable ? 'default' : 'stop'}
        />
      </div>

      <Panel title="Воронка">
        <div className="grid gap-2 sm:grid-cols-5 text-center">
          {[
            ['вакансий собрано', funnel.postings],
            ['уникальных после дедупа', funnel.clusters],
            ['отсеяно фильтрами', funnel.gated_postings],
            ['оценено', funnel.scored],
            ['проверено судьёй', funnel.judged],
          ].map(([label, value]) => (
            <div key={label as string} className="bg-panel2 rounded-md p-3">
              <div className="text-xl font-semibold tnum">{value as number}</div>
              <div className="label mt-1">{label as string}</div>
            </div>
          ))}
        </div>
        <p className="text-xs text-muted mt-3">
          Дедуп схлопывает одну вакансию, висящую на нескольких площадках. Широкий
          охват агрегаторами понижает приоритет: это признак толпы, а не качества.
        </p>
      </Panel>

      <div className="grid gap-6 lg:grid-cols-2">
        <Panel title="Почему отсеивалось">
          {Object.keys(data.gate_counts).length === 0 ? (
            <Empty>пока ничего не отсеяно</Empty>
          ) : (
            <ul className="flex flex-col gap-2">
              {Object.entries(data.gate_counts).map(([code, count]) => (
                <li key={code} className="flex items-center gap-3 text-sm">
                  <span className="w-40 shrink-0 text-slate-300">
                    {GATE_LABEL[code] ?? code}
                  </span>
                  <div className="h-1.5 flex-1 bg-line rounded overflow-hidden">
                    <div
                      className="h-full bg-warn"
                      style={{
                        width: `${Math.min(100,
                          ((count as number) /
                            Math.max(...Object.values(data.gate_counts) as number[])) * 100)}%`,
                      }}
                    />
                  </div>
                  <span className="font-mono text-[11px] tnum w-12 text-right">
                    {count as number}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="Отклики по стадиям">
          {Object.keys(data.stage_counts).length === 0 ? (
            <Empty>ещё ничего не отправлено</Empty>
          ) : (
            <div className="flex flex-wrap gap-2">
              {Object.entries(data.stage_counts).map(([stage, count]) => (
                <Chip key={stage} tone={stage === 'ghosted' ? 'stop' : 'accent'}>
                  {STAGE_LABEL[stage] ?? stage}: {count as number}
                </Chip>
              ))}
            </div>
          )}
          <div className="mt-4 flex flex-wrap gap-2">
            <Chip>{data.pending_drafts} открытых черновиков</Chip>
            <Chip tone={data.sources.failing ? 'warn' : 'muted'}>
              источники: {data.sources.enabled}/{data.sources.total}
              {data.sources.failing ? ` · ${data.sources.failing} с ошибками` : ''}
            </Chip>
          </div>
        </Panel>
      </div>

      <Panel title="Последние прогоны источников">
        {!activity.data?.length ? (
          <Empty>прогонов ещё не было</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm min-w-[640px]">
              <thead>
                <tr className="text-left label border-b border-line">
                  <th className="py-2">источник</th>
                  <th className="py-2">когда</th>
                  <th className="py-2 text-right">найдено</th>
                  <th className="py-2 text-right">новых</th>
                  <th className="py-2">заметки</th>
                </tr>
              </thead>
              <tbody>
                {activity.data.slice(0, 12).map((run, i) => (
                  <tr key={i} className="border-b border-line/50">
                    <td className="py-2 font-mono text-[12px]">{run.source}</td>
                    <td className="py-2 text-muted">{dt(run.started_at)}</td>
                    <td className="py-2 text-right tnum">{run.items_seen}</td>
                    <td className="py-2 text-right tnum text-accent">{run.items_new}</td>
                    <td className="py-2 text-xs">
                      {run.error && <span className="text-stop">{run.error}</span>}
                      {run.drift && <span className="text-warn">{run.drift}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
