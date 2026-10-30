import { type FormEvent, useCallback, useState } from 'react';
import { type AiModels, type AiVoiceRequest, type JobResponse, putJsonApi } from '../../api';
import { Button } from '../../components/Button';
import { useConfirm } from '../../components/Dialog';
import { Callout, Card } from '../../components/Layout';
import { Badge } from '../../components/Status';
import { useAction } from '../../lib/useAction';
import { JOB_POLL_MS } from '../../lib/jobs';
import { useApi } from '../../lib/useApi';
import { JobLog, option } from './AiModelsCard';

/** Where the voice assistant's answers come from, in words for the person choosing. */
export function whereVoiceRuns(data: Pick<AiModels, 'voice' | 'chat_models'>): string {
  const local = data.chat_models.find((model) => model.id === data.voice.model);
  const name = local ? local.name : 'a local model';
  const where =
    data.voice.privacy === 'local_only'
      ? `Answers come from ${name}, only on this server.`
      : data.voice.privacy === 'local_first'
        ? `Answers come from ${name} on this server, or your outside providers when it is slow or stopped.`
        : 'Answers come from your outside providers.';
  return data.voice.matches_chat ? `${where} This matches chat (Settings, AI) until you save a choice here.` : where;
}

/** The voice assistant's own switch and model, independent of chat (Settings, AI). */
export function VoiceModelCard({ readOnly = false }: { readOnly?: boolean }) {
  // Followed while a settings change runs, so the card unlocks when it ends.
  const { data, error, reload } = useApi<AiModels>('/api/v1/ai/models', {
    interval: (current) => (current?.active_job_id ? JOB_POLL_MS : 0),
  });
  const confirm = useConfirm();
  const { pending, run } = useAction();
  const [edits, setEdits] = useState<AiVoiceRequest | null>(null);
  const [jobId, setJobId] = useState('');
  const [running, setRunning] = useState(false);
  const finished = useCallback(
    (lost: boolean) => {
      setRunning(false);
      // Unread progress: the server's active job (polled above) keeps the card locked while it runs.
      if (lost) setJobId('');
      void reload();
    },
    [reload],
  );
  const title = 'Where the voice assistant runs';
  const description =
    'Choose where the voice assistant gets its answers, separately from chat. Speech is always turned into text and read back on this server.';
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
  const draft = edits ?? { privacy: saved.privacy, model: saved.model };
  // While voice matches chat, saving the same choice keeps it when chat changes later.
  const localOn = draft.privacy !== 'outside_allowed';
  const changed = saved.matches_chat || draft.privacy !== saved.privacy || (localOn && draft.model !== saved.model);
  // Edited away from what is saved; a voice that matches chat can be saved as is to keep it.
  const edited = draft.privacy !== saved.privacy || (localOn && draft.model !== saved.model);
  // The full-duplex model answers by itself; this setting waits until separate steps are back.
  const duplexOn = data.voice_engine.engine === 'duplex';
  const busy = running || Boolean(data.active_job_id) || duplexOn;
  const missingLocal = localOn && !draft.model;
  const chosen = data.chat_models.find((model) => model.id === draft.model);

  const apply = async (body: AiVoiceRequest) => {
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

  const followChat = async () => {
    if (
      !(await confirm({
        title: 'Follow chat again?',
        description:
          'The voice assistant goes back to where chat runs (Settings, AI), and changes with it from now on.',
        confirmLabel: 'Follow chat',
      }))
    )
      return;
    await apply({ privacy: saved.privacy, model: '', matches_chat: true });
  };

  const save = async (event: FormEvent) => {
    event.preventDefault();
    const body: AiVoiceRequest = { privacy: draft.privacy, model: localOn ? draft.model : '' };
    if (
      chosen &&
      localOn &&
      !chosen.downloaded &&
      !(await confirm({
        title: 'Change the voice model?',
        description: `${chosen.name} is downloaded first (${chosen.download_gb} GB). Until it works, the voice assistant keeps its current model.`,
        confirmLabel: 'Change',
      }))
    )
      return;
    await apply(body);
  };

  return (
    <Card title={title} description={description}>
      <p className="model-where">{whereVoiceRuns(data)}</p>
      {duplexOn && (
        <Callout tone="info" title="Not used while the full-duplex model is on">
          This setting applies again when the voice assistant switches back to separate steps.
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
          <label className="choice">
            <input
              type="radio"
              name="voice-privacy"
              checked={draft.privacy === 'outside_allowed'}
              onChange={() => setEdits({ ...draft, privacy: 'outside_allowed' })}
            />
            <span>
              <strong>Outside providers allowed</strong>
              <small>
                Answers come from the providers connected in Settings, AI. The text of what you say leaves this server.
              </small>
            </span>
          </label>
          <label className="choice">
            <input
              type="radio"
              name="voice-privacy"
              checked={draft.privacy === 'local_first'}
              onChange={() => setEdits({ ...draft, privacy: 'local_first' })}
            />
            <span>
              <strong>Local first</strong>
              <small>
                Answers come from a model on this server. When it is slow or stopped, your outside providers answer
                instead, so the text of what you say can leave this server.
              </small>
            </span>
          </label>
          <label className="choice">
            <input
              type="radio"
              name="voice-privacy"
              checked={draft.privacy === 'local_only'}
              onChange={() => setEdits({ ...draft, privacy: 'local_only' })}
            />
            <span>
              <strong>Local only</strong>
              <small>
                Answers come from a model on this server. If it stops working, the voice assistant stops too: it never
                falls back to an outside provider.
              </small>
            </span>
          </label>
        </fieldset>
        {localOn && (
          <label className="field">
            <span>Voice model</span>
            <select
              aria-label="Voice model"
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
        {missingLocal && <p className="error-text">Choose a voice model to run it on this server.</p>}
        {!readOnly && (
          <div className="button-row">
            <Button type="submit" size="sm" loading={pending === 'voice'} disabled={!changed || missingLocal || busy}>
              Save and apply
            </Button>
            {!saved.matches_chat && (
              <Button
                size="sm"
                variant="ghost"
                loading={pending === 'voice'}
                disabled={busy}
                onClick={() => void followChat()}
              >
                Follow chat again
              </Button>
            )}
            {edited && !busy && <small className="muted">Not saved yet: nothing changes until you save.</small>}
          </div>
        )}
      </form>
      {(jobId || data.active_job_id) && <JobLog id={jobId || data.active_job_id} finished={finished} />}
    </Card>
  );
}
