import { Play } from 'lucide-react';
import { type FormEvent, useCallback, useState } from 'react';
import {
  type JobResponse,
  postJsonApi,
  putJsonApi,
  type VoicePage,
  type VoiceSample,
  type VoiceSpeechRequest,
} from '../../api';
import { Button, LinkButton } from '../../components/Button';
import { useConfirm } from '../../components/Dialog';
import { Callout, Card } from '../../components/Layout';
import { useAction } from '../../lib/useAction';
import type { ApiState } from '../../lib/useApi';
import { JobLog } from './AiModelsCard';
import { ModelChoices, useRemoveModel } from './ModelChoices';

/** How the assistant sounds and hears: its own voice, and Speaches' models, which other apps share. */
export function VoiceSoundCard({ api, readOnly = false }: { api: ApiState<VoicePage>; readOnly?: boolean }) {
  const { data, error, reload } = api;
  const confirm = useConfirm();
  const { pending, run } = useAction();
  const [models, setModels] = useState<VoiceSpeechRequest | null>(null);
  const [jobId, setJobId] = useState('');
  const [running, setRunning] = useState(false);
  const finished = useCallback(
    (lost: boolean) => {
      setRunning(false);
      if (lost) setJobId('');
      void reload();
    },
    [reload],
  );
  const { remove, removing } = useRemoveModel(
    (model) => postJsonApi('/api/v1/ai/voice/speech/remove', { model: model.id }),
    () => void reload(),
  );
  const title = 'Voice';
  const description = 'How the voice assistant sounds, and the speech models that turn speech into text and back.';
  if (!data)
    return (
      <Card title={title} description={description}>
        {error ? <p className="error-text">Could not load: {error}</p> : <p className="muted card-loading">Loading…</p>}
      </Card>
    );
  const speech = data.speech;
  if (!speech.installed)
    return (
      <Card title={title} description={description}>
        <Callout
          tone="info"
          title="Speaches isn’t installed"
          action={<LinkButton to={`/apps/${speech.app_id}`}>View Speaches</LinkButton>}
        >
          The voice assistant hears and speaks through Speaches, on this server.
        </Callout>
      </Card>
    );
  const busy = running || Boolean(data.active_job_id);
  const voice = data.settings.voice;
  const defaultVoice = data.voices.find((item) => item.id === speech.default_voice);
  const draft = models ?? { stt_model: speech.stt.value, tts_model: speech.tts.value };
  const modelsEdited = draft.stt_model !== speech.stt.value || draft.tts_model !== speech.tts.value;

  const chooseVoice = async (id: string) => {
    if (await run('voice', () => putJsonApi<VoicePage>('/api/v1/ai/voice/settings', { voice: id }), 'Voice saved'))
      void reload();
  };
  const play = async () => {
    const sample = await run('sample', () =>
      postJsonApi<VoiceSample>('/api/v1/ai/voice/sample', { voice: voice || speech.default_voice }),
    );
    if (sample) void new Audio(`data:audio/mpeg;base64,${sample.audio}`).play();
  };
  const saveModels = async (event: FormEvent) => {
    event.preventDefault();
    if (
      !(await confirm({
        title: 'Change the speech models?',
        description:
          'Speaches is set up again: it downloads and checks the new models, then restarts. Voice calls and dictation pause for a few minutes, and every app using speech gets the new models.',
        confirmLabel: 'Change models',
      }))
    )
      return;
    const result = await run(
      'models',
      () => putJsonApi<JobResponse>('/api/v1/ai/voice/speech', draft),
      'Speaches is being set up with the new models.',
    );
    if (result) {
      setModels(null);
      setJobId(result.job.id);
      setRunning(true);
    }
  };

  return (
    <Card title={title} description={description}>
      <div className="form">
        <label className="field">
          <span>The assistant’s voice</span>
          <div className="inline-field">
            <select
              aria-label="The assistant’s voice"
              value={voice}
              disabled={readOnly || pending === 'voice' || !data.voices.length}
              onChange={(event) => void chooseVoice(event.target.value)}
            >
              <option value="">
                Speaches’ default{defaultVoice ? ` (${defaultVoice.name}, ${defaultVoice.accent})` : ''}
              </option>
              {data.voices.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} · {item.accent}
                  {item.gender ? `, ${item.gender}` : ''}
                </option>
              ))}
            </select>
            <Button
              size="sm"
              icon={Play}
              loading={pending === 'sample'}
              disabled={!data.voices.length || busy}
              onClick={() => void play()}
            >
              Try
            </Button>
          </div>
          <small className="muted">
            {data.voices.length
              ? 'The next call uses it; no restart needed.'
              : 'Speaches is not answering, so its voices cannot be listed. Start it in Apps.'}
          </small>
        </label>
      </div>
      <form className="form" onSubmit={(event) => void saveModels(event)}>
        <fieldset className="plain-fieldset" disabled={readOnly || busy}>
          <legend>Speech models, shared with every app that uses speech</legend>
          <ModelChoices
            name="hearing-model"
            legend="Hearing model (speech to text)"
            models={speech.stt.options}
            value={draft.stt_model}
            onChange={(id) => setModels({ ...draft, stt_model: id })}
            remove={(model) => void remove(model)}
            removing={removing}
          />
          <ModelChoices
            name="speaking-model"
            legend="Speaking model (text to speech)"
            models={speech.tts.options}
            value={draft.tts_model}
            onChange={(id) => setModels({ ...draft, tts_model: id })}
            remove={(model) => void remove(model)}
            removing={removing}
          />
        </fieldset>
        {!readOnly && (
          <div className="button-row">
            <Button type="submit" size="sm" loading={pending === 'models'} disabled={!modelsEdited || busy}>
              Save and set up Speaches
            </Button>
            {modelsEdited && !busy && <small className="muted">Not saved yet: nothing changes until you save.</small>}
          </div>
        )}
      </form>
      {(jobId || data.active_job_id) && <JobLog id={jobId || data.active_job_id} finished={finished} />}
    </Card>
  );
}
