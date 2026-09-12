import { api } from '@/lib/api';
import { useAsync } from '@/lib/useAsync';
import { Chip, Empty, ErrorNote, Panel } from '@/components/ui';
import { STAGE_LABEL, TRACK_LABEL, dt } from '@/lib/format';

const NEXT_STAGES = [
  'acknowledged', 'screen', 'tech', 'final', 'offer', 'accepted',
  'rejected', 'ghosted', 'withdrawn',
];

const TONE: Record<string, 'accent' | 'warn' | 'stop' | 'muted'> = {
  offer: 'accent', accepted: 'accent', screen: 'accent', tech: 'accent',
  final: 'accent', sent: 'muted', acknowledged: 'muted',
  rejected: 'stop', ghosted: 'stop', knocked_out: 'stop', withdrawn: 'warn',
};

export default function Applications() {
  const { data, error, loading, reload, setError } = useAsync(
    () => api.applications(), [],
  );

  async function advance(id: number, stage: string) {
    try {
      await api.advance(id, stage);
      reload();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const overdue = (row: any) =>
    !row.replied && row.follow_up_due_at && new Date(row.follow_up_due_at) < new Date();

  return (
    <div className="flex flex-col gap-4">
      <ErrorNote error={error} />
      <Panel title="Отклики">
        {loading && !data ? (
          <Empty>загрузка…</Empty>
        ) : !data?.length ? (
          <Empty>откликов пока нет</Empty>
        ) : (
          <ul className="divide-y divide-line">
            {data.map((row) => (
              <li key={row.id} className="py-3 flex flex-wrap gap-3 items-start">
                <div className="flex-1 min-w-[16rem]">
                  <div className="font-medium">{row.title ?? `отклик #${row.id}`}</div>
                  <div className="text-sm text-muted">{row.company ?? '—'}</div>
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    <Chip tone={TONE[row.stage] ?? 'muted'}>
                      {STAGE_LABEL[row.stage] ?? row.stage}
                    </Chip>
                    <Chip>{TRACK_LABEL[row.track] ?? row.track}</Chip>
                    <Chip>отправлено {dt(row.sent_at)}</Chip>
                    {row.replied && (
                      <Chip tone="accent">
                        ответ через {Math.round(row.hours_to_reply ?? 0)} ч
                      </Chip>
                    )}
                    {overdue(row) && <Chip tone="warn">пора напомнить</Chip>}
                  </div>
                </div>
                <div className="flex flex-wrap gap-1">
                  {NEXT_STAGES.map((stage) => (
                    <button
                      key={stage}
                      className="btn text-xs px-2 py-1"
                      onClick={() => advance(row.id, stage)}
                    >
                      {STAGE_LABEL[stage] ?? stage}
                    </button>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        )}
        <p className="text-xs text-muted mt-4">
          Напоминание ставится на 4-5 день, автозакрытие на 10-й: медиана
          архивации неотобранных кандидатов около 6 дней, поэтому напоминание
          должно приходить до отказа, а молчание после десятого дня это
          фактический отказ.
        </p>
      </Panel>
    </div>
  );
}
