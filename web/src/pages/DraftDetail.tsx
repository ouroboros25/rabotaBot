import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '@/lib/api';
import { useAsync } from '@/lib/useAsync';
import { Chip, Empty, ErrorNote, Panel } from '@/components/ui';
import { TRACK_LABEL } from '@/lib/format';

const CHANNELS = [
  ['ats_form', 'форма ATS'], ['email', 'письмо'], ['upwork', 'Upwork'],
  ['referral', 'реферал'], ['platform_alert', 'платформа'],
];

const CHECK_LABEL: Record<string, string> = {
  factguard: 'обоснованность фактов',
  no_ledger_keys: 'нет служебных ключей в тексте',
  banlist: 'без штампов',
  length: 'длина',
  specificity: 'конкретность',
  jd_hook: 'зацепка из вакансии',
  novelty: 'непохожесть на прошлые',
};

export default function DraftDetail() {
  const { id } = useParams();
  const draftId = Number(id);
  const { data, error, loading, reload, setError } = useAsync(
    () => api.draft(draftId), [draftId],
  );
  const [body, setBody] = useState('');
  const [dirty, setDirty] = useState(false);
  const [channel, setChannel] = useState('ats_form');
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    if (data?.version) { setBody(data.version.body); setDirty(false); }
  }, [data?.version?.id]);

  if (loading && !data) return <Empty>загрузка…</Empty>;
  if (error && !data) return <ErrorNote error={error} />;
  if (!data) return null;

  async function act(fn: () => Promise<any>, message?: string) {
    try {
      const res = await fn();
      setNote(message ?? res?.note ?? 'готово');
      setError(null);
      reload();
    } catch (e) {
      setError((e as Error).message);
      setNote(null);
    }
  }

  const failed = data.checks.filter((c: any) => !c.passed);

  return (
    <div className="flex flex-col gap-4">
      <Link to="/drafts" className="label hover:text-accent w-fit">← черновики</Link>
      <ErrorNote error={error} />
      {note && (
        <div className="card border-accent/40 bg-accent/10 p-3 text-sm text-accent">{note}</div>
      )}

      <Panel>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold">{data.title ?? `черновик #${data.id}`}</h1>
            <p className="text-muted text-sm">{data.company ?? '—'}</p>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <Chip tone={data.status === 'approved' ? 'accent' : 'muted'}>{data.status}</Chip>
            <Chip>{TRACK_LABEL[data.track] ?? data.track}</Chip>
            <Chip>{data.template}</Chip>
            <Chip>версия {data.version.version}</Chip>
            {data.version.edited_by_human && <Chip tone="accent">правлено вручную</Chip>}
          </div>
        </div>
      </Panel>

      {failed.length > 0 && (
        <div className="card border-warn/40 bg-warn/10 p-4 text-sm">
          <strong className="text-warn">Черновик не прошёл проверки.</strong> Он показан
          именно потому, что решение ваше. Что не сошлось:
          <ul className="mt-2 flex flex-col gap-1">
            {failed.map((c: any) => (
              <li key={c.check} className="font-mono text-xs">
                {CHECK_LABEL[c.check] ?? c.check}: {JSON.stringify(c.detail)}
              </li>
            ))}
          </ul>
        </div>
      )}

      <Panel
        title={`Текст · ${body.split(/\s+/).filter(Boolean).length} слов`}
        right={
          data.version.subject ? (
            <span className="text-xs text-muted">тема: {data.version.subject}</span>
          ) : null
        }
      >
        <textarea
          className="w-full h-72 bg-panel2 border border-line rounded-md p-3
                     font-mono text-[13px] leading-relaxed resize-y"
          value={body}
          onChange={(e) => { setBody(e.target.value); setDirty(true); }}
        />
        <div className="flex flex-wrap gap-2 mt-3">
          <button
            className="btn"
            disabled={!dirty}
            onClick={() => act(
              () => api.editDraft(draftId, body),
              'Сохранено новой версией. Одобрение сброшено: токен привязан к точному тексту.',
            )}
          >
            Сохранить правку
          </button>
          <button
            className="btn btn-accent"
            disabled={dirty}
            onClick={() => act(() => api.approveDraft(draftId))}
          >
            Одобрить
          </button>
          <button
            className="btn"
            onClick={() => navigator.clipboard.writeText(body)}
          >
            Скопировать
          </button>
          <a className="btn" href={api.docxUrl(draftId)}>Скачать .docx</a>
          {data.apply_url && (
            <a className="btn" href={data.apply_url} target="_blank" rel="noreferrer noopener">
              Открыть форму
            </a>
          )}
          <button
            className="btn btn-stop ml-auto"
            onClick={() => act(() => api.discardDraft(draftId), 'Удалено')}
          >
            Удалить
          </button>
        </div>
        {dirty && (
          <p className="label mt-2 text-warn">
            есть несохранённые правки — сохраните, потом одобряйте
          </p>
        )}
      </Panel>

      <Panel title="Отправка">
        <p className="text-sm text-slate-300">
          Бот не отправляет ничего сам. Откройте форму, вставьте текст, отправьте
          сами, и только потом отметьте здесь. Отметка требует живого токена
          одобрения и создаёт запись в трекере с напоминанием на 4-5 день.
        </p>
        <div className="flex flex-wrap items-center gap-2 mt-3">
          <select
            className="bg-panel2 border border-line rounded-md px-2 py-1.5 text-sm"
            value={channel}
            onChange={(e) => setChannel(e.target.value)}
          >
            {CHANNELS.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
          <button
            className="btn btn-accent"
            disabled={data.status !== 'approved'}
            onClick={() => act(() => api.markSent(draftId, channel), 'Записано в трекер')}
          >
            Я отправил
          </button>
          {data.status !== 'approved' && (
            <span className="label">сначала одобрите</span>
          )}
        </div>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Проверки">
          <ul className="flex flex-col gap-2 text-sm">
            {data.checks.map((c: any) => (
              <li key={c.check} className="flex items-start gap-2">
                <Chip tone={c.passed ? 'accent' : 'warn'}>{c.passed ? 'ok' : 'нет'}</Chip>
                <div>
                  <div>{CHECK_LABEL[c.check] ?? c.check}</div>
                  {!c.passed && (
                    <div className="font-mono text-[11px] text-muted mt-0.5">
                      {JSON.stringify(c.detail)}
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel title="Основания">
          <p className="text-sm">
            <span className="label block mb-1">факты из реестра</span>
            {data.version.fact_keys?.join(', ') || '—'}
          </p>
          {data.version.jd_hooks?.length > 0 && (
            <p className="text-sm mt-3">
              <span className="label block mb-1">зацепки из вакансии</span>
              {data.version.jd_hooks.join(' · ')}
            </p>
          )}
          {data.version.open_questions?.length > 0 && (
            <div className="text-sm mt-3">
              <span className="label block mb-1">о чём стоит спросить</span>
              <ul className="list-disc list-inside">
                {data.version.open_questions.map((q: string, i: number) => <li key={i}>{q}</li>)}
              </ul>
            </div>
          )}
          {data.form_questions?.length > 0 && (
            <div className="text-sm mt-3">
              <span className="label block mb-1">вопросы формы</span>
              <ul className="list-disc list-inside">
                {data.form_questions.map((q: any, i: number) => (
                  <li key={i}>{typeof q === 'string' ? q : q.question ?? JSON.stringify(q)}</li>
                ))}
              </ul>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
