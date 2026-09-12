import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '@/lib/api';
import { useAsync } from '@/lib/useAsync';
import { Chip, Empty, ErrorNote, Panel } from '@/components/ui';
import { TRACK_LABEL, age, money } from '@/lib/format';

const TRACKS = ['', 'fte', 'outstaff', 'freelance', 'equity'];
const AGES = [
  { value: 0, label: 'любой возраст' },
  { value: 3, label: '3 дня' },
  { value: 7, label: 'неделя' },
  { value: 14, label: '2 недели' },
  { value: 30, label: 'месяц' },
];
const SKIP_REASONS = [
  ['wrong_stack', 'не тот стек'], ['geo', 'география'], ['comp', 'деньги'],
  ['too_junior', 'слишком джун'], ['too_senior', 'слишком сеньор'],
  ['company', 'компания'], ['gut', 'просто нет'],
];

const PAGE = 40;

export default function Queue() {
  const [track, setTrack] = useState('');
  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  const [source, setSource] = useState('');
  const [maxAge, setMaxAge] = useState(0);
  const [minPriority, setMinPriority] = useState(0);
  const [hasComp, setHasComp] = useState(false);
  const [remoteOnly, setRemoteOnly] = useState(false);
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState<number | null>(null);
  const [skipFor, setSkipFor] = useState<number | null>(null);

  // Typing should not fire a query per keystroke.
  useEffect(() => {
    const t = setTimeout(() => { setDebouncedQ(q); setOffset(0); }, 300);
    return () => clearTimeout(t);
  }, [q]);

  const facets = useAsync(() => api.facets(), []);
  const params = useMemo(() => ({
    limit: PAGE, offset, track, q: debouncedQ, source,
    max_age_days: maxAge, min_priority: minPriority,
    has_comp: hasComp, remote_only: remoteOnly,
  }), [offset, track, debouncedQ, source, maxAge, minPriority, hasComp, remoteOnly]);

  const { data, error, loading, reload, setError } = useAsync(
    () => api.jobs(params), [params],
  );

  const resetPage = (fn: () => void) => { fn(); setOffset(0); };

  async function draft(id: number) {
    setBusy(id);
    try { await api.draftJob(id); reload(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(null); }
  }

  async function skip(id: number, reason: string) {
    setBusy(id); setSkipFor(null);
    try { await api.skipJob(id, reason); reload(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(null); }
  }

  const filtersActive =
    !!debouncedQ || !!source || !!track || maxAge > 0 || minPriority > 0 || hasComp || remoteOnly;

  function clearAll() {
    setQ(''); setDebouncedQ(''); setSource(''); setTrack('');
    setMaxAge(0); setMinPriority(0); setHasComp(false); setRemoteOnly(false);
    setOffset(0);
  }

  return (
    <div className="flex flex-col gap-4">
      <ErrorNote error={error} />

      <Panel title="Фильтры очереди" right={
        <Link to="/search" className="label hover:text-accent">
          постоянные правила поиска →
        </Link>
      }>
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap gap-2">
            <input
              className="bg-panel2 border border-line rounded-md px-3 py-1.5 text-sm flex-1 min-w-[14rem]"
              placeholder="поиск по должности или компании"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <select
              className="bg-panel2 border border-line rounded-md px-2 py-1.5 text-sm"
              value={source}
              onChange={(e) => resetPage(() => setSource(e.target.value))}
            >
              <option value="">все источники</option>
              {(facets.data?.sources ?? []).map((s: any) => (
                <option key={s.key} value={s.key}>{s.key} ({s.count})</option>
              ))}
            </select>
            <select
              className="bg-panel2 border border-line rounded-md px-2 py-1.5 text-sm"
              value={maxAge}
              onChange={(e) => resetPage(() => setMaxAge(Number(e.target.value)))}
            >
              {AGES.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
            </select>
            <label className="flex items-center gap-1.5 text-sm text-slate-300 px-2">
              приоритет от
              <input
                type="number" min={0} step={10}
                className="bg-panel2 border border-line rounded-md px-2 py-1.5 text-sm w-20"
                value={minPriority}
                onChange={(e) => resetPage(() => setMinPriority(Number(e.target.value) || 0))}
              />
            </label>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="flex gap-1">
              {TRACKS.map((t) => (
                <button
                  key={t || 'all'}
                  onClick={() => resetPage(() => setTrack(t))}
                  className={`px-2 py-1 rounded text-xs ${
                    track === t ? 'bg-accent/15 text-accent' : 'text-muted hover:bg-panel2'
                  }`}
                >
                  {t ? TRACK_LABEL[t] : 'все треки'}
                </button>
              ))}
            </div>
            <label className="flex items-center gap-1.5 text-sm text-slate-300 ml-2">
              <input type="checkbox" checked={hasComp}
                     onChange={(e) => resetPage(() => setHasComp(e.target.checked))} />
              только с вилкой
            </label>
            <label className="flex items-center gap-1.5 text-sm text-slate-300">
              <input type="checkbox" checked={remoteOnly}
                     onChange={(e) => resetPage(() => setRemoteOnly(e.target.checked))} />
              без гибрида и офиса
            </label>
            {filtersActive && (
              <button className="btn text-xs px-2 py-1 ml-auto" onClick={clearAll}>
                сбросить
              </button>
            )}
          </div>
        </div>
      </Panel>

      <Panel
        title={`Очередь${data?.total != null ? ` · ${data.total}` : ''}`}
        right={
          data?.total > PAGE ? (
            <span className="flex items-center gap-2">
              <button className="btn text-xs px-2 py-1" disabled={offset === 0}
                      onClick={() => setOffset(Math.max(0, offset - PAGE))}>←</button>
              <span className="label">
                {offset + 1}–{Math.min(offset + PAGE, data.total)}
              </span>
              <button className="btn text-xs px-2 py-1"
                      disabled={offset + PAGE >= data.total}
                      onClick={() => setOffset(offset + PAGE)}>→</button>
            </span>
          ) : null
        }
      >
        {loading && !data ? (
          <Empty>загрузка…</Empty>
        ) : !data?.items?.length ? (
          <Empty>
            {filtersActive
              ? 'под эти фильтры ничего не подошло — попробуйте ослабить их'
              : 'пусто: либо всё обработано, либо сбор ещё не прошёл'}
          </Empty>
        ) : (
          <ul className="flex flex-col divide-y divide-line">
            {data.items.map((job: any) => (
              <li key={job.cluster_id} className="py-3 flex flex-wrap gap-3 items-start">
                <div className="w-14 shrink-0 text-right">
                  <div className="text-lg font-semibold tnum text-accent">
                    {Math.round(job.priority)}
                  </div>
                  {job.llm_fit != null && <div className="label">{job.llm_fit}/10</div>}
                </div>

                <div className="flex-1 min-w-[16rem]">
                  <Link to={`/queue/${job.cluster_id}`}
                        className="font-medium hover:text-accent transition-colors">
                    {job.title}
                  </Link>
                  <div className="text-sm text-muted mt-0.5">
                    {job.company ?? 'компания не указана'} ·{' '}
                    {money(job.comp_min, job.comp_max, job.comp_currency)}
                  </div>
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    <Chip>{TRACK_LABEL[job.track] ?? job.track}</Chip>
                    <Chip>{job.source}</Chip>
                    <Chip>{age(job.posted_at)}</Chip>
                    {job.boost_hits?.length > 0 && (
                      <Chip tone="accent">+{job.boost_hits.join(', ')}</Chip>
                    )}
                    {job.fan_out >= 4 && <Chip tone="warn">на {job.fan_out} площадках</Chip>}
                    {job.ghost_risk >= 0.3 && (
                      <Chip tone="warn">риск фейка {Math.round(job.ghost_risk * 100)}%</Chip>
                    )}
                    {job.injection_suspected && <Chip tone="stop">инъекция в тексте</Chip>}
                    {job.status === 'drafted' && <Chip tone="accent">черновик есть</Chip>}
                  </div>
                </div>

                <div className="flex flex-col gap-1.5 items-end">
                  <div className="flex gap-2">
                    <button className="btn btn-accent" disabled={busy === job.cluster_id}
                            onClick={() => draft(job.cluster_id)}>
                      {busy === job.cluster_id ? '…' : 'Черновик'}
                    </button>
                    <button className="btn" disabled={busy === job.cluster_id}
                            onClick={() => setSkipFor(
                              skipFor === job.cluster_id ? null : job.cluster_id)}>
                      Пропустить
                    </button>
                  </div>
                  {skipFor === job.cluster_id && (
                    <div className="flex flex-wrap gap-1 justify-end max-w-[20rem]">
                      {SKIP_REASONS.map(([code, label]) => (
                        <button key={code} className="btn text-xs px-2 py-0.5"
                                onClick={() => skip(job.cluster_id, code)}>
                          {label}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
        {skipFor !== null && (
          <p className="label mt-3">
            причина попадает в обучение: три одинаковые подряд станут предложением нового фильтра
          </p>
        )}
      </Panel>
    </div>
  );
}
