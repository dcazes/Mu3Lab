import { type FormEvent, useCallback, useState } from 'react';
import { type AiModels, type AiVoiceRequest, type JobResponse, putJsonApi } from '../../api';
import { Button } from '../../components/Button';
import { useConfirm } from '../../components/Dialog';
import { Callout, Card } from '../../components/Layout';
import { Badge } from '../../components/Status';
import { useAction } from '../../lib/useAction';
import type { ApiState } from '../../lib/useApi';
import { JobLog, option, whereChatRuns } from './AiModelsCard';

/** Where the voice assistant's answers come from, in words for the person choosing. */
export function whereVoiceRuns(data: Pick<AiModels, 'voice' | 'chat_models' | 'settings'>): string {
  if (data.voice.matches_chat) return `The same as chat. ${whereChatRuns(data)}`;
  const local = data.chat_models.find((model) => model.id === data.voice.model);
  const name = local ? local.name : 'a local model';
  return data.voice.privacy === 'local_only'
    ? `Answers come from ${name}, only on this server.`
    : data.voice.privacy === 'local_first'
      ? `Answers come from ${name} on this server, or your outside providers when it is slow or stopped.`
      : 'Answers come from your outside providers.';
}

/** The answer source in two or three words, for the page's status line. */
export function voiceSourceLabel(data: Pick<AiModels, 'voice' | 'chat_models'>): string {
  const local = data.chat_models.find((model) => model.id === data.voice.model);
  const where =
    data.voice.privacy === 'local_only'
      ? 'Local only'
      : data.voice.privacy === 'local_first'
        ? 'Local first'
        : 'Outside providers';
  const what = data.voice.privacy === 'outside_allowed' || !local ? where : `${where} · ${local.name}`;
  return data.voice.matches_chat ? `Same as chat (${what})` : what;
}

type Choice = 'chat' | AiVoiceRequest['privacy'];

const CHOICES: { id: Choice; title: string; body: string }[] = [
  {
    id: 'chat',
    title: 'Same as chat',
    body: 'Follows Settings, AI, and changes with it.',
  },
  {
    id: 'local_only',
    title: 'Local only',
    body: 'A model on this server answers. If it stops working, the voice assistant stops too: nothing you say leaves this server.',
  },
  {
    id: 'local_first',
    title: 'Local first',
    body: 'A model on this server answers; your outside providers take over when it is slow or stopped, so the text of what you say can leave this server.',
  },
  {
    id: 'outside_allowed',
    title: 'Outside providers',
    body: 'The providers connected in Settings, AI answer. The text of what you say leaves this server.',
  },
];

/** The voice assistant's answer model: the same as chat, or its own choice (Settings, AI holds chat's). */
export function VoiceModelCard({ api, readOnly = false }: { api: ApiState<AiModels>; readOnly?: boolean }) {
  const { data, error, reload } = api;
  const confirm = useConfirm();
  const { pending, run } = useAction();
  const [edits, setEdits] = useState<{ choice: Choice; model: string } | null>(null);
  const [jobId, setJobId] = useState('');
  const [running, setRunning] = useState(false);
  const finished = useCallback(
    (lost: boolean) => {
      setRunning(false);
      // Unread progress: the server's active job (polled by the page) keeps the card locked while it runs.
      if (lost) setJobId('');
      void reload();
    },
    [reload],
  );
  const title = 'Answers';
  const description =
    'Where the voice assistant’s replies come from. Your speech is always turned into text, and replies read aloud, on this server.';
  if (!data)
    return (
      <Card title={title} description={description}>
        {error ? (
          <p className="error-text">Could not load: {error}</p>
        ) : (
          <p className="muted card-loading card-loading-tall">Loading…</p>
        )}
      </Card>
    );
  const saved = data.voice;
  const savedChoice: Choice = saved.matches_chat ? 'chat' : saved.privacy;
  const draft = edits ?? { choice: savedChoice, model: saved.model };
  const localOn = draft.choice === 'local_only' || draft.choice === 'local_first';
  const edited = draft.choice !== savedChoice || (localOn && draft.model !== saved.model);
  // The full-duplex model answers by itself; this setting waits until separate steps are back.
  const duplexOn = data.voice_engine.engine === 'duplex';
  const busy = running || Boolean(data.active_job_id) || duplexOn;
  const missingLocal = localOn && !draft.model;
  const chosen = data.chat_models.find((model) => model.id === draft.model);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    const body: AiVoiceRequest =
      draft.choice === 'chat'
        ? { privacy: saved.privacy, model: '', matches_chat: true }
        : { privacy: draft.choice, model: localOn ? draft.model : '' };
    if (
      chosen &&
      localOn &&
      !chosen.downloaded &&
      !(await confirm({
        title: 'Change the answer model?',
        description: `${chosen.name} is downloaded first (${chosen.download_gb} GB). Until it works, the voice assistant keeps its current model.`,
        confirmLabel: 'Change',
      }))
    )
      return;
    const result = await run(
      'voice',
      () => putJsonApi<JobResponse>('/api/v1/ai/voice', body),
      'Mu3Lab is checking the voice model. Nothing changes until it works.',
    );
    if (result) {
      setEdits(null);
      setJobId(result.job.id);
      setRunning(true);
    }
  };

  return (
    <Card title={title} description={description}>
      <p className="model-where">{whereVoiceRuns(data)}</p>
      {duplexOn && (
        <Callout tone="info" title="Not used while the full-duplex model is on">
          This applies again when Labs, full-duplex mode is switched off.
        </Callout>
      )}
      {!data.runner_reachable && localOn && (
        <Callout tone="warning" title="The model runner is not answering">
          Download states may be out of date until it is running again.
        </Callout>
      )}
      <form className="form" onSubmit={(event) => void save(event)}>
        <fieldset className="choice-list" disabled={readOnly || busy}>
          <legend>Where the voice assistant may send what you say</legend>
          {CHOICES.map((choice) => (
            <label className="choice" key={choice.id}>
              <input
                type="radio"
                name="voice-answers"
                checked={draft.choice === choice.id}
                onChange={() => setEdits({ ...draft, choice: choice.id })}
              />
              <span>
                <strong>{choice.title}</strong>
                <small>{choice.body}</small>
              </span>
            </label>
          ))}
        </fieldset>
        {localOn && (
          <label className="field">
            <span>Answer model</span>
            <select
              aria-label="Answer model"
              disabled={readOnly || busy}
              value={draft.model}
              onChange={(event) => setEdits({ ...draft, model: event.target.value })}
            >
              <option value="">Choose a model</option>
              {data.chat_models.map((model) => (
                <option key={model.id} value={model.id}>
                  {option(model)}
                </option>
              ))}
            </select>
            {chosen && (
              <small className="muted">
                {chosen.note} {chosen.downloaded ? <Badge tone="green">Downloaded</Badge> : null}
              </small>
            )}
          </label>
        )}
        {missingLocal && <p className="error-text">Choose an answer model to run on this server.</p>}
        {!readOnly && (
          <div className="button-row">
            <Button type="submit" size="sm" loading={pending === 'voice'} disabled={!edited || missingLocal || busy}>
              Save and apply
            </Button>
            {edited && !busy && <small className="muted">Not saved yet: nothing changes until you save.</small>}
          </div>
        )}
      </form>
      {(jobId || data.active_job_id) && <JobLog id={jobId || data.active_job_id} finished={finished} />}
    </Card>
  );
}
