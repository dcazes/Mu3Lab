import { type FormEvent, useState } from 'react';
import { putJsonApi, type Service, type ServiceConfigResponse } from '../../api';
import { Button } from '../../components/Button';
import { Card, EmptyState } from '../../components/Layout';
import { useAction } from '../../lib/useAction';
import { useApi } from '../../lib/useApi';

type Value = string | boolean | number;

function initialValues(config: ServiceConfigResponse | null): Record<string, Value> {
  return Object.fromEntries(
    (config?.fields || [])
      .filter((field) => field.value !== null && field.value !== undefined)
      .map((field) => [field.key, field.value as Value]),
  );
}

function Form({ service, config }: { service: Service; config: ServiceConfigResponse }) {
  const [values, setValues] = useState<Record<string, Value>>(() => initialValues(config));
  // Sent back so a save never overwrites a change someone else made after this form loaded.
  const [revision, setRevision] = useState(config.revision ?? null);
  const { pending, run } = useAction();
  const set = (key: string, value: Value) => setValues((current) => ({ ...current, [key]: value }));
  const save = (event: FormEvent) => {
    event.preventDefault();
    void run(
      'save',
      async () => {
        const result = await putJsonApi<ServiceConfigResponse>(`/api/v1/services/${service.id}/configuration`, {
          values,
          expected_revision: revision,
        });
        setRevision(result.revision ?? null);
        return result;
      },
      (result) => (result.restart_required ? 'Saved. Restart the app to apply it.' : 'Settings saved'),
    );
  };
  return (
    <form className="form" onSubmit={save}>
      {config.fields.map((field) => {
        const label = field.label || field.key;
        if (field.type === 'boolean')
          return (
            <label className="switch-field" key={field.key}>
              <span>{label}</span>
              <input
                type="checkbox"
                role="switch"
                checked={Boolean(values[field.key] ?? field.default)}
                onChange={(event) => set(field.key, event.target.checked)}
              />
            </label>
          );
        return (
          <label className="field" key={field.key}>
            <span>
              {label}
              {field.required && <em> required</em>}
            </span>
            {field.type === 'enum' ? (
              <select
                value={String(values[field.key] ?? field.default ?? '')}
                onChange={(event) => set(field.key, event.target.value)}
              >
                {field.options?.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            ) : (
              <input
                required={Boolean(field.required) && !(field.type === 'secret' && field.secret_present)}
                type={field.type === 'secret' ? 'password' : field.type === 'integer' ? 'number' : 'text'}
                autoComplete="off"
                value={String(values[field.key] ?? '')}
                placeholder={field.type === 'secret' && field.secret_present ? 'Saved — leave blank to keep' : ''}
                onChange={(event) =>
                  set(field.key, field.type === 'integer' ? Number(event.target.value) : event.target.value)
                }
              />
            )}
          </label>
        );
      })}
      <div>
        <Button variant="primary" type="submit" loading={pending === 'save'}>
          Save settings
        </Button>
      </div>
    </form>
  );
}

export function ConfigurationForm({ service }: { service: Service }) {
  const config = useApi<ServiceConfigResponse>(`/api/v1/services/${service.id}/configuration`);
  return (
    <Card title="Settings" description={`Configure the options Mu3Lab manages for ${service.name}.`}>
      {config.error ? (
        <p className="error-text">{config.error}</p>
      ) : !config.data ? (
        <p className="muted">Loading…</p>
      ) : config.data.fields.length ? (
        <Form key={JSON.stringify(config.data.fields)} service={service} config={config.data} />
      ) : (
        <EmptyState title="No settings">No app settings are available through Mu3Lab.</EmptyState>
      )}
    </Card>
  );
}
