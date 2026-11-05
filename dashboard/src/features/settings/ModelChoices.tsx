import type { ReactNode } from 'react';
import { Button } from '../../components/Button';
import { useConfirm } from '../../components/Dialog';
import { Badge } from '../../components/Status';
import { useAction } from '../../lib/useAction';

/** What every model list shows: a local model (Ollama) or a speech model (Speaches). */
export type ModelChoice = {
  id: string;
  name: string;
  note: string;
  precision: string;
  download_gb: number;
  memory_gb: number;
  downloaded: boolean;
  in_use?: boolean;
};

/** Its size and the memory it needs while loaded, at the precision it is stored in. */
export function sizes(model: ModelChoice): string {
  const precision = model.precision ? `${model.precision}: ` : '';
  return `${precision}${model.download_gb} GB download, about ${model.memory_gb} GB memory.`;
}

/** Remove a downloaded model after the person confirms; ``request`` deletes it on the server. */
export function useRemoveModel(request: (model: ModelChoice) => Promise<unknown>, removed: () => void) {
  const confirm = useConfirm();
  const { pending, run } = useAction();
  const remove = async (model: ModelChoice) => {
    if (
      !(await confirm({
        title: `Remove ${model.name}?`,
        description: `This frees ${model.download_gb} GB of disk space. Choosing it again downloads it again.`,
        confirmLabel: 'Remove',
        tone: 'danger',
      }))
    )
      return;
    if (await run(`remove-${model.id}`, () => request(model), `${model.name} removed`)) removed();
  };
  return { remove, removing: pending?.startsWith('remove-') ? pending.slice('remove-'.length) : '' };
}

/**
 * One model per row: its badges, note, size and memory, Downloaded once it is on this server, and
 * Remove for a downloaded one nothing uses. Every model setting lists its choices this way.
 */
export function ModelChoices<M extends ModelChoice>({
  name,
  legend,
  hint,
  footer,
  models,
  value,
  onChange,
  disabled = false,
  locked = false,
  readOnly = false,
  remove,
  removing = '',
  badges,
  actions,
  below,
}: {
  name: string;
  legend: string;
  hint?: ReactNode;
  footer?: ReactNode;
  models: M[];
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
  // The choice cannot change, though unused downloads can still be removed.
  locked?: boolean;
  readOnly?: boolean;
  remove?: (model: M) => void;
  removing?: string;
  badges?: (model: M) => ReactNode;
  actions?: (model: M) => ReactNode;
  below?: (model: M) => ReactNode;
}) {
  return (
    <fieldset className="model-list" disabled={readOnly || disabled}>
      <legend>{legend}</legend>
      {hint}
      {models.map((model) => {
        const removable = Boolean(remove) && model.downloaded && !model.in_use && !readOnly;
        const extra = actions?.(model);
        return (
          <div key={model.id} className="model-entry">
            <label className="model-row">
              <input
                type="radio"
                name={name}
                aria-label={model.name}
                disabled={locked}
                checked={value === model.id}
                onChange={() => onChange(model.id)}
              />
              <span className="model-row-text">
                <strong>
                  {model.name}
                  {badges?.(model)}
                  {model.downloaded && <Badge>Downloaded</Badge>}
                </strong>
                <small>
                  {model.note} {sizes(model)}
                </small>
              </span>
              {(extra || removable) && (
                <span className="model-row-actions">
                  {extra}
                  {removable && (
                    <Button
                      size="sm"
                      variant="ghost"
                      loading={removing === model.id}
                      aria-label={`Remove ${model.name}`}
                      onClick={() => remove?.(model)}
                    >
                      Remove
                    </Button>
                  )}
                </span>
              )}
            </label>
            {below?.(model)}
          </div>
        );
      })}
      {footer}
    </fieldset>
  );
}
