import { type SystemConfig, putJsonApi } from '../../api';
import { useConfirm } from '../../components/Dialog';
import { Card } from '../../components/Layout';
import { useAction } from '../../lib/useAction';
import { useApi } from '../../lib/useApi';

/** The host-wide accelerator for local AI, photo recognition and document reading (administrators only). */
export function ComputeCard() {
  const config = useApi<SystemConfig>('/api/v1/system/config');
  const confirm = useConfirm();
  const { pending, run } = useAction();
  const modes = ['auto', 'cpu', 'nvidia', 'amd'] as const;
  const select = async (mode: SystemConfig['compute_mode']) => {
    if (
      !(await confirm({
        title: `Use ${mode === 'auto' ? 'automatic' : mode.toUpperCase()} acceleration?`,
        description:
          'New installs use it straight away. Apps already installed keep their version until you press Switch on their page.',
      }))
    )
      return;
    await run('compute', () => putJsonApi('/api/v1/system/config', { compute_mode: mode }), 'Compute mode saved');
    void config.reload();
  };
  return (
    <Card
      title="AI acceleration"
      description={`Used by local AI models, photo recognition and document reading. Automatic currently uses ${config.data?.resolved_compute_mode?.toUpperCase() || '…'}.`}
    >
      <div className="segmented" role="radiogroup" aria-label="Compute mode">
        {modes.map((mode) => (
          <button
            key={mode}
            type="button"
            role="radio"
            aria-checked={config.data?.compute_mode === mode}
            disabled={
              Boolean(pending) || !config.data || (mode !== 'auto' && !config.data.available_modes.includes(mode))
            }
            onClick={() => void select(mode)}
          >
            {mode === 'auto' ? 'Automatic' : mode.toUpperCase()}
          </button>
        ))}
      </div>
    </Card>
  );
}
