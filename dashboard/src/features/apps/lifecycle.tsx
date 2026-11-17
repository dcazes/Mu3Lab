import { Check } from 'lucide-react';
import { createContext, type ReactNode, useCallback, useContext, useState } from 'react';
import { toast } from 'sonner';
import { postJsonApi, type Service } from '../../api';
import { AppIcon } from '../../components/AppIcon';
import { Button } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
import { errorText } from '../../lib/format';
import { isInstalled, isRunning } from '../../lib/services';
import { useDashboard } from '../../state/dashboard';

/** The lifecycle actions that can run on several apps at once, from Home's select mode. */
export type Lifecycle = 'start' | 'stop' | 'restart';

export const can = (service: Service, action: Lifecycle) => (service.allowed_actions || []).includes(action);

const list = (names: string[]) => new Intl.ListFormat('en', { style: 'long', type: 'conjunction' }).format(names);

/** Every app `service` needs, directly or through another, each one's own needs first. */
export function requirements(service: Service, byId: Map<string, Service>, seen = new Set<string>()): Service[] {
  const found: Service[] = [];
  for (const id of service.dependencies || []) {
    if (seen.has(id)) continue;
    seen.add(id);
    const dependency = byId.get(id);
    if (!dependency) continue;
    found.push(...requirements(dependency, byId, seen), dependency);
  }
  return found;
}

const index = (services: Service[]) => new Map(services.map((service) => [service.id, service]));

/** `apps` with whatever they need before them: the order to start them in (stop in reverse). */
export function inOrder(apps: Service[], services: Service[]): Service[] {
  const byId = index(services);
  const wanted = new Set(apps.map((app) => app.id));
  const order: Service[] = [];
  for (const app of apps) {
    for (const item of [...requirements(app, byId), app]) {
      if (wanted.has(item.id) && !order.includes(item)) order.push(item);
    }
  }
  return order;
}

/** The stopped apps the chosen ones need, which start with them (the server starts them first). */
export function neededToStart(chosen: Service[], services: Service[]): Service[] {
  const byId = index(services);
  const picked = new Set(chosen.map((app) => app.id));
  const found: Service[] = [];
  for (const app of chosen) {
    for (const item of requirements(app, byId)) {
      if (!picked.has(item.id) && !found.includes(item) && isInstalled(item) && item.display_state === 'stopped')
        found.push(item);
    }
  }
  return found;
}

/** Running apps that need one of the chosen ones and would stop working without it. */
export function runningDependents(chosen: Service[], services: Service[]): Service[] {
  const byId = index(services);
  const picked = new Set(chosen.map((app) => app.id));
  return services.filter(
    (app) =>
      !picked.has(app.id) &&
      isRunning(app) &&
      can(app, 'stop') &&
      requirements(app, byId).some((item) => picked.has(item.id)),
  );
}

/** Running apps only the stopping ones needed: nothing else running uses them afterwards. */
export function unneededAfterStop(stopping: Service[], services: Service[]): Service[] {
  const byId = index(services);
  const leaving = new Set(stopping.map((app) => app.id));
  let helpers = new Set(
    stopping
      .flatMap((app) => requirements(app, byId))
      .filter((item) => !leaving.has(item.id) && isRunning(item) && can(item, 'stop'))
      .map((item) => item.id),
  );
  // Drop each helper an app that keeps running still needs, until none is left to drop.
  for (let changed = true; changed;) {
    changed = false;
    for (const app of services) {
      if (leaving.has(app.id) || helpers.has(app.id) || !isRunning(app)) continue;
      const needs = requirements(app, byId).filter((item) => helpers.has(item.id));
      if (needs.length) {
        helpers = new Set([...helpers].filter((id) => !needs.some((item) => item.id === id)));
        changed = true;
      }
    }
  }
  return services.filter((app) => helpers.has(app.id));
}

interface Group {
  key: 'needed' | 'dependents' | 'helpers';
  title: string;
  hint: string;
  apps: Service[];
}

interface Plan {
  action: Lifecycle;
  apps: Service[];
  // Start: the stopped apps they need. Stop: the running apps that need them.
  others: Service[];
  picked: Set<string>;
  resolve: (queued: boolean) => void;
}

/** What the dialog shows; for a stop, what only the stopping apps used follows the picks. */
function groupsOf(plan: Plan, services: Service[]): Group[] {
  const names = list(plan.apps.map((app) => app.name));
  if (plan.action === 'start')
    return [
      {
        key: 'needed',
        title: plan.apps.length === 1 ? `${names} needs these stopped apps` : 'These apps need stopped apps',
        hint: 'They start first. Tap one to leave it stopped; what needs it may not work.',
        apps: plan.others,
      },
    ];
  const stopping = [...plan.apps, ...plan.others.filter((app) => plan.picked.has(app.id))];
  return [
    {
      key: 'dependents',
      title: 'These stop working too',
      hint: `They need ${names}. Tap one to leave it running.`,
      apps: plan.others,
    },
    {
      key: 'helpers',
      title: 'Nothing else running needs these',
      hint: 'Tap any to stop it too and free what it uses.',
      apps: unneededAfterStop(stopping, services),
    },
  ];
}

type Run = (action: Lifecycle, apps: Service[]) => Promise<boolean>;
const LifecycleContext = createContext<Run | null>(null);

const DOING: Record<Lifecycle, string> = { start: 'Starting', stop: 'Stopping', restart: 'Restarting' };
const LABEL: Record<Lifecycle, string> = { start: 'Start', stop: 'Stop', restart: 'Restart' };

