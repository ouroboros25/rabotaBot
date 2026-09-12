import { Link } from 'react-router-dom';
import { api } from '@/lib/api';
import { useAsync } from '@/lib/useAsync';
import { Chip, Empty, ErrorNote, Panel } from '@/components/ui';
import { TRACK_LABEL, dt } from '@/lib/format';

const STATUS_TONE: Record<string, 'muted' | 'accent' | 'warn'> = {
  draft: 'muted', approved: 'accent', sent: 'accent', discarded: 'warn',
};

export default function Drafts() {
  const { data, error, loading } = useAsync(() => api.drafts(), []);

  return (
    <div className="flex flex-col gap-4">
      <ErrorNote error={error} />
      <Panel title="Черновики">
        {loading && !data ? (
          <Empty>загрузка…</Empty>
        ) : !data?.length ? (
          <Empty>черновиков нет — сделайте один из очереди</Empty>
        ) : (
          <ul className="divide-y divide-line">
            {data.map((d) => (
              <li key={d.id} className="py-3 flex flex-wrap items-center gap-3">
                <Link to={`/drafts/${d.id}`} className="flex-1 min-w-[14rem]">
                  <span className="font-medium hover:text-accent transition-colors">
                    {d.title ?? `черновик #${d.id}`}
                  </span>
                  <div className="text-sm text-muted">{d.company ?? '—'}</div>
                </Link>
                <div className="flex flex-wrap gap-1.5">
                  <Chip tone={STATUS_TONE[d.status] ?? 'muted'}>{d.status}</Chip>
                  <Chip>{TRACK_LABEL[d.track] ?? d.track}</Chip>
                  <Chip>{d.template}</Chip>
                  <Chip>{d.word_count} слов</Chip>
                  <Chip tone={d.checks_passed ? 'accent' : 'warn'}>
                    {d.checks_passed ? 'проверки пройдены' : 'есть замечания'}
                  </Chip>
                </div>
                <span className="label w-24 text-right">{dt(d.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
