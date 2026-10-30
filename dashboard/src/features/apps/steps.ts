import { humanize } from '../../lib/format';

/** What each worker step means, in plain words; the worker's own lines stay in Live activity. */
const STEP_LABEL: Record<string, string> = {
  validate_service: 'Checking the app',
  materialize_runtime: 'Preparing its files',
  validate_configuration: 'Checking its settings',
  pull_images: 'Downloading',
  resolve_digests: 'Verifying the download',
  start_service: 'Starting it for the first time',
  base_installation: 'Running its first-time setup',
  account_bootstrap: 'Creating your account',
  account_verified: 'Checking your account',
  configure_application: 'Configuring it',
  gateway_key: 'Connecting it to the AI gateway',
  gateway_routes: 'Checking its AI models',
  provider_routing: 'Connecting it to your AI providers',
  model_check: 'Checking its AI model',
  embedding_check: 'Checking the search model',
  embedding_model_pull: 'Downloading the search model',
  verify_application: 'Waiting for it to answer',
  configure_route: 'Setting up its private address',
  authentik_ready: 'Setting up single sign-on',
  verify_sign_in: 'Checking sign-in',
  finalize: 'Finishing up',
  stop_after_setup: 'Stopping it until you need it',
};

/** A step's plain name; an unknown step is spelled out from its id. */
export const stepLabel = (step: string) => STEP_LABEL[step] || humanize(step || 'starting');
