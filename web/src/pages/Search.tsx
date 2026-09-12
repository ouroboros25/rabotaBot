import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { useAsync } from '@/lib/useAsync';
import { Chip, Empty, ErrorNote, Panel } from '@/components/ui';
import { TagInput } from '@/components/TagInput';
import { TRACK_LABEL } from '@/lib/format';

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
  const profile = useAsync(() => api.profile(), []);
  const [form, setForm] = useState<any>(null);
  const [variants, setVariants] = useState<any[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { if (filters.data) setForm({ ...filters.data }); }, [filters.data]);
  useEffect(() => { if (profile.data) setVariants(profile.data.variants.map((v: any) => ({ ...v }))); },
    [profile.data]);

  if (!form) return <Empty>загрузка…</Empty>;

  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));

  async function saveFilters() {
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
      });
      setForm({ ...form, ...saved });
      setError(null);
      setNote(
        'Сохранено. Фильтры применяются на этапе жёсткого отсева, но к уже ' +
        'проверенным вакансиям задним числом не применяются: нажмите ' +
        '«Перепроверить всё», если хотите пересчитать корпус.',
      );
    } catch (e) { setError((e as Error).message); setNote(null); }
  }

  async function saveVariant(v: any) {
    try {
      await api.saveVariant(v.id, {
        track: v.track,
        headline: v.headline,
        target_titles: v.target_titles,
        must_have_skills: v.must_have_skills,
        nice_to_have_skills: v.nice_to_have_skills,
        exclude_skills: v.exclude_skills,
        enabled: v.enabled,
      });
      setError(null);
      setNote(`Трек «${TRACK_LABEL[v.track] ?? v.track}» сохранён.`);
    } catch (e) { setError((e as Error).message); setNote(null); }
  }

  async function rescan() {
    if (!window.confirm(
      'Перепроверить весь корпус по новым правилам? Это сбросит отметки фильтров ' +
      'и прогонит все собранные вакансии заново. Займёт минуту.',
    )) return;
    try {
      const res = await api.rescan();
      setError(null);
      setNote(`Перепроверено вакансий: ${res.processed}. Пересчёт очереди пойдёт по расписанию.`);
    } catch (e) { setError((e as Error).message); setNote(null); }
  }

  const patchVariant = (id: number, key: string, value: any) =>
    setVariants((list) => list.map((v) => (v.id === id ? { ...v, [key]: value } : v)));

  return (
    <div className="flex flex-col gap-4">
      <ErrorNote error={error} />
      {note && (
        <div className="card border-accent/40 bg-accent/10 p-3 text-sm text-accent">{note}</div>
      )}

      <Panel
        title="Ключевые слова"
        right={<span className="label">применяются до скоринга, бесплатно</span>}
      >
        <p className="text-sm text-muted mb-4">
          Эти правила срабатывают на этапе жёсткого отсева, то есть отклонённая
          вакансия не тратит ни эмбеддинга, ни вызова модели. Регистр не важен.
          Совпадение идёт по границе слова, поэтому <code>go</code> не поймает
          «good», а <code>.net</code> и <code>c#</code> работают как есть.
        </p>

        <div className="flex flex-col gap-5">
          <Field
            label="Обязательно хотя бы одно"
            hint="Вакансия отбрасывается, если не встретилось ни одного из этих слов. Пусто = требования нет."
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
            hint={`Не фильтр, а надбавка: +${(form.boost_per_term ?? 0.06).toFixed(2)} за слово, но не больше +${(form.boost_cap ?? 0.24).toFixed(2)} суммарно, чтобы длинный список не перебил реальное совпадение.`}
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
            <Field label="Максимальный возраст, дней" hint="0 — без ограничения.">
              <input
                type="number" min={0} max={365}
                className="bg-panel2 border border-line rounded-md px-2 py-1.5 text-sm w-full"
                value={form.max_age_days}
                onChange={(e) => set('max_age_days', e.target.value)}
              />
            </Field>
            <Field
              label="Минимальный приоритет в дайджесте"
              hint="Ниже этого значения вакансии не показываются и не уходят в Telegram."
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

        <div className="flex flex-wrap items-center gap-3 mt-5">
          <button className="btn btn-accent" onClick={saveFilters}>Сохранить фильтры</button>
          <button className="btn" onClick={rescan}>Перепроверить весь корпус</button>
          <span className="label">
            новые правила действуют на всё, что придёт дальше; для уже собранного нужна перепроверка
          </span>
        </div>
      </Panel>

      <Panel title="Технологии и должности по трекам">
        <p className="text-sm text-muted mb-4">
          Это ядро совпадения: обязательные навыки дают основной вес в покрытии,
          желательные считаются с коэффициентом, а исключённые отбрасывают
          вакансию целиком. Должности сравниваются нечётко, поэтому «Senior
          Backend Engineer» поймает и «Sr. Back-end Developer».
        </p>
        <div className="flex flex-col gap-4">
          {variants.map((v) => (
            <div key={v.id} className="bg-panel2 rounded-md p-4 flex flex-col gap-4">
              <div className="flex flex-wrap items-center gap-2">
                <Chip tone="accent">{TRACK_LABEL[v.track] ?? v.track}</Chip>
                <input
                  className="flex-1 min-w-[16rem] bg-panel border border-line rounded-md px-2 py-1.5 text-sm"
                  value={v.headline}
                  onChange={(e) => patchVariant(v.id, 'headline', e.target.value)}
                  placeholder="как вы себя позиционируете на этом треке"
                />
                <label className="flex items-center gap-1.5 text-sm text-muted">
                  <input
                    type="checkbox" checked={v.enabled}
                    onChange={(e) => patchVariant(v.id, 'enabled', e.target.checked)}
                  />
                  включён
                </label>
              </div>

              <Field label="Целевые должности">
                <TagInput
                  value={v.target_titles}
                  onChange={(x) => patchVariant(v.id, 'target_titles', x)}
                  placeholder="senior software engineer…"
                />
              </Field>
              <div className="grid gap-4 md:grid-cols-3">
                <Field label="Обязательные навыки">
                  <TagInput
                    value={v.must_have_skills} tone="accent"
                    onChange={(x) => patchVariant(v.id, 'must_have_skills', x)}
                    placeholder="dotnet, azure…"
                    suggestions={SUGGEST.stack}
                  />
                </Field>
                <Field label="Желательные">
                  <TagInput
                    value={v.nice_to_have_skills}
                    onChange={(x) => patchVariant(v.id, 'nice_to_have_skills', x)}
                    placeholder="react, terraform…"
                    suggestions={SUGGEST.stack}
                  />
                </Field>
                <Field label="Исключающие">
                  <TagInput
                    value={v.exclude_skills} tone="stop"
                    onChange={(x) => patchVariant(v.id, 'exclude_skills', x)}
                    placeholder="php, wordpress…"
                    suggestions={SUGGEST.exclude}
                  />
                </Field>
              </div>
              <div>
                <button className="btn btn-accent" onClick={() => saveVariant(v)}>
                  Сохранить трек
                </button>
              </div>
            </div>
          ))}
        </div>
      </Panel>
    </div>
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
