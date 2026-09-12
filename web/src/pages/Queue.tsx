import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '@/lib/api';
import { useAsync } from '@/lib/useAsync';
import { Chip, Empty, ErrorNote, Panel } from '@/components/ui';
import { TRACK_LABEL, age, money } from '@/lib/format';

const TRACKS = ['', 'fte', 'outstaff', 'freelance', 'equity'];

export default function Queue() {
  const [track, setTrack] = useState('');
  const [busy, setBusy] = useState<number | null>(null);
  const { data, error, loading, reload, setError } = useAsync(
    () => api.jobs({ limit: 60, ...(track ? { track } : {}) }),
    [track],
  );

  async function draft(id: number) {
    setBusy(id);
    try {
      await api.draftJob(id);
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function skip(id: number) {
    const reason = window.prompt(
      'Причина: wrong_stack / geo / comp / too_junior / too_senior / company / gut',
      'gut',
    );
    if (!reason) return;
    setBusy(id);
    try {
      await api.skipJob(id, reason);
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <ErrorNote error={error} />
      <Panel
        title="Очередь"
        right={
          <div className="flex gap-1">
            {TRACKS.map((t) => (
              <button
                key={t || 'all'}
                onClick={() => setTrack(t)}
                className={`px-2 py-1 rounded text-xs ${
                  track === t ? 'bg-accent/15 text-accent' : 'text-muted hover:bg-panel2'
                }`}
              >
                {t ? TRACK_LABEL[t] : 'все'}
              </button>
            ))}
          </div>
        }
      >
        {loading && !data ? (
          <Empty>загрузка…</Empty>
        ) : !data?.items?.length ? (
          <Empty>пусто: либо всё обработано, либо сбор ещё не прошёл</Empty>
        ) : (
          <ul className="flex flex-col divide-y divide-line">
            {data.items.map((job: any) => (
              <li key={job.cluster_id} className="py-3 flex flex-wrap gap-3 items-start">
                <div className="w-14 shrink-0 text-right">
                  <div className="text-lg font-semibold tnum text-accent">
                    {Math.round(job.priority)}
                  </div>
                  {job.llm_fit != null && (
                    <div className="label">{job.llm_fit}/10</div>
                  )}
                </div>

                <div className="flex-1 min-w-[16rem]">
                  <Link
                    to={`/queue/${job.cluster_id}`}
                    className="font-medium hover:text-accent transition-colors"
                  >
                    {job.title}
                  </Link>
                  <div className="text-sm text-muted mt-0.5">
                    {job.company ?? 'компания не указана'} · {money(job.comp_min, job.comp_max, job.comp_currency)}
                  </div>
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    <Chip>{TRACK_LABEL[job.track] ?? job.track}</Chip>
                    <Chip>{job.source}</Chip>
                    <Chip>{age(job.posted_at)}</Chip>
                    {job.fan_out >= 4 && <Chip tone="warn">на {job.fan_out} площадках</Chip>}
                    {job.ghost_risk >= 0.3 && (
                      <Chip tone="warn">риск фейка {Math.round(job.ghost_risk * 100)}%</Chip>
                    )}
                    {job.injection_suspected && <Chip tone="stop">инъекция в тексте</Chip>}
                    {job.status === 'drafted' && <Chip tone="accent">черновик есть</Chip>}
                  </div>
                </div>

                <div className="flex gap-2">
                  <button
                    className="btn btn-accent"
                    disabled={busy === job.cluster_id}
                    onClick={() => draft(job.cluster_id)}
                  >
                    {busy === job.cluster_id ? '…' : 'Черновик'}
                  </button>
                  <button
                    className="btn"
                    disabled={busy === job.cluster_id}
                    onClick={() => skip(job.cluster_id)}
                  >
                    Пропустить
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
