import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '@/lib/api';
import { useAsync } from '@/lib/useAsync';
import { Empty, ErrorNote, Panel } from '@/components/ui';
import { TagInput } from '@/components/TagInput';

/** Starting points, not defaults: nothing is applied until the user adds it. */
const SUGGEST = {
  stack: ['dotnet', 'c#', 'azure', 'react', 'typescript', 'python', 'kubernetes',
          'terraform', 'postgres', 'aws', 'node.js', 'go'],
  exclude: ['wordpress', 'drupal', 'sharepoint', 'salesforce', 'php', 'magento',
            'unity', 'sap'],
  excludeTitle: ['manager', 'sales', 'intern', 'recruiter', 'support', 'qa',
                 'designer', 'analyst'],
  boost: ['bicep', 'azure devops', 'fastapi', 'llm', 'rag', 'pgvector',
          'event sourcing', 'ci/cd'],
};

export default function SearchPage() {
  const filters = useAsync(() => api.searchFilters(), []);
  const [form, setForm] = useState<any>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);

  useEffect(() => { if (filters.data) setForm({ ...filters.data }); }, [filters.data]);

  if (!form) return <Empty>загрузка…</Empty>;

  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));

  /**
   * Save and apply in one action.
   *
   * Saving alone only changes what happens to postings collected from now on:
   * gates run once per posting and scores are cached. Splitting this into two
   * buttons produced exactly the confusion it deserved — tags changed, results
   * did not.
   */
  async function saveAndApply() {
    setApplying(true);
    setError(null);
    try {
      const saved = await api.saveSearchFilters({
        require_any: form.require_any,
        exclude: form.exclude,
        exclude_title: form.exclude_title,
        boost: form.boost,
        exclude_companies: form.exclude_companies,
        exclude_sources: form.exclude_sources,
        max_age_days: Number(form.max_age_days) || 0,
        min_priority: Number(form.min_priority) || 0,
        require_full_remote: !!form.require_full_remote,
        keywords_only: !!form.keywords_only,
      });
      setForm({ ...form, ...saved });

      setNote('Сохранено. Пересобираю очередь по новым тегам…');
      const res = await api.reapply((phase, processed) =>
        setNote(
          phase === 'gates'
            ? `Отбираю по фильтрам… ${processed}`
            : `Пересчитываю приоритеты… ${processed}`,
        ),
      );

      const dropped = Object.entries(res.counts)
        .filter(([code]) => code !== 'scored')
        .sort((a, b) => (b[1] as number) - (a[1] as number))
        .slice(0, 4)
        .map(([code, n]) => `${code} ${n}`)
        .join(', ');

      setNote(
        `Готово. Отсеяно: ${dropped || '—'}. ` +
        `Пересчитано вакансий: ${res.counts.scored ?? 0}. ` +
        'Очередь уже обновлена.',
      );
    } catch (e) {
      setError((e as Error).message);
      setNote(null);
    } finally {
      setApplying(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <ErrorNote error={error} />
      {note && (
        <div className="card border-accent/40 bg-accent/10 p-3 text-sm text-accent">{note}</div>
      )}

      <Panel title="Режим поиска">
        <div className="flex flex-col gap-3">
          <Toggle
            checked={!!form.keywords_only}
            onChange={(v) => set('keywords_only', v)}
            label="Искать только по ключевым словам"
            hint={
              'Совпадение считается по тегам ниже и больше ни по чему. ' +
              'Навыки, должности, грейд и зарплатный пол из профиля перестают ' +
              'влиять и на отбор, и на порядок. Выключите, если хотите вернуть ' +
              'подбор по профилю.'
            }
          />
          <Toggle
            checked={!!form.require_full_remote}
            onChange={(v) => set('require_full_remote', v)}
            label="Только полностью удалённые"
            hint={
              'Гибрид, офис и «X дней в неделю в офисе» отбрасываются. ' +
              'Вакансия, которая нигде не говорит про удалёнку, тоже ' +
              'отбрасывается: это осознанно строго, потому что молчание почти ' +
              'всегда означает офис. Ограничение по странам работает отдельно.'
            }
          />
        </div>
      </Panel>

      <Panel
        title="По каким словам искать"
        right={<Link to="/queue" className="label hover:text-accent">в очередь →</Link>}
      >
        {form.keywords_only && !(form.require_any ?? []).length && (
          <div className="card border-warn/40 bg-warn/10 p-3 text-sm text-warn mb-4">
            Режим «только по ключевым словам» включён, но слов нет. Пока список
            пуст, отбирать не по чему: в очередь попадает всё подряд, а порядок
            держится только на свежести и качестве источника.
          </div>
        )}
        <p className="text-sm text-muted mb-5">
          Эти теги определяют и что попадает в очередь, и как оно ранжируется.
          Регистр не важен. Совпадение идёт по границе слова, поэтому{' '}
          <code>go</code> не поймает «good», а <code>.net</code>, <code>c#</code>{' '}
          и <code>ci/cd</code> работают как написаны.
        </p>

        <div className="flex flex-col gap-5">
          <Field
            label="Обязательно хотя бы одно"
            hint="Главное поле. Вакансия отбрасывается, если не встретилось ни одного из этих слов. Чем больше слов из списка совпало, тем выше вакансия в выдаче."
          >
            <TagInput
              value={form.require_any} tone="accent"
              onChange={(v) => set('require_any', v)}
              placeholder="azure, dotnet, python…"
              suggestions={SUGGEST.stack}
            />
          </Field>

          <Field
            label="Исключить, если встречается"
            hint="Ищется в заголовке и в тексте вакансии."
          >
            <TagInput
              value={form.exclude} tone="stop"
              onChange={(v) => set('exclude', v)}
              placeholder="wordpress, sharepoint…"
              suggestions={SUGGEST.exclude}
            />
          </Field>

          <Field
            label="Исключить по заголовку"
            hint="Отдельно от предыдущего: «manager» внутри текста это норма, а в заголовке — не ваша вакансия."
          >
            <TagInput
              value={form.exclude_title} tone="stop"
              onChange={(v) => set('exclude_title', v)}
              placeholder="manager, sales, intern…"
              suggestions={SUGGEST.excludeTitle}
            />
          </Field>

          <Field
            label="Поднимать в выдаче"
            hint="Не отсеивает, а повышает приоритет: полезно для технологий, которые вам интересны, но не обязательны."
          >
            <TagInput
              value={form.boost} tone="accent"
              onChange={(v) => set('boost', v)}
              placeholder="bicep, llm, rag…"
              suggestions={SUGGEST.boost}
            />
          </Field>

          <Field label="Компании в игноре" hint="Совпадение по подстроке в названии.">
            <TagInput
              value={form.exclude_companies} tone="warn"
              onChange={(v) => set('exclude_companies', v)}
              placeholder="andela, turing…"
            />
          </Field>

          <Field
            label="Источники в игноре"
            hint="Источник продолжает собирать данные, но его вакансии не попадают в очередь."
          >
            <TagInput
              value={form.exclude_sources} tone="warn"
              onChange={(v) => set('exclude_sources', v)}
              placeholder="remoteok, jobicy…"
              suggestions={(form.available_sources ?? []).map((s: any) => s.key)}
            />
          </Field>

          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Максимальный возраст вакансии, дней" hint="0 — без ограничения.">
              <input
                type="number" min={0} max={365}
                className="bg-panel2 border border-line rounded-md px-2 py-1.5 text-sm w-full"
                value={form.max_age_days}
                onChange={(e) => set('max_age_days', e.target.value)}
              />
            </Field>
            <Field
              label="Минимальный приоритет"
              hint="Ниже этого значения вакансии не показываются и не уходят в Telegram. 0 — показывать всё."
            >
              <input
                type="number" min={0} max={1000}
                className="bg-panel2 border border-line rounded-md px-2 py-1.5 text-sm w-full"
                value={form.min_priority}
                onChange={(e) => set('min_priority', e.target.value)}
              />
            </Field>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3 mt-6">
          <button className="btn btn-accent" onClick={saveAndApply} disabled={applying}>
            {applying ? 'Применяю…' : 'Сохранить и применить'}
          </button>
          <span className="label">
            пересобирает уже собранные вакансии под новые теги, занимает до минуты
          </span>
        </div>
      </Panel>
    </div>
  );
}

function Toggle({ checked, onChange, label, hint }: {
  checked: boolean; onChange: (v: boolean) => void; label: string; hint: string;
}) {
  return (
    <label className="flex gap-3 items-start cursor-pointer">
      <input
        type="checkbox" checked={checked} className="mt-1"
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="flex flex-col gap-0.5">
        <span className="text-sm font-medium">{label}</span>
        <span className="text-xs text-muted">{hint}</span>
      </span>
    </label>
  );
}

function Field({ label, hint, children }: {
  label: string; hint?: string; children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="label">{label}</span>
      {hint && <span className="text-xs text-muted -mt-1">{hint}</span>}
      {children}
    </div>
  );
}
