import { useState } from 'react';
import { putJsonApi, type VoicePage, type VoiceSettingsRequest } from '../../api';
import { Card } from '../../components/Layout';
import { useAction } from '../../lib/useAction';
import type { ApiState } from '../../lib/useApi';

type Key = 'vad_silence' | 'min_delay' | 'max_delay' | 'eot_threshold' | 'interrupt_after';

// The ranges match TIMING in ctl/voice_settings.py.
const FIELDS: { key: Key; label: string; hint: string; min: number; max: number; step: number; zero?: string }[] = [
  {
    key: 'vad_silence',
    label: 'Pause that ends a phrase (seconds)',
    hint: 'Each stretch of speech goes to speech recognition on its own. Longer keeps sentences whole; shorter answers sooner.',
    min: 0.1,
    max: 1.5,
    step: 0.05,
  },
  {
    key: 'min_delay',
    label: 'Shortest wait before answering (seconds)',
    hint: 'Even when you clearly sound finished. LiveKit allows no less than 0.25.',
    min: 0.25,
    max: 2,
    step: 0.05,
  },
  {
    key: 'max_delay',
    label: 'Longest wait when you sound unfinished (seconds)',
    hint: '0 follows Responsiveness above.',
    min: 0,
    max: 6,
    step: 0.25,
    zero: 'from Responsiveness',
  },
  {
    key: 'eot_threshold',
    label: 'Turn detector threshold',
    hint: 'Below this score you sound unfinished, so it waits longer. 0 uses the detector’s own.',
    min: 0,
    max: 0.99,
    step: 0.01,
    zero: 'the detector’s own',
  },
  {
    key: 'interrupt_after',
    label: 'Talk over it for at least (seconds)',
    hint: 'Shorter sounds (a cough, an “mm-hm”, the TV) don’t stop the assistant.',
    min: 0.2,
    max: 2,
    step: 0.1,
  },
];

/**
 * Fine-tuning for administrators: the timing behind Responsiveness, set from the scores the voice
 * agent logs on every turn. Folded away, since the presets suit most homes.
 */
export function VoiceTimingCard({ api }: { api: ApiState<VoicePage> }) {
  const { data, reload } = api;
  const { pending, run } = useAction();
  if (!data) return null;
  const settings = data.settings;
  const save = async (body: VoiceSettingsRequest) => {
    if (
      await run(
        'timing',
        () => putJsonApi<VoicePage>('/api/v1/ai/voice/settings', body),
        'Saved; the next call uses it',
      )
    )
      void reload();
  };
  return (
    <Card
      title="Advanced: turn timing"
      description="When the assistant decides you have finished. Each call’s log shows every turn’s score and wait (“End of turn” and “Reply after” lines), to tune these from."
      collapsible
      defaultOpen={false}
    >
      <div className="form voice-timing">
        {FIELDS.map((field) => (
          <NumberField
            key={`${field.key}-${settings[field.key]}`}
            field={field}
            value={Number(settings[field.key] ?? 0)}
            disabled={Boolean(pending)}
            onSave={(value) => void save({ [field.key]: value })}
          />
        ))}
      </div>
    </Card>
  );
}

function NumberField({
  field,
  value,
  disabled,
  onSave,
}: {
  field: (typeof FIELDS)[number];
  value: number;
  disabled: boolean;
  onSave: (value: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  const commit = () => {
    const number = Number(draft);
    if (draft.trim() === '' || Number.isNaN(number) || number === value) return setDraft(String(value));
    onSave(number);
  };
  return (
    <label className="field">
      <span>{field.label}</span>
      <input
        type="number"
        inputMode="decimal"
        min={field.min}
        max={field.max}
        step={field.step}
        value={draft}
        disabled={disabled}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === 'Enter') commit();
        }}
      />
      <small className="muted">
        {field.hint}
        {field.zero && value === 0 ? ` Now ${field.zero}.` : ''}
      </small>
    </label>
  );
}
