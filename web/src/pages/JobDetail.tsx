import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '@/lib/api';
import { useAsync } from '@/lib/useAsync';
import { Bar, Chip, Empty, ErrorNote, Panel } from '@/components/ui';
import { GATE_LABEL, TRACK_LABEL, dt, money } from '@/lib/format';

export default function JobDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const clusterId = Number(id);
  const { data, error, loading, reload, setError } = useAsync(
    () => api.job(clusterId), [clusterId],
  );

  if (loading && !data) return <Empty>загрузка…</Empty>;
  if (error) return <ErrorNote error={error} />;
  if (!data) return null;

  const { posting, score } = data;

  async function draft() {
    try {
      const res = await api.draftJob(clusterId);
      navigate(`/drafts/${res.draft_id}`);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Link to="/queue" className="label hover:text-accent w-fit">← очередь</Link>

      <Panel>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold">{posting.title}</h1>
            <p className="text-muted mt-1">
              {posting.company_name ?? 'компания не указана'} ·{' '}
              {money(posting.comp_min, posting.comp_max, posting.comp_currency)}
              {posting.comp_period ? ` / ${posting.comp_period}` : ''}
            </p>
            <div className="flex flex-wrap gap-1.5 mt-3">
              <Chip tone="accent">{TRACK_LABEL[score?.track] ?? score?.track}</Chip>
              <Chip>{posting.remote_policy}</Chip>
              {posting.seniority && <Chip>{posting.seniority}</Chip>}
              {posting.employment_type && <Chip>{posting.employment_type}</Chip>}
              <Chip>опубликовано {dt(posting.first_published_at)}</Chip>
              {data.fan_out > 1 && (
                <Chip tone={data.fan_out >= 4 ? 'warn' : 'muted'}>
                  на {data.fan_out} площадках
                </Chip>
              )}
            </div>
          </div>
          <div className="flex flex-col gap-2">
            <button className="btn btn-accent" onClick={draft}>Сделать черновик</button>
            {posting.apply_url && (
              <a className="btn text-center" href={posting.apply_url}
                 target="_blank" rel="noreferrer noopener">
                Открыть вакансию
              </a>
            )}
          </div>
        </div>
      </Panel>

      {score?.injection_suspected && (
        <div className="card border-stop/40 bg-stop/10 p-4 text-sm">
          <strong className="text-stop">В тексте вакансии есть указания, адресованные модели.</strong>{' '}
          Позиция понижена в рейтинге автоматически. Прочитайте описание глазами,
          прежде чем что-то отправлять.
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {score && (
          <Panel title={`Оценка · приоритет ${Math.round(score.priority)}`}>
            <div className="flex flex-col gap-2">
              <Bar value={score.fit} label="fit" />
              <Bar value={score.trust} label="trust" />
              <Bar value={score.reach} label="reach" />
              <Bar value={score.value} label="value" />
              <hr className="border-line my-2" />
              <Bar value={score.semantic} label="семантика" />
              <Bar value={score.skill_coverage} label="покрытие навыков" />
              <Bar value={score.title_fit} label="совпадение тайтла" />
              <Bar value={score.freshness} label="свежесть" />
              <Bar value={score.comp_fit} label="деньги" />
              <Bar value={score.source_prior} label="приор источника" />
              <Bar value={score.ghost_risk} label="риск фейка" />
              <Bar value={score.crowding} label="толпа" />
            </div>
            {score.matched_skills?.length > 0 && (
              <p className="text-xs text-muted mt-3">
                совпало: {score.matched_skills.join(', ')}
              </p>
            )}
            {score.llm_fit != null && (
              <p className="text-xs text-muted mt-2">
                судья: {score.llm_fit}/10 · модель {score.llm_model ?? '—'}
              </p>
            )}
          </Panel>
        )}

        <div className="flex flex-col gap-4">
          {score?.evidence?.length > 0 && (
            <Panel title="Доказательства из текста вакансии">
              <ul className="flex flex-col gap-3">
                {score.evidence.map((item: any, i: number) => (
                  <li key={i} className="text-sm">
                    <div className="text-slate-200">{item.claim}</div>
                    <blockquote className="text-muted border-l-2 border-line pl-3 mt-1 italic">
                      «{item.quote}»
                    </blockquote>
                  </li>
                ))}
              </ul>
              <p className="label mt-3">
                каждая цитата проверена на наличие в тексте; выдуманные отброшены
              </p>
            </Panel>
          )}

          {score?.verdict?.risks?.length > 0 && (
            <Panel title="Риски">
              <ul className="list-disc list-inside text-sm text-warn flex flex-col gap-1">
                {score.verdict.risks.map((r: string, i: number) => <li key={i}>{r}</li>)}
              </ul>
            </Panel>
          )}

          {data.gates.length > 0 && (
            <Panel title="Сработавшие фильтры">
              <ul className="flex flex-col gap-2 text-sm">
                {data.gates.map((g: any, i: number) => (
                  <li key={i}>
                    <Chip tone="warn">{GATE_LABEL[g.code] ?? g.code}</Chip>
                    {g.span && (
                      <span className="text-muted text-xs ml-2 font-mono">…{g.span}…</span>
                    )}
                  </li>
                ))}
              </ul>
            </Panel>
          )}

          <Panel title={`Найдено на источниках (${data.sources.length})`}>
            <ul className="text-sm flex flex-col gap-1">
              {data.sources.map((s: any, i: number) => (
                <li key={i} className="flex justify-between gap-3">
                  <span className="font-mono text-[12px]">{s.source}</span>
                  <span className="text-muted">{dt(s.posted_at)}</span>
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      </div>

      <Panel title="Текст вакансии">
        {/* Rendered as plain text on purpose: this content is third-party and
            untrusted, so it never becomes HTML. */}
        <div className="jd max-h-[32rem] overflow-y-auto">{posting.body_text || '—'}</div>
      </Panel>
    </div>
  );
}
