import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { useAsync } from '@/lib/useAsync';
import { Chip, Empty, ErrorNote, Panel } from '@/components/ui';
import { TRACK_LABEL } from '@/lib/format';

export default function ProfilePage() {
  const { data, error, loading, reload, setError } = useAsync(() => api.profile(), []);
  const [form, setForm] = useState<any>(null);
  const [newFact, setNewFact] = useState({ key: '', type: 'metric', claim: '', verifiable_by: '' });
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => { if (data) setForm({ ...data }); }, [data]);

  if (loading && !data) return <Empty>загрузка…</Empty>;
  if (error && !data) return <ErrorNote error={error} />;
  if (!form) return null;

  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));

  async function save() {
    try {
      await api.saveProfile({
        display_name: form.display_name,
        timezone: form.timezone,
        countries_eligible: form.countries_eligible,
        min_overlap_hours: Number(form.min_overlap_hours),
        entity_status: form.entity_status,
        eor_ready: form.eor_ready,
        rate_floor_hourly: num(form.rate_floor_hourly),
        rate_target_hourly: num(form.rate_target_hourly),
        comp_floor_annual: num(form.comp_floor_annual),
        comp_currency: form.comp_currency,
        weekly_send_cap: Number(form.weekly_send_cap),
      });
      setNote('Сохранено'); setError(null); reload();
    } catch (e) { setError((e as Error).message); }
  }

  async function addFact() {
    if (!newFact.key.trim() || !newFact.claim.trim()) {
      setError('нужны ключ и формулировка факта');
      return;
    }
    try {
      await api.createFact({
        key: newFact.key.trim(),
        type: newFact.type,
        payload: { claim: newFact.claim.trim() },
        verifiable_by: newFact.verifiable_by.trim() || null,
      });
      setNewFact({ key: '', type: 'metric', claim: '', verifiable_by: '' });
      setNote('Факт добавлен'); setError(null); reload();
    } catch (e) { setError((e as Error).message); }
  }

  async function removeFact(id: number) {
    if (!window.confirm('Удалить факт? Черновики больше не смогут на него ссылаться.')) return;
    try { await api.deleteFact(id); reload(); }
    catch (e) { setError((e as Error).message); }
  }

  const health = data!.ledger_health;

  return (
    <div className="flex flex-col gap-4">
      <ErrorNote error={error} />
      {note && (
        <div className="card border-accent/40 bg-accent/10 p-3 text-sm text-accent">{note}</div>
      )}

      {!health.has_metrics && (
        <div className="card border-warn/40 bg-warn/10 p-4 text-sm">
          <strong className="text-warn">В реестре нет ни одного измеримого результата.</strong>{' '}
          Черновик не может назвать число, которого нет в реестре: такое число
          считается выдумкой и отклоняется. Пока метрик нет, письма будут
          обтекаемыми. Добавьте два-три факта вида «сократил p95 с 820мс до 190мс».
        </div>
      )}

      <Panel title="Профиль">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Имя" value={form.display_name} onChange={(v) => set('display_name', v)} />
          <Field label="Таймзона" value={form.timezone} onChange={(v) => set('timezone', v)} />
          <Field
            label="Страны (через запятую)"
            value={(form.countries_eligible ?? []).join(', ')}
            onChange={(v) => set('countries_eligible',
              v.split(',').map((s) => s.trim().toUpperCase()).filter(Boolean))}
          />
          <Field label="Мин. пересечение, ч" value={form.min_overlap_hours}
                 onChange={(v) => set('min_overlap_hours', v)} />
          <Field label="Ставка, пол ($/ч)" value={form.rate_floor_hourly ?? ''}
                 onChange={(v) => set('rate_floor_hourly', v)} />
          <Field label="Ставка, цель ($/ч)" value={form.rate_target_hourly ?? ''}
                 onChange={(v) => set('rate_target_hourly', v)} />
          <Field label="Годовой пол" value={form.comp_floor_annual ?? ''}
                 onChange={(v) => set('comp_floor_annual', v)} />
          <Field label="Валюта" value={form.comp_currency}
                 onChange={(v) => set('comp_currency', v)} />
          <Field label="Лимит отправок в неделю" value={form.weekly_send_cap}
                 onChange={(v) => set('weekly_send_cap', v)} />
        </div>
        <div className="flex items-center gap-3 mt-4">
          <button className="btn btn-accent" onClick={save}>Сохранить</button>
          <span className="label">
            лимит отправок намеренно низкий: рост объёма измеримо снижает конверсию
          </span>
        </div>
      </Panel>

      <Panel
        title={`Реестр фактов · ${health.fact_count} активных, метрик ${health.metric_count}`}
      >
        <p className="text-sm text-muted mb-3">
          Генератор может только выбирать, упорядочивать и переформулировать эти
          факты. Ввести число, компанию или инструмент, которых здесь нет, он не
          может — это проверяется кодом после генерации.
        </p>
        <ul className="divide-y divide-line">
          {data!.facts.map((f: any) => (
            <li key={f.id} className="py-2 flex flex-wrap items-start gap-3">
              <Chip tone={f.active ? 'accent' : 'muted'}>{f.key}</Chip>
              <Chip>{f.type}</Chip>
              <div className="flex-1 min-w-[14rem] text-sm">
                <div>{f.payload?.claim ?? JSON.stringify(f.payload)}</div>
                {f.verifiable_by && (
                  <div className="label mt-0.5">подтверждается: {f.verifiable_by}</div>
                )}
              </div>
              <button className="btn btn-stop text-xs px-2 py-1"
                      onClick={() => removeFact(f.id)}>
                удалить
              </button>
            </li>
          ))}
        </ul>

        <div className="mt-4 grid gap-2 sm:grid-cols-4">
          <Field label="Ключ (F030)" value={newFact.key}
                 onChange={(v) => setNewFact({ ...newFact, key: v })} />
          <div className="flex flex-col gap-1">
            <span className="label">Тип</span>
            <select
              className="bg-panel2 border border-line rounded-md px-2 py-1.5 text-sm"
              value={newFact.type}
              onChange={(e) => setNewFact({ ...newFact, type: e.target.value })}
            >
              {['metric', 'role', 'artifact', 'legal', 'identity', 'skill'].map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </div>
          <Field label="Формулировка" value={newFact.claim}
                 onChange={(v) => setNewFact({ ...newFact, claim: v })} />
          <Field label="Чем подтверждается" value={newFact.verifiable_by}
                 onChange={(v) => setNewFact({ ...newFact, verifiable_by: v })} />
        </div>
        <button className="btn btn-accent mt-3" onClick={addFact}>Добавить факт</button>
      </Panel>

      <Panel title="Позиционирование по трекам">
        <div className="grid gap-3 md:grid-cols-2">
          {data!.variants.map((v: any) => (
            <div key={v.id} className="bg-panel2 rounded-md p-3">
              <div className="flex items-center gap-2 mb-2">
                <Chip tone="accent">{TRACK_LABEL[v.track] ?? v.track}</Chip>
                {!v.enabled && <Chip tone="muted">выключен</Chip>}
              </div>
              <div className="text-sm">{v.headline}</div>
              <div className="label mt-2">цели</div>
              <div className="text-xs text-muted">{v.target_titles?.join(' · ')}</div>
              <div className="label mt-2">обязательно</div>
              <div className="text-xs text-accent">{v.must_have_skills?.join(', ') || '—'}</div>
              <div className="label mt-2">исключить</div>
              <div className="text-xs text-stop">{v.exclude_skills?.join(', ') || '—'}</div>
            </div>
          ))}
        </div>
        <p className="label mt-3">
          правится в config/profile.example.yaml при первом развёртывании или через API
        </p>
      </Panel>
    </div>
  );
}

function Field({ label, value, onChange }: {
  label: string; value: any; onChange: (v: string) => void;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="label">{label}</span>
      <input
        className="bg-panel2 border border-line rounded-md px-2 py-1.5 text-sm"
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

function num(v: any): number | null {
  if (v === '' || v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}
