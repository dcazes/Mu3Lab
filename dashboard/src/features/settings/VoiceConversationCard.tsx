import { putJsonApi, type VoicePage, type VoiceSettingsRequest } from '../../api';
import { Card } from '../../components/Layout';
import { useAction } from '../../lib/useAction';
import type { ApiState } from '../../lib/useApi';

type Responsiveness = VoicePage['settings']['responsiveness'];

// The seconds match MAX_WAIT_SECONDS in apps/livekit/voice-agent/src/agent.py.
const RESPONSIVENESS: { id: Responsiveness; label: string; hint: string }[] = [
  {
    id: 'fast',
    label: 'Fast',
    hint: 'Answers soonest. When you sound unfinished it waits at most 1 second, so a long pause mid-sentence can be cut off.',
  },
  {
    id: 'balanced',
    label: 'Balanced',
    hint: 'When you sound unfinished it waits up to 2 seconds for the rest. Right for most people.',
  },
  {
    id: 'patient',
    label: 'Patient',
    hint: 'Waits up to 3 seconds when you sound unfinished. For slower or more thoughtful speakers.',
  },
];

/** How the conversation feels: how long the assistant waits for you, and whether you can talk over it. */
export function VoiceConversationCard({ api, readOnly = false }: { api: ApiState<VoicePage>; readOnly?: boolean }) {
  const { data, error, reload } = api;
  const { pending, run } = useAction();
  const title = 'Conversation';
  const description = 'When the voice assistant decides you have finished, and whether you can talk over it.';
  if (!data)
    return (
      <Card title={title} description={description}>
        {error ? <p className="error-text">Could not load: {error}</p> : <p className="muted card-loading">Loading…</p>}
      </Card>
    );
  const settings = data.settings;
  const change = async (key: string, body: VoiceSettingsRequest) => {
    if (await run(key, () => putJsonApi<VoicePage>('/api/v1/ai/voice/settings', body), 'Saved; the next call uses it'))
      void reload();
  };
  const chosen = RESPONSIVENESS.find((item) => item.id === settings.responsiveness) ?? RESPONSIVENESS[1];
  return (
    <Card title={title} description={description}>
      <div className="form">
        <div className="field">
          <span id="voice-responsiveness">Responsiveness</span>
          <div className="segmented" role="radiogroup" aria-labelledby="voice-responsiveness">
            {RESPONSIVENESS.map((item) => (
              <button
                key={item.id}
                type="button"
                role="radio"
                aria-checked={settings.responsiveness === item.id}
                disabled={readOnly || Boolean(pending)}
                onClick={() => void change('responsiveness', { responsiveness: item.id })}
              >
                {item.label}
              </button>
            ))}
          </div>
          <small className="muted">{chosen.hint}</small>
        </div>
        <label className="switch-field">
          <span className="switch-label">
            Let people interrupt
            <small>Talking over the assistant stops it. Turn this off if background noise keeps cutting it off.</small>
          </span>
          <input
            type="checkbox"
            role="switch"
            aria-label="Let people interrupt"
            checked={settings.interruptions}
            disabled={readOnly || pending === 'interruptions'}
            onChange={(event) => void change('interruptions', { interruptions: event.target.checked })}
          />
        </label>
      </div>
    </Card>
  );
}
