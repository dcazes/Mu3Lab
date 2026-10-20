import { type FormEvent, useCallback, useState } from 'react';
import { type AiModels, type AiVoiceRequest, type JobResponse, putJsonApi } from '../../api';
import { Button } from '../../components/Button';
import { useConfirm } from '../../components/Dialog';
import { Callout, Card } from '../../components/Layout';
import { Badge } from '../../components/Status';
import { useAction } from '../../lib/useAction';
import { useApi } from '../../lib/useApi';
import { JobLog, option } from './AiModelsCard';

/** Where the voice assistant's answers come from, in words for the person choosing. */
export function whereVoiceRuns(data: Pick<AiModels, 'voice' | 'chat_models'>): string {
  const local = data.chat_models.find((model) => model.id === data.voice.model);
  const where =
    data.voice.privacy === 'local_only'
      ? `Answers come from ${local ? local.name : 'a local model'}, only on this server.`
      : 'Answers come from your outside providers.';
  return data.voice.matches_chat ? `${where} This matches chat (Settings, AI) until you save a choice here.` : where;
}

/** The voice assistant's own switch and model, independent of chat (Settings, AI). */
export function VoiceModelCard({ readOnly = false }: { readOnly?: boolean }) {
  const { data, error, reload } = useApi<AiModels>('/api/v1/ai/models');
  const confirm = useConfirm();
  const { pending, run } = useAction();
  const [edits, setEdits] = useState<AiVoiceRequest | null>(null);
  const [jobId, setJobId] = useState('');
  const [running, setRunning] = useState(false);
  const finished = useCallback(() => {
    setRunning(false);
    void reload();
  }, [reload]);
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
  const changed = draft.privacy !== saved.privacy || (draft.privacy === 'local_only' && draft.model !== saved.model);
  const busy = running || Boolean(data.active_job_id);
  const missingLocal = draft.privacy === 'local_only' && !draft.model;
  const chosen = data.chat_models.find((model) => model.id === draft.model);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    const body: AiVoiceRequest = { privacy: draft.privacy, model: draft.privacy === 'local_only' ? draft.model : '' };
    if (
      chosen &&
      draft.privacy === 'local_only' &&
      !chosen.downloaded &&
      !(await confirm({
        title: 'Change the voice model?',
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
      {!data.runner_reachable && draft.privacy === 'local_only' && (
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
        {draft.privacy === 'local_only' && (
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
        {missingLocal && <p className="error-text">Local only needs a voice model.</p>}
        {!readOnly && (
          <div className="button-row">
            <Button type="submit" size="sm" loading={pending === 'voice'} disabled={!changed || missingLocal || busy}>
              Save and apply
            </Button>
          </div>
        )}
      </form>
      {(jobId || data.active_job_id) && <JobLog id={jobId || data.active_job_id} finished={finished} />}
    </Card>
  );
}