/**
 * Starts, stops and restarts apps with what they need in mind, asking first when other apps are
 * involved: SurfSense starts with LiteLLM, Ollama and FreeLLMAPI; stopping LiteLLM stops what
 * needs it; stopping SurfSense offers to stop what only it used.
 */
export function LifecycleProvider({ children }: { children: ReactNode }) {
  const { data, refresh } = useDashboard();
  const services = data.services.services;
  const [plan, setPlan] = useState<Plan | null>(null);

  const queue = useCallback(
    async (action: Lifecycle, apps: Service[], needed: Service[]) => {
      const ordered = action === 'stop' ? inOrder(apps, services).reverse() : inOrder(apps, services);
      const byId = index(services);
      const chosen = new Set(needed.map((app) => app.id));
      let queued = 0;
      for (const app of ordered) {
        // Each start names the needed apps it may start; the rest stay stopped, as the person chose.
        const body =
          action !== 'start'
            ? { action }
            : {
                action,
                dependencies: requirements(app, byId)
                  .filter((item) => chosen.has(item.id))
                  .map((item) => item.id),
              };
        try {
          await postJsonApi(`/api/v1/services/${app.id}/actions`, body);
          queued += 1;
        } catch (error) {
          toast.error(`${app.name}: ${errorText(error)}`);
        }
      }
      if (queued) {
        const names = [...(action === 'start' ? inOrder(needed, services) : []), ...ordered].map((app) => app.name);
        toast.success(`${DOING[action]} ${list(names)}…`);
      }
      void refresh();
      return queued > 0;
    },
    [services, refresh],
  );

  const run = useCallback<Run>(
    async (action, apps) => {
      const others =
        action === 'start' ? neededToStart(apps, services) : action === 'stop' ? runningDependents(apps, services) : [];
      // Nothing else involved in a start: go straight away, as a single Start always has.
      if (action === 'start' && !others.length) return queue(action, apps, []);
      // What needs looking after is picked; helpers are only offered.
      const picked = new Set(others.map((app) => app.id));
      return new Promise<boolean>((resolve) => setPlan({ action, apps, others, picked, resolve }));
    },
    [services, queue],
  );

  const groups = plan ? groupsOf(plan, services).filter((group) => group.apps.length) : [];
  const chosen = groups.flatMap((group) => group.apps).filter((app) => plan?.picked.has(app.id));
  const close = (queued: boolean) => {
    plan?.resolve(queued);
    setPlan(null);
  };
  const confirmPlan = async () => {
    if (!plan) return;
    const { action, apps, resolve } = plan;
    setPlan(null);
    resolve(action === 'start' ? await queue(action, apps, chosen) : await queue(action, [...apps, ...chosen], []));
  };
  const toggle = (id: string) => {
    if (!plan) return;
    const picked = new Set(plan.picked);
    if (picked.has(id)) picked.delete(id);
    else picked.add(id);
    setPlan({ ...plan, picked });
  };

  const count = (plan?.apps.length || 0) + chosen.length;
  const one = plan?.apps.length === 1 ? plan.apps[0] : undefined;
  const description = !plan
    ? ''
    : plan.action === 'start'
      ? undefined
      : plan.action === 'stop'
        ? // Some apps reach further than themselves when stopped (Vaultwarden and Bitwarden).
          [
            `${one ? 'It becomes' : 'They become'} unavailable until started again.`,
            ...[...plan.apps, ...chosen].map((app) => app.stop_warning),
          ]
            .filter(Boolean)
            .join(' ')
        : `${one ? 'It is' : 'They are'} briefly unavailable while ${one ? 'it restarts' : 'they restart'}.`;
  return (
    <LifecycleContext.Provider value={run}>
      {children}
      <Dialog
        open={Boolean(plan)}
        onClose={() => close(false)}
        title={plan ? `${LABEL[plan.action]} ${one ? one.name : `${plan.apps.length} apps`}?` : ''}
        description={description}
        footer={
          <>
            <Button onClick={() => close(false)}>Cancel</Button>
            <Button
              variant={plan?.action === 'stop' ? 'danger' : 'primary'}
              onClick={() => void confirmPlan()}
              data-autofocus
            >
              {plan ? `${LABEL[plan.action]} ${count === 1 ? (one?.name ?? '1 app') : `${count} apps`}` : ''}
            </Button>
          </>
        }
      >
        {plan &&
          groups.map((group) => (
            <section key={group.key} className="plan-group" aria-label={group.title}>
              <h3>{group.title}</h3>
              <p>{group.hint}</p>
              <div className="plan-apps">
                {group.apps.map((app) => {
                  const on = plan.picked.has(app.id);
                  return (
                    <button
                      key={app.id}
                      type="button"
                      className={`plan-app${on ? ' is-selected' : ''}`}
                      aria-pressed={on}
                      onClick={() => toggle(app.id)}
                    >
                      <AppIcon id={app.id} size="sm" />
                      <span>{app.name}</span>
                      <Check className="plan-check" aria-hidden="true" />
                    </button>
                  );
                })}
              </div>
            </section>
          ))}
      </Dialog>
    </LifecycleContext.Provider>
  );
}

export function useLifecycle(): Run {
  const run = useContext(LifecycleContext);
  if (!run) throw new Error('useLifecycle must be used inside LifecycleProvider');
  return run;
}
