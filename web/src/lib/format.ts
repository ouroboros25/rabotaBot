export function money(min?: number | null, max?: number | null, cur?: string | null) {
  if (!min && !max) return 'вилка не указана';
  const fmt = (n: number) => n.toLocaleString('ru-RU', { maximumFractionDigits: 0 });
  if (min && max) return `${fmt(min)}–${fmt(max)} ${cur ?? ''}`.trim();
  return `${fmt((max ?? min) as number)} ${cur ?? ''}`.trim();
}

export function age(iso?: string | null) {
  if (!iso) return '—';
  const hours = (Date.now() - new Date(iso).getTime()) / 36e5;
  if (hours < 1) return 'только что';
  if (hours < 48) return `${Math.round(hours)} ч`;
  return `${Math.round(hours / 24)} дн`;
}

export function dt(iso?: string | null) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

export function pct(value?: number | null, digits = 0) {
  if (value === null || value === undefined) return '—';
  return `${(value * 100).toFixed(digits)}%`;
}

export const TRACK_LABEL: Record<string, string> = {
  fte: 'найм', outstaff: 'контракт', freelance: 'фриланс', equity: 'стартап',
};

export const STAGE_LABEL: Record<string, string> = {
  sent: 'отправлено', acknowledged: 'подтверждено', screen: 'скрининг',
  tech: 'техинтервью', final: 'финал', offer: 'оффер', accepted: 'принято',
  rejected: 'отказ', ghosted: 'молчание', withdrawn: 'отозвано',
  knocked_out: 'мгновенный отказ', declined: 'отклонено мной',
};

export const GATE_LABEL: Record<string, string> = {
  GEO_FENCED: 'география', HYBRID_ONSITE: 'гибрид/офис', CLEARANCE: 'допуск',
  STALE: 'протухло', EVERGREEN: 'вечная вакансия', SENIORITY_OUT: 'не тот грейд',
  COMP_FLOOR: 'ниже пола', STACK_EXCLUDE: 'исключённый стек',
  EMPLOYMENT_MISMATCH: 'форма занятости', BLOCKLIST: 'чёрный список',
  EXPIRED: 'истекло', TZ_MISMATCH: 'таймзона', DUPLICATE_OF: 'дубль',
  NOT_FULL_REMOTE: 'не полная удалёнка', KEYWORD_MISSING: 'нет ключевых слов',
  KEYWORD_EXCLUDE: 'запрещённое слово', TITLE_EXCLUDE: 'слово в заголовке',
  COMPANY_EXCLUDE: 'компания в игноре', TOO_OLD: 'слишком старая',
};
