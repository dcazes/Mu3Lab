export interface paths {
  '/api/health': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Health
     * @description Liveness probe used by Caddy, systemd, and the installer.
     */
    get: operations['health_api_health_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/ai/models': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Ai Models
     * @description Everyone may see where AI requests go; only administrators change it.
     */
    get: operations['ai_models_api_v1_ai_models_get'];
    /**
     * Change Ai Models
     * @description Queue the change: models download and are checked before anything is switched over.
     */
    put: operations['change_ai_models_api_v1_ai_models_put'];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/ai/voice': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    /**
     * Change Voice
     * @description Queue the voice assistant's change: a local model downloads and is checked before voice switches over.
     */
    put: operations['change_voice_api_v1_ai_voice_put'];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/app-sizes': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * App Sizes
     * @description Per-app sizes, plus totals for a selection with shared layers counted once.
     */
    get: operations['app_sizes_api_v1_app_sizes_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/audit': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Audit
     * @description Append-only, secret-redacted audit metadata.
     */
    get: operations['audit_api_v1_audit_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/backups': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Backups
     * @description Local encrypted-backup readiness; execution needs an authenticated job.
     */
    get: operations['backups_api_v1_backups_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/calendar/authorization': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Start Authorization
     * @description Begin Nextcloud Login Flow v2 without sending a password to the browser.
     */
    post: operations['start_authorization_api_v1_calendar_authorization_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/calendar/authorization/{authorization_id}': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post?: never;
    /** Cancel Authorization */
    delete: operations['cancel_authorization_api_v1_calendar_authorization__authorization_id__delete'];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/calendar/authorization/{authorization_id}/poll': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Poll Authorization */
    post: operations['poll_authorization_api_v1_calendar_authorization__authorization_id__poll_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/calendar/auto-connect': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Auto Connect
     * @description Create the owner's encrypted device credential without password entry.
     */
    post: operations['auto_connect_api_v1_calendar_auto_connect_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/calendar/connection': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Connection */
    get: operations['get_connection_api_v1_calendar_connection_get'];
    /** Select Connection */
    put: operations['select_connection_api_v1_calendar_connection_put'];
    /** Save Connection */
    post: operations['save_connection_api_v1_calendar_connection_post'];
    /** Delete Connection */
    delete: operations['delete_connection_api_v1_calendar_connection_delete'];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/calendar/events': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Events */
    get: operations['list_events_api_v1_calendar_events_get'];
    put?: never;
    /** Create Event */
    post: operations['create_event_api_v1_calendar_events_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/calendar/events/{event_id}': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    /** Update Event */
    put: operations['update_event_api_v1_calendar_events__event_id__put'];
    post?: never;
    /** Delete Event */
    delete: operations['delete_event_api_v1_calendar_events__event_id__delete'];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/calendar/events/{event_id}/resolve': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Resolve Conflict
     * @description Keep this version (replacing Nextcloud's) or Nextcloud's (dropping the local edit).
     */
    post: operations['resolve_conflict_api_v1_calendar_events__event_id__resolve_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/calendar/sync': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Sync
     * @description Send events kept on Mu3Lab to the connected Nextcloud calendar, never overwriting its changes.
     */
    post: operations['sync_api_v1_calendar_sync_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/catalog': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Catalog
     * @description App descriptions and the core suite, from the app manifests.
     */
    get: operations['catalog_api_v1_catalog_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/chat/apps': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Chat Apps
     * @description The chat apps Mu3Lab can show on the Chat page, and which one it shows.
     */
    get: operations['chat_apps_api_v1_chat_apps_get'];
    /**
     * Choose Chat App
     * @description Make an installed chat app the household one.
     */
    put: operations['choose_chat_app_api_v1_chat_apps_put'];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/chat/connect': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Connect Chat */
    post: operations['connect_chat_api_v1_chat_connect_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/chat/connect/poll': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Poll Chat */
    post: operations['poll_chat_api_v1_chat_connect_poll_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/chat/status': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Chat Status */
    get: operations['chat_status_api_v1_chat_status_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/dismissed': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Dismiss
     * @description Hide one Home warning for this person until it changes.
     */
    post: operations['dismiss_api_v1_dismissed_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/identity': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Identity */
    get: operations['identity_api_v1_identity_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/jobs': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Jobs */
    get: operations['list_jobs_api_v1_jobs_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/jobs/core-install': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Start Core
     * @description Start the one guided core-suite job for an Authentik operator.
     */
    post: operations['start_core_api_v1_jobs_core_install_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/jobs/core-verify': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Verify Core
     * @description Queue live contract checks without reinstalling healthy services.
     */
    post: operations['verify_core_api_v1_jobs_core_verify_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/jobs/{job_id}': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Job Detail */
    get: operations['job_detail_api_v1_jobs__job_id__get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/jobs/{job_id}/cancel': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Cancel Job */
    post: operations['cancel_job_api_v1_jobs__job_id__cancel_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/jobs/{job_id}/events': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Job Events */
    get: operations['job_events_api_v1_jobs__job_id__events_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/jobs/{job_id}/retry': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Retry Job */
    post: operations['retry_job_api_v1_jobs__job_id__retry_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/mcp/servers': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Servers */
    get: operations['list_servers_api_v1_mcp_servers_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/mcp/servers/{server_id}/configuration': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    /** Put Configuration */
    put: operations['put_configuration_api_v1_mcp_servers__server_id__configuration_put'];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/mcp/servers/{server_id}/jobs': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Server Jobs */
    get: operations['server_jobs_api_v1_mcp_servers__server_id__jobs_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/mcp/servers/{server_id}/logs': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Server Logs */
    get: operations['server_logs_api_v1_mcp_servers__server_id__logs_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/mcp/servers/{server_id}/tools': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Tools */
    get: operations['list_tools_api_v1_mcp_servers__server_id__tools_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/mcp/servers/{server_id}/updates': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Server Updates */
    get: operations['server_updates_api_v1_mcp_servers__server_id__updates_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/mcp/servers/{server_id}/{action}': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Queue Action */
    post: operations['queue_action_api_v1_mcp_servers__server_id___action__post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/me/checklist': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Checklist */
    get: operations['get_checklist_api_v1_me_checklist_get'];
    /** Put Checklist */
    put: operations['put_checklist_api_v1_me_checklist_put'];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/people': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List People */
    get: operations['list_people_api_v1_people_get'];
    put?: never;
    /** Add Person */
    post: operations['add_person_api_v1_people_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/people/{username}/{action}': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Change Person */
    post: operations['change_person_api_v1_people__username___action__post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/providers': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * List Providers
     * @description List safe connection state; encrypted keys never cross this boundary.
     */
    get: operations['list_providers_api_v1_providers_get'];
    put?: never;
    /**
     * Save Provider
     * @description Accept one provider key without ever echoing or logging its value.
     *
     *     ``provider_id`` is optional: without it the provider is detected from the key.
     */
    post: operations['save_provider_api_v1_providers_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/providers/catalog': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Providers Catalog */
    get: operations['providers_catalog_api_v1_providers_catalog_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/providers/{provider_id}': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post?: never;
    /** Remove Provider */
    delete: operations['remove_provider_api_v1_providers__provider_id__delete'];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/providers/{provider_id}/disable': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Disable Provider */
    post: operations['disable_provider_api_v1_providers__provider_id__disable_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/providers/{provider_id}/enable': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Enable Provider */
    post: operations['enable_provider_api_v1_providers__provider_id__enable_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/providers/{provider_id}/models': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Provider Models */
    get: operations['provider_models_api_v1_providers__provider_id__models_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/providers/{provider_id}/verify': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Verify Provider */
    post: operations['verify_provider_api_v1_providers__provider_id__verify_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/provisioning': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Provisioning
     * @description Durable first-run state, never transient browser progress.
     */
    get: operations['provisioning_api_v1_provisioning_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/service-install-batches': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Latest Install Batch */
    get: operations['latest_install_batch_api_v1_service_install_batches_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/service-install-batches/{batch_id}': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Install Batch */
    get: operations['get_install_batch_api_v1_service_install_batches__batch_id__get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/service-install-batches/{batch_id}/cancel': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Cancel Install Batch */
    post: operations['cancel_install_batch_api_v1_service_install_batches__batch_id__cancel_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/service-install-batches/{batch_id}/downloads/{service_id}/pause': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Pause Download */
    post: operations['pause_download_api_v1_service_install_batches__batch_id__downloads__service_id__pause_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/service-install-batches/{batch_id}/downloads/{service_id}/resume': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Resume Download */
    post: operations['resume_download_api_v1_service_install_batches__batch_id__downloads__service_id__resume_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/service-install-batches/{batch_id}/order': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Reorder Batch */
    post: operations['reorder_batch_api_v1_service_install_batches__batch_id__order_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/service-install-batches/{batch_id}/parallel-downloads': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Set Parallel Downloads */
    post: operations['set_parallel_downloads_api_v1_service_install_batches__batch_id__parallel_downloads_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/service-install-batches/{batch_id}/reset': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Reset Install Batch */
    post: operations['reset_install_batch_api_v1_service_install_batches__batch_id__reset_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/service-install-batches/{batch_id}/resume': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Resume Install Batch */
    post: operations['resume_install_batch_api_v1_service_install_batches__batch_id__resume_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/services': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Services */
    get: operations['list_services_api_v1_services_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/services/install-batch': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Create Install Batch
     * @description Install apps in the order given; ``parallel_downloads`` apps download at once.
     */
    post: operations['create_install_batch_api_v1_services_install_batch_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/services/{service_id}/actions': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Service Action
     * @description Queue one allowlisted service lifecycle action for the durable worker.
     */
    post: operations['service_action_api_v1_services__service_id__actions_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/services/{service_id}/backups': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Service Backups
     * @description The app's local backups, newest first.
     */
    get: operations['service_backups_api_v1_services__service_id__backups_get'];
    put?: never;
    /**
     * Back Up Service
     * @description Ask Kopia's backup service to back the app up now, exactly as a scheduled run does.
     */
    post: operations['back_up_service_api_v1_services__service_id__backups_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/services/{service_id}/configuration': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Service Configuration */
    get: operations['get_service_configuration_api_v1_services__service_id__configuration_get'];
    /** Put Service Configuration */
    put: operations['put_service_configuration_api_v1_services__service_id__configuration_put'];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/services/{service_id}/initialization/confirm': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Confirm Service Initialization */
    post: operations['confirm_service_initialization_api_v1_services__service_id__initialization_confirm_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/services/{service_id}/logs': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Service Logs
     * @description Bounded, redacted logs for a curated Compose service.
     */
    get: operations['service_logs_api_v1_services__service_id__logs_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/services/{service_id}/updates': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Service Updates
     * @description Compare the installed release with the one Mu3Lab approves; no network needed.
     */
    get: operations['service_updates_api_v1_services__service_id__updates_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/session': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Session
     * @description Issue browser-readable CSRF material only after verified proxy identity.
     */
    get: operations['session_api_v1_session_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/setup/core': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Core Setup
     * @description Describe the mandatory suite without claiming it is runnable early.
     */
    get: operations['core_setup_api_v1_setup_core_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/snapshot': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Snapshot */
    get: operations['snapshot_api_v1_snapshot_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/system': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** System */
    get: operations['system_api_v1_system_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/system/backups/platform': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Platform Backup Status
     * @description Mu3Lab's own backup and each core app's, from their own evidence.
     */
    get: operations['platform_backup_status_api_v1_system_backups_platform_get'];
    put?: never;
    /**
     * Start Platform Backup
     * @description Ask Kopia's backup service to back up Mu3Lab itself and each core app now, one at a time.
     */
    post: operations['start_platform_backup_api_v1_system_backups_platform_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/system/backups/recovery-sheet': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Open Recovery Sheet
     * @description The backup password and where the backups are, for printing. Each opening is audited.
     */
    post: operations['open_recovery_sheet_api_v1_system_backups_recovery_sheet_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/system/backups/schedule': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Backup Schedule Status
     * @description When scheduled backups run, Kopia's next run, and what did not finish.
     */
    get: operations['backup_schedule_status_api_v1_system_backups_schedule_get'];
    /** Update Backup Schedule */
    put: operations['update_backup_schedule_api_v1_system_backups_schedule_put'];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/system/changelog': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Mu3Lab Changelog
     * @description What changed in the installed release and the few before it (What's new).
     */
    get: operations['mu3lab_changelog_api_v1_system_changelog_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/system/cleanup': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Free Up Space
     * @description Remove removed apps' leftovers and old app versions; their data only when asked.
     */
    post: operations['free_up_space_api_v1_system_cleanup_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/system/config': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** System Config */
    get: operations['system_config_api_v1_system_config_get'];
    /** Update System Config */
    put: operations['update_system_config_api_v1_system_config_put'];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/system/update': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Mu3Lab Update
     * @description Whether GitHub has a newer Mu3Lab this copy can move to; fetches at most every few minutes.
     */
    get: operations['mu3lab_update_api_v1_system_update_get'];
    put?: never;
    /**
     * Start Mu3Lab Update
     * @description Queue the self-update. It restarts the dashboard and worker, so nothing else may be running.
     */
    post: operations['start_mu3lab_update_api_v1_system_update_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/vault/setup': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Setup Vault
     * @description Use the master password for this one request only; it is never stored or logged.
     */
    post: operations['setup_vault_api_v1_vault_setup_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/vault/status': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Vault Status */
    get: operations['vault_status_api_v1_vault_status_get'];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/vault/sync': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Sync Now
     * @description Save waiting logins to everyone's vault now instead of at the next automatic run.
     */
    post: operations['sync_now_api_v1_vault_sync_post'];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  '/api/v1/voice-key': {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Voice Key Status */
    get: operations['voice_key_status_api_v1_voice_key_get'];
    put?: never;
    /** Create Voice Key */
    post: operations['create_voice_key_api_v1_voice_key_post'];
    /** Revoke Voice Key */
    delete: operations['revoke_voice_key_api_v1_voice_key_delete'];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
}
export type webhooks = Record<string, never>;
export interface components {
  schemas: {
    /** AiModelSettings */
    AiModelSettings: {
      /** Changed At */
      changed_at: string;
      /** Changed By */
      changed_by: string;
      /** Chat Model */
      chat_model: string;
      /** Embedding Model */
      embedding_model: string;
      /**
       * Privacy
       * @enum {string}
       */
      privacy: 'outside_allowed' | 'local_only';
      /** Runner */
      runner: string;
    };
    /**
     * AiModels
     * @description Where AI requests may go, the model runner, the local chat model and the search (embedding) model.
     */
    AiModels: {
      /** Active Job Id */
      active_job_id: string;
      /** Chat Models */
      chat_models: components['schemas']['LocalModel'][];
      /** Embedding Locked By */
      embedding_locked_by: string[];
      /** Embedding Models */
      embedding_models: components['schemas']['LocalModel'][];
      /** Ok */
      ok: boolean;
      /** Runner */
      runner: string;
      /** Runner Reachable */
      runner_reachable: boolean;
      /** Runners */
      runners: components['schemas']['ModelRunnerOption'][];
      settings: components['schemas']['AiModelSettings'];
      voice: components['schemas']['AiVoiceSettings'];
    };
    /** AiModelsRequest */
    AiModelsRequest: {
      /**
       * Chat Model
       * @default
       */
      chat_model?: string;
      /** Embedding Model */
      embedding_model: string;
      /**
       * Privacy
       * @enum {string}
       */
      privacy: 'outside_allowed' | 'local_only';
      /**
       * Runner
       * @default
       */
      runner?: string;
    };
    /** AiVoiceRequest */
    AiVoiceRequest: {
      /**
       * Model
       * @default
       */
      model?: string;
      /**
       * Privacy
       * @enum {string}
       */
      privacy: 'outside_allowed' | 'local_only';
    };
    /**
     * AiVoiceSettings
     * @description Where the voice assistant's answers come from, independent of chat.
     */
    AiVoiceSettings: {
      /** Changed At */
      changed_at: string;
      /** Changed By */
      changed_by: string;
      /** Matches Chat */
      matches_chat: boolean;
      /** Model */
      model: string;
      /**
       * Privacy
       * @enum {string}
       */
      privacy: 'outside_allowed' | 'local_only';
    };
    /** ApiErrorResponse */
    ApiErrorResponse: {
      /** Blocking Job Id */
      blocking_job_id?: string | null;
      /** Code */
      code: string;
      /** Error */
      error:
        | string
        | {
            [key: string]: components['schemas']['JsonValue'];
          };
      /** Message */
      message: string;
      /**
       * Ok
       * @default false
       * @constant
       */
      ok?: false;
      /** Recommended Action */
      recommended_action: string;
    };
    /** AppSize */
    AppSize: {
      /** Disk Bytes */
      disk_bytes: number;
      /** Download Bytes */
      download_bytes: number;
      /** Needed Bytes */
      needed_bytes: number;
      /** Needed Disk Bytes */
      needed_disk_bytes: number;
    };
    /** AppSizesResponse */
    AppSizesResponse: {
      /** Apps */
      apps: {
        [key: string]: components['schemas']['AppSizesResponseAppsValue'];
      };
      /** Free Bytes */
      free_bytes: number;
      /** Measured */
      measured: boolean;
      /**
       * Ok
       * @default true
       */
      ok?: boolean;
      selection: components['schemas']['AppSize'];
    };
    /** AppSizesResponseAppsValue */
    AppSizesResponseAppsValue: {
      /** Complete */
      complete: boolean;
      /** Disk Bytes */
      disk_bytes: number;
      /** Download Bytes */
      download_bytes: number;
      /** Needed Bytes */
      needed_bytes: number;
      /** Needed Disk Bytes */
      needed_disk_bytes: number;
      /** Updated At */
      updated_at: string;
    };
    /** AuditEvent */
    AuditEvent: {
      /** Actor */
      actor: string;
      /** Created At */
      created_at: string;
      /** Detail */
      detail: string;
      /** Event */
      event: string;
      /** Id */
      id: number;
      /** Job Id */
      job_id: string | null;
    };
    /** AuditResponse */
    AuditResponse: {
      /** Available */
      available: boolean;
      /** Events */
      events: components['schemas']['AuditEvent'][];
      /** Ok */
      ok: boolean;
    };
    /**
     * BackupAttention
     * @description One backup problem Home shows administrators; empty while backups keep up with the schedule.
     */
    BackupAttention: {
      /** Key */
      key: string;
      /** Message */
      message: string;
    };
    /**
     * BackupProtection
     * @description One app's backup evidence; never inferred from another app's backups.
     */
    BackupProtection: {
      /**
       * Completed At
       * @default
       */
      completed_at?: string;
      /**
       * Detail
       * @default
       */
      detail?: string;
      /**
       * Off Device
       * @default false
       */
      off_device?: boolean;
      /**
       * Restore Tested At
       * @default
       */
      restore_tested_at?: string;
      /**
       * Snapshot Id
       * @default
       */
      snapshot_id?: string;
      /**
       * State
       * @enum {string}
       */
      state: 'missing' | 'stale' | 'unchecked' | 'checked';
      /**
       * Verification
       * @default none
       * @enum {string}
       */
      verification?: 'none' | 'structure' | 'data';
    };
    /** BackupReadiness */
    BackupReadiness: {
      /**
       * Available
       * @default false
       */
      available?: boolean;
      /** Detail */
      detail?: string | null;
      /**
       * Engine
       * @default kopia
       */
      engine?: string;
      /** Integrity Verified */
      integrity_verified?: boolean | null;
      /** Last Verified At */
      last_verified_at?: string | null;
      /** Legacy Repository */
      legacy_repository?: string | null;
      /** Off Device */
      off_device?: boolean | null;
      /**
       * Ok
       * @default true
       */
      ok?: boolean;
      /**
       * Repository Path
       * @default
       */
      repository_path?: string;
      /** Repository Present */
      repository_present?: boolean | null;
      /** Retention */
      retention?: {
        [key: string]: number;
      };
      /** Snapshot Present */
      snapshot_present?: boolean | null;
      /** State */
      state?: string | null;
    };
    /**
     * BackupSchedule
     * @description When Kopia's backup service runs backups, in this server's local time. Weekday: Monday is 0, Sunday 6.
     */
    BackupSchedule: {
      /**
       * Frequency
       * @enum {string}
       */
      frequency: 'off' | '6h' | '12h' | 'daily' | 'weekly';
      /** Next Run At */
      next_run_at: string;
      /** Ok */
      ok: boolean;
      /** Problems */
      problems: string[];
      /**
       * Service Installed
       * @default true
       */
      service_installed?: boolean;
      /** Service Running */
      service_running: boolean;
      /** Time */
      time: string;
      /** Weekday */
      weekday: number;
    };
    /** BackupScheduleRequest */
    BackupScheduleRequest: {
      /**
       * Frequency
       * @enum {string}
       */
      frequency: 'off' | '6h' | '12h' | 'daily' | 'weekly';
      /** Time */
      time: string;
      /** Weekday */
      weekday: number;
    };
    /** BackupSnapshot */
    BackupSnapshot: {
      /**
       * Complete
       * @default true
       */
      complete?: boolean;
      /** Id */
      id: string;
      /** Paths */
      paths: string[];
      /** Reason */
      reason: string;
      /**
       * Release Id
       * @default
       */
      release_id?: string;
      /** Short Id */
      short_id: string;
      /** Time */
      time: string;
      /** Version */
      version: string;
    };
    /**
     * BackupStarted
     * @description Kopia's backup service was asked to back up now; progress shows on the app.
     */
    BackupStarted: {
      /** Detail */
      detail: string;
      /** Ok */
      ok: boolean;
    };
    /** BackupsResponse */
    BackupsResponse: {
      /** Backups */
      backups: components['schemas']['BackupSnapshot'][];
      /** Ok */
      ok: boolean;
      protection?: components['schemas']['BackupProtection'] | null;
      readiness: components['schemas']['BackupReadiness'];
      /** Service Id */
      service_id: string;
    };
    /** BatchOrderRequest */
    BatchOrderRequest: {
      /** Service Ids */
      service_ids: string[];
    };
    /** CalendarAuthorization */
    CalendarAuthorization: {
      /** Authorization Id */
      authorization_id?: string | null;
      connection?: components['schemas']['CalendarConnection'] | null;
      /** Error */
      error?: string | null;
      /** Expires At */
      expires_at?: string | null;
      /** Login Url */
      login_url?: string | null;
      /** Ok */
      ok: boolean;
      /** Poll After Ms */
      poll_after_ms?: number | null;
      /** State */
      state: 'awaiting_user' | 'pending' | 'connected' | 'expired' | 'failed';
    };
    /** CalendarConnectRequest */
    CalendarConnectRequest: {
      /** App Password */
      app_password: string;
      /** Username */
      username: string;
    };
    /** CalendarConnection */
    CalendarConnection: {
      /** Calendars */
      calendars: components['schemas']['CalendarConnectionCalendarsItem'][];
      /** Error */
      error: string;
      /** Last Success At */
      last_success_at: string;
      /** Ok */
      ok: boolean;
      /** Selected Calendar Id */
      selected_calendar_id: string;
      /** State */
      state:
        | 'not_installed'
        | 'service_stopped'
        | 'sso_not_ready'
        | 'not_connected'
        | 'awaiting_approval'
        | 'connected'
        | 'authentication_expired'
        | 'unavailable';
      /** Username Hint */
      username_hint: string;
    };
    /** CalendarConnectionCalendarsItem */
    CalendarConnectionCalendarsItem: {
      /** Id */
      id: string;
      /** Name */
      name: string;
    };
    /** CalendarDeleteRequest */
    CalendarDeleteRequest: {
      /**
       * Revision
       * @default
       */
      revision?: string;
    };
    /** CalendarEvent */
    CalendarEvent: {
      /** All Day */
      all_day: boolean;
      /** Conflict */
      conflict?: string | null;
      /** Editable */
      editable?: boolean | null;
      /** End */
      end: string;
      /** Id */
      id: string;
      /** Local */
      local?: boolean | null;
      /** Revision */
      revision?: string | null;
      /** Start */
      start: string;
      /** Title */
      title: string;
    };
    /** CalendarEventRequest */
    CalendarEventRequest: {
      /**
       * All Day
       * @default false
       */
      all_day?: boolean;
      /** End */
      end: string;
      /**
       * Revision
       * @default
       */
      revision?: string;
      /** Start */
      start: string;
      /** Title */
      title: string;
    };
    /** CalendarEvents */
    CalendarEvents: {
      calendar?: components['schemas']['CalendarEventsCalendar'] | null;
      /** Error */
      error?: string | null;
      /** Events */
      events: components['schemas']['CalendarEvent'][];
      /** Fetched At */
      fetched_at?: string | null;
      /** Nextcloud Ready */
      nextcloud_ready?: boolean | null;
      /** Ok */
      ok: boolean;
      /** Pending Upload */
      pending_upload?: boolean | null;
      /** State */
      state: string;
    };
    /** CalendarEventsCalendar */
    CalendarEventsCalendar: {
      /** Id */
      id: string;
      /** Name */
      name: string;
    };
    /** CalendarResolveRequest */
    CalendarResolveRequest: {
      /**
       * Keep
       * @enum {string}
       */
      keep: 'mine' | 'server';
    };
    /** CalendarSelectRequest */
    CalendarSelectRequest: {
      /** Calendar Id */
      calendar_id: string;
    };
    /**
     * CalendarSync
     * @description What one sync sent, and what it could not send without asking.
     */
    CalendarSync: {
      /** Conflicts */
      conflicts: string[];
      /** Kept */
      kept: string[];
      /** Moved */
      moved: number;
      /** Ok */
      ok: boolean;
    };
    /** CatalogProfile */
    CatalogProfile: {
      /** Description */
      description: string;
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Services */
      services: string[];
    };
    /** CatalogResponse */
    CatalogResponse: {
      /** Ok */
      ok: boolean;
      /** Profiles */
      profiles: components['schemas']['CatalogProfile'][];
      /** Services */
      services: {
        [key: string]: components['schemas']['CatalogService'];
      };
    };
    /** CatalogService */
    CatalogService: {
      /** Category */
      category?: string | null;
      /** Integrations */
      integrations?: string[] | null;
      /** Resource Guidance */
      resource_guidance?: string | null;
      /** Stage Label */
      stage_label?: string | null;
      /** Summary */
      summary: string;
      /** Tagline */
      tagline?: string | null;
    };
    /** ChatAppChoice */
    ChatAppChoice: {
      /** Id */
      id: string;
      /** Installed */
      installed: boolean;
      /** Name */
      name: string;
      /** Selected */
      selected: boolean;
    };
    /** ChatAppRequest */
    ChatAppRequest: {
      /** App Id */
      app_id: string;
    };
    /** ChatApps */
    ChatApps: {
      /** Apps */
      apps: components['schemas']['ChatAppChoice'][];
      /** Ok */
      ok: boolean;
    };
    /** ChatAssistant */
    ChatAssistant: {
      /** Detail */
      detail: string;
      /** Id */
      id: string;
      /** Name */
      name: string;
      /**
       * Status
       * @enum {string}
       */
      status:
        | 'ready'
        | 'paused'
        | 'pending'
        | 'failed'
        | 'not_connected'
        | 'connector_down'
        | 'needs_review'
        | 'operator_only';
    };
    /** ChatConnectResponse */
    ChatConnectResponse: {
      /** Interval */
      interval: number;
      /** Ok */
      ok: boolean;
      /** User Code */
      user_code: string;
      /** Verification Uri Complete */
      verification_uri_complete: string;
    };
    /** ChatPollResponse */
    ChatPollResponse: {
      /** Detail */
      detail?: string | null;
      /** Interval */
      interval?: number | null;
      /** Ok */
      ok: boolean;
      /** Ready */
      ready?: boolean | null;
      /** State */
      state: string;
    };
    /** ChatProvider */
    ChatProvider: {
      /** Authentication */
      authentication: string;
      /** Detail */
      detail: string;
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Ready */
      ready: boolean;
      /** Url */
      url: string;
    };
    /** ChatStatus */
    ChatStatus: {
      /**
       * App Id
       * @default
       */
      app_id?: string;
      /**
       * App Name
       * @default
       */
      app_name?: string;
      /** Assistants */
      assistants?: components['schemas']['ChatAssistant'][] | null;
      /** Authentication */
      authentication: string;
      /** Connected */
      connected?: boolean | null;
      /** Detail */
      detail: string;
      /** Mcp Enabled Count */
      mcp_enabled_count: number;
      /** Ok */
      ok: boolean;
      /** Providers */
      providers?: components['schemas']['ChatProvider'][] | null;
      /** Ready */
      ready: boolean;
      /** Url */
      url: string;
    };
    /** ChecklistRequest */
    ChecklistRequest: {
      /** Items */
      items?: {
        [key: string]: boolean;
      };
    };
    /** ChecklistResponse */
    ChecklistResponse: {
      /** Items */
      items: {
        [key: string]: boolean;
      };
      /** Ok */
      ok: boolean;
    };
    /** CleanupRequest */
    CleanupRequest: {
      /**
       * Delete Data
       * @default false
       */
      delete_data?: boolean;
    };
    /** ComputeRequest */
    ComputeRequest: {
      /**
       * Compute Mode
       * @enum {string}
       */
      compute_mode: 'auto' | 'cpu' | 'nvidia' | 'amd';
    };
    /** ConfigurationRequest */
    ConfigurationRequest: {
      /** Expected Revision */
      expected_revision?: number | null;
      /** Values */
      values?: {
        [key: string]: string | boolean | number | null;
      };
    };
    /** CoreAppBackup */
    CoreAppBackup: {
      /** Id */
      id: string;
      /** Included */
      included: boolean;
      /** Name */
      name: string;
      protection?: components['schemas']['BackupProtection'] | null;
      /**
       * Reason
       * @default
       */
      reason?: string;
    };
    /** CoreAppProgress */
    CoreAppProgress: {
      download?: components['schemas']['ImageDownload'] | null;
      /** Name */
      name: string;
      /** Service Id */
      service_id: string;
      /**
       * State
       * @enum {string}
       */
      state: 'waiting' | 'downloading' | 'downloaded' | 'installing' | 'ready' | 'failed';
    };
    /** CoreSetupResponse */
    CoreSetupResponse: {
      /** Apps */
      apps?: components['schemas']['CoreAppProgress'][] | null;
      capacity?: components['schemas']['CoreSetupResponseCapacity'] | null;
      current_job?: components['schemas']['Job'] | null;
      /** Missing Manifests */
      missing_manifests: string[];
      /** Next Action */
      next_action: string;
      /** Ok */
      ok: boolean;
      provisioning?: components['schemas']['ProvisioningResponse'] | null;
      /** Ready To Run */
      ready_to_run: boolean;
      /** Services */
      services: string[];
    };
    /** CoreSetupResponseCapacity */
    CoreSetupResponseCapacity: {
      /** Disk Free */
      disk_free?: number | null;
      /** Docker Ready */
      docker_ready?: boolean | null;
      /** Memory Total */
      memory_total?: number | null;
      /** Ok */
      ok: boolean;
      /** Reasons */
      reasons?: string[] | null;
    };
    /** DiskShare */
    DiskShare: {
      /** Data */
      data: number;
      /** Program */
      program: number;
    };
    /** DiskUsage */
    DiskUsage: {
      /** Core Parts */
      core_parts?: {
        [key: string]: components['schemas']['DiskShare'];
      };
      /** Measured At */
      measured_at: string;
      /** Owners */
      owners: {
        [key: string]: components['schemas']['DiskShare'];
      };
      /** Removed Folders */
      removed_folders?: string[];
    };
    /** DismissRequest */
    DismissRequest: {
      /** Warning */
      warning: string;
    };
    /** DismissedResponse */
    DismissedResponse: {
      /** Dismissed */
      dismissed: string[];
      /**
       * Ok
       * @default true
       */
      ok?: boolean;
    };
    /**
     * GpuMemory
     * @description Graphics memory of the NVIDIA GPUs, all cards added up, in bytes.
     */
    GpuMemory: {
      /** By Core Part */
      by_core_part?: {
        [key: string]: number;
      };
      /** By Owner */
      by_owner?: {
        [key: string]: number;
      };
      /** Name */
      name: string;
      /** Total */
      total: number;
      /** Used */
      used: number;
    };
    /** Health */
    Health: {
      /** Ok */
      ok: boolean;
      /** Version */
      version: string;
    };
    /** IdentityResponse */
    IdentityResponse: {
      /** Control Plane Auth */
      control_plane_auth: string;
      /** Detail */
      detail: string;
      /** Display Name */
      display_name?: string | null;
      /** Email */
      email?: string | null;
      /** Groups */
      groups?: string[] | null;
      /** Is Admin */
      is_admin?: boolean | null;
      /** Ok */
      ok: boolean;
      /** Role */
      role?: 'admin' | 'member' | '' | null;
      /** Subject Id */
      subject_id?: string | null;
      /** Username */
      username?: string | null;
      /** Writes Enabled */
      writes_enabled: boolean;
    };
    /** ImageDownload */
    ImageDownload: {
      /** Connections */
      connections: number;
      /** Done Bytes */
      done_bytes: number;
      /** Images Done */
      images_done: number;
      /** Images Total */
      images_total: number;
      /** Rate Bps */
      rate_bps: number;
      /** State */
      state: 'downloading' | 'loading' | 'done' | 'docker';
      /** Total Bytes */
      total_bytes: number;
      /** Updated At */
      updated_at: string;
    };
    /** InstallBatch */
    InstallBatch: {
      /** Actor */
      actor: string;
      /** Created At */
      created_at: string;
      /** Current Ordinal */
      current_ordinal: number;
      error?: components['schemas']['InstallBatchError'] | null;
      /** Id */
      id: string;
      /** Items */
      items: components['schemas']['InstallBatchItem'][];
      /** Parallel Downloads */
      parallel_downloads: number;
      /** State */
      state:
        | 'queued'
        | 'running'
        | 'paused'
        | 'succeeded'
        | 'cancelled'
        | 'completed_with_failures'
        | 'resetting'
        | 'reset_failed'
        | 'reset';
      /** Updated At */
      updated_at: string;
    };
    /** InstallBatchError */
    InstallBatchError: {
      /** Code */
      code?: string | null;
      /** Message */
      message?: string | null;
    };
    /** InstallBatchEvent */
    InstallBatchEvent: {
      /** Created At */
      created_at: string;
      /** Detail */
      detail: string;
      /** Event */
      event: string;
      /** Id */
      id: number;
      /** Job Id */
      job_id: string;
    };
    /** InstallBatchItem */
    InstallBatchItem: {
      /** Batch Id */
      batch_id: string;
      /** Completed At */
      completed_at: string;
      download?: components['schemas']['ImageDownload'] | null;
      /** Download Error */
      download_error?: string | null;
      /** Download State */
      download_state: '' | 'downloading' | 'paused' | 'ready';
      /** Error Json */
      error_json?: string | null;
      /** Explicitly Selected */
      explicitly_selected: number;
      /** Job Id */
      job_id: string;
      /** Ordinal */
      ordinal: number;
      /** Priority */
      priority: number;
      /** Service Id */
      service_id: string;
      /** Started At */
      started_at: string;
      /** State */
      state: string;
    };
    /** InstallBatchJob */
    InstallBatchJob: {
      /** Detail */
      detail: string;
      /** Events */
      events: components['schemas']['InstallBatchEvent'][];
      /** Id */
      id: string;
      /** State */
      state: string;
      /** Step Id */
      step_id: string;
    };
    /** InstallBatchRequest */
    InstallBatchRequest: {
      /**
       * Parallel Downloads
       * @default 3
       */
      parallel_downloads?: number;
      /** Service Ids */
      service_ids: string[];
    };
    /** InstallBatchResponse */
    InstallBatchResponse: {
      batch: components['schemas']['InstallBatch'] | null;
      current_job?: components['schemas']['InstallBatchJob'] | null;
      /** Ok */
      ok: boolean;
      /** Reset */
      reset?: boolean | null;
    };
    /** Job */
    Job: {
      /** Action */
      action: string;
      /** Actor */
      actor: string;
      /** Created At */
      created_at: string;
      /** Detail */
      detail: string;
      /** Error Code */
      error_code?: string | null;
      /** Id */
      id: string;
      /** Kind */
      kind: string;
      /** Service Id */
      service_id: string;
      /** State */
      state: string;
      /** Step Id */
      step_id?: string | null;
      /** Updated At */
      updated_at: string;
    };
    /** JobDetailResponse */
    JobDetailResponse: {
      download?: components['schemas']['ImageDownload'] | null;
      /** Events */
      events: components['schemas']['AuditEvent'][];
      job: components['schemas']['Job'];
      /** Ok */
      ok: boolean;
    };
    /** JobEventsResponse */
    JobEventsResponse: {
      /** Events */
      events: components['schemas']['AuditEvent'][];
      /** Ok */
      ok: boolean;
    };
    /** JobResponse */
    JobResponse: {
      /** Duplicate */
      duplicate?: boolean | null;
      job: components['schemas']['QueuedJob'];
      /** Ok */
      ok: boolean;
    };
    /** JobsResponse */
    JobsResponse: {
      /** Available */
      available: boolean;
      /** Jobs */
      jobs: components['schemas']['Job'][];
      /** Ok */
      ok: boolean;
    };
    JsonValue: unknown;
    /**
     * LocalModel
     * @description One reviewed local model. Sizes are Ollama's download sizes; memory is an estimate.
     */
    LocalModel: {
      /** Dimensions */
      dimensions: number;
      /** Download Gb */
      download_gb: number;
      /** Downloaded */
      downloaded: boolean;
      /** Id */
      id: string;
      /** Memory Gb */
      memory_gb: number;
      /** Name */
      name: string;
      /** Note */
      note: string;
      /** Tag */
      tag: string;
    };
    /** McpJobsResponse */
    McpJobsResponse: {
      /** Jobs */
      jobs: components['schemas']['Job'][];
      /** Ok */
      ok: boolean;
      /** Server Id */
      server_id: string;
    };
    /** McpLogsResponse */
    McpLogsResponse: {
      /** Lines */
      lines: string[];
      /** Ok */
      ok: boolean;
      /** Server Id */
      server_id: string;
    };
    /** McpRegistryResponse */
    McpRegistryResponse: {
      /** Ok */
      ok: boolean;
      /** Policy */
      policy: string;
      /** Servers */
      servers: components['schemas']['McpServer'][];
      /** Summary */
      summary: {
        [key: string]: number;
      };
    };
    /** McpServer */
    McpServer: {
      /** App State */
      app_state: string;
      auth: components['schemas']['McpServerAuth'];
      /** Configuration */
      configuration?: components['schemas']['ServiceConfigField'][] | null;
      /** Enabled */
      enabled: boolean;
      /** Error */
      error?: string | null;
      /** Id */
      id: string;
      /** Kind */
      kind: string;
      /** Last Verified At */
      last_verified_at?: string | null;
      /** Manage Url */
      manage_url?: string | null;
      /** Name */
      name: string;
      /** Prepared */
      prepared?: boolean | null;
      review?: components['schemas']['McpServerReview'] | null;
      /** Service Id */
      service_id: string;
      /** State */
      state:
        | 'live'
        | 'degraded'
        | 'authentication_required'
        | 'disabled'
        | 'prepared'
        | 'unavailable'
        | 'starting'
        | 'incompatible'
        | 'failed'
        | 'stopped';
      /** Tools */
      tools: components['schemas']['McpServerToolsItem'][];
      /** Transport */
      transport: string;
    };
    /** McpServerAuth */
    McpServerAuth: {
      /** Auto Provision */
      auto_provision: boolean;
      /** Auto Provision Note */
      auto_provision_note?: string | null;
      /** Configured */
      configured: boolean;
      /** Type */
      type: string;
    };
    /** McpServerResponse */
    McpServerResponse: {
      /** Ok */
      ok: boolean;
      server: components['schemas']['McpServer'];
    };
    /** McpServerReview */
    McpServerReview: {
      /** Note */
      note?: string | null;
      /** Preferred */
      preferred: boolean;
      /** Repository */
      repository: string;
      /** Revision */
      revision: string;
      /** Status */
      status: string;
    };
    /** McpServerToolsItem */
    McpServerToolsItem: {
      /** Enabled */
      enabled: boolean;
      /** Id */
      id: string;
      /** Parameters */
      parameters?: {
        [key: string]: components['schemas']['JsonValue'];
      } | null;
      /** Risk */
      risk: string;
      /** Title */
      title: string;
    };
    /** McpToolsResponse */
    McpToolsResponse: {
      /** Ok */
      ok: boolean;
      /** Server Id */
      server_id: string;
      /** State */
      state: string;
      /** Tools */
      tools: components['schemas']['McpServerToolsItem'][];
    };
    /** McpUpdatesResponse */
    McpUpdatesResponse: {
      /** Current Version */
      current_version: string;
      /** Detail */
      detail: string;
      /** Ok */
      ok: boolean;
      /** Reviewed Update */
      reviewed_update: {
        [key: string]: string;
      } | null;
      /** Server Id */
      server_id: string;
    };
    /** Metric */
    Metric: {
      /** Percent */
      percent: number;
      /** Total */
      total: number;
      /** Used */
      used: number;
    };
    /** MobileClient */
    MobileClient: {
      /** Caveat */
      caveat: string;
      /** Fallback */
      fallback: boolean;
      /** Homepage */
      homepage: string | null;
      /** Id */
      id: string;
      /** Install */
      install: {
        [key: string]: string;
      };
      /** Kind */
      kind: 'native' | 'pwa' | 'web';
      /** Name */
      name: string;
      /** Platforms */
      platforms: ('ios' | 'android' | 'web')[];
      /** Setup */
      setup: 'server_url' | 'pwa' | 'web' | 'api_token' | 'device_qr' | 'developer_mode';
      /** Source */
      source: string | null;
      /** Steps */
      steps: string[];
      /** Summary */
      summary: string;
      /** Support */
      support: 'official' | 'community' | 'experimental';
    };
    /** ModelRunnerOption */
    ModelRunnerOption: {
      /** Has Ui */
      has_ui: boolean;
      /** Id */
      id: string;
      /** Installed */
      installed: boolean;
      /** Name */
      name: string;
    };
    /** Mu3LabChangelog */
    Mu3LabChangelog: {
      /** Ok */
      ok: boolean;
      /** Releases */
      releases: components['schemas']['Mu3LabRelease'][];
    };
    /**
     * Mu3LabDevelopment
     * @description One merged branch or pull request (or a run of direct commits) inside a release.
     */
    Mu3LabDevelopment: {
      /**
       * Branch
       * @default
       */
      branch?: string;
      /** Changes */
      changes: string[];
      /** Date */
      date: string;
      /** Title */
      title: string;
    };
    /**
     * Mu3LabRelease
     * @description One release's changes; a development copy has no version.
     */
    Mu3LabRelease: {
      /** Changes */
      changes: string[];
      /**
       * Commit
       * @default
       */
      commit?: string;
      /** Date */
      date: string;
      /** Developments */
      developments?: components['schemas']['Mu3LabDevelopment'][];
      /** Version */
      version: string;
    };
    /** Mu3LabUpdate */
    Mu3LabUpdate: {
      /** Available */
      available: boolean;
      /** Behind */
      behind: number;
      /** Blocked Reason */
      blocked_reason: string;
      /** Changes */
      changes: string[];
      /** Checked At */
      checked_at: number;
      /** Commit */
      commit: string;
      /** Date */
      date: string;
      last_update?: components['schemas']['Mu3LabUpdateResult'] | null;
      /** Ok */
      ok: boolean;
      /** Version */
      version: string;
    };
    /**
     * Mu3LabUpdateResult
     * @description How the last update ended: verified, rolled_back or needs_attention.
     */
    Mu3LabUpdateResult: {
      /** At */
      at: string;
      /** Detail */
      detail: string;
      /**
       * From
       * @default
       */
      from?: string;
      /** State */
      state: string;
      /**
       * To
       * @default
       */
      to?: string;
    };
    /** OkResponse */
    OkResponse: {
      /** Id */
      id?: string | null;
      /** Message */
      message?: string | null;
      /** Ok */
      ok: boolean;
      /** Warning */
      warning?: string | null;
    };
    /** ParallelDownloadsRequest */
    ParallelDownloadsRequest: {
      /** Parallel Downloads */
      parallel_downloads: number;
    };
    /** PeopleResponse */
    PeopleResponse: {
      /** Ok */
      ok: boolean;
      /** People */
      people: components['schemas']['Person'][];
    };
    /** Person */
    Person: {
      access?: components['schemas']['PersonAccess'] | null;
      /** Active */
      active: boolean;
      /** Email */
      email: string;
      /** Last Login */
      last_login: string;
      /** Name */
      name: string;
      /**
       * Role
       * @enum {string}
       */
      role: 'admin' | 'member' | '';
      /** Uid */
      uid: string;
      /** Username */
      username: string;
    };
    /** PersonAccess */
    PersonAccess: {
      /** Detail */
      detail: string;
      /**
       * Kind
       * @enum {string}
       */
      kind: 'deactivated' | 'demoted';
      /** Pending */
      pending: number;
      /** Pending Targets */
      pending_targets: string[];
      /** Since */
      since: string;
      /**
       * State
       * @enum {string}
       */
      state: 'pending' | 'complete';
    };
    /** PersonChangeRequest */
    PersonChangeRequest: {
      /**
       * Role
       * @default
       * @enum {string}
       */
      role?: 'member' | 'admin' | '';
    };
    /** PersonInvite */
    PersonInvite: {
      /** Url */
      url: string;
      /** Valid Hours */
      valid_hours: number;
    };
    /** PersonRequest */
    PersonRequest: {
      /** Email */
      email: string;
      /** Name */
      name: string;
      /**
       * Role
       * @default member
       * @enum {string}
       */
      role?: 'member' | 'admin';
    };
    /** PersonResponse */
    PersonResponse: {
      invite?: components['schemas']['PersonInvite'] | null;
      /** Ok */
      ok: boolean;
      person: components['schemas']['Person'];
    };
    /**
     * PlatformBackupStatus
     * @description Mu3Lab's own state and each core app, each from its own backup evidence.
     */
    PlatformBackupStatus: {
      /** Core Apps */
      core_apps: components['schemas']['CoreAppBackup'][];
      /** Ok */
      ok: boolean;
      platform: components['schemas']['BackupProtection'];
      recovery_sheet?: components['schemas']['RecoverySheetStatus'] | null;
    };
    /** ProviderCatalogItem */
    ProviderCatalogItem: {
      /** Account */
      account?: 'email' | 'google' | null;
      /** Free Tier */
      free_tier?: string | null;
      /** Id */
      id: string;
      /** Instructions */
      instructions: string;
      /** Key Hint */
      key_hint: string;
      /** Key Pattern */
      key_pattern?: string | null;
      /** Keys Url */
      keys_url?: string | null;
      /** Name */
      name: string;
      /** Payment Required */
      payment_required?: boolean | null;
      /** Prefixes */
      prefixes: string[];
      /** Recommended */
      recommended?: boolean | null;
      /** Signup Url */
      signup_url?: string | null;
    };
    /** ProviderCatalogResponse */
    ProviderCatalogResponse: {
      /** Ok */
      ok: boolean;
      /** Providers */
      providers: components['schemas']['ProviderCatalogItem'][];
      /** Recommended Minimum */
      recommended_minimum: number;
    };
    /** ProviderMetadata */
    ProviderMetadata: {
      /** Active Job Id */
      active_job_id: string;
      /** Credential Indicator */
      credential_indicator: string;
      /** Enabled */
      enabled: boolean;
      /** Error */
      error: string;
      /** Error Code */
      error_code?: string | null;
      /** Id */
      id: string;
      /** Key Hint */
      key_hint: string;
      /** Label */
      label: string;
      /** Last Attempt At */
      last_attempt_at: string;
      /** Last Verified At */
      last_verified_at: string;
      /** Model Samples */
      model_samples: string[];
      /** Models Are Examples */
      models_are_examples: boolean;
      /** Name */
      name: string;
      /** Recommended Action */
      recommended_action?: string | null;
      /** Routed Via */
      routed_via?: string | null;
      /** State */
      state: 'saved' | 'verifying' | 'verified' | 'degraded' | 'disabled' | 'removing' | 'unsupported_legacy';
      /** Supported */
      supported: boolean;
      /** Updated At */
      updated_at: string;
    };
    /** ProviderMetadataResponse */
    ProviderMetadataResponse: {
      /** Ok */
      ok: boolean;
      /** Providers */
      providers: components['schemas']['ProviderMetadata'][];
      setup?: components['schemas']['ProviderSetupProgress'] | null;
    };
    /** ProviderModelsResponse */
    ProviderModelsResponse: {
      /** Examples */
      examples: boolean;
      /** Models */
      models: string[];
      /** Ok */
      ok: boolean;
      /** Provider Id */
      provider_id: string;
    };
    /** ProviderRequest */
    ProviderRequest: {
      /** Api Key */
      api_key: string;
      /**
       * Label
       * @default
       */
      label?: string;
      /**
       * Provider Id
       * @default
       */
      provider_id?: string;
    };
    /** ProviderSetupProgress */
    ProviderSetupProgress: {
      /** Complete */
      complete: boolean;
      /** Recommendation Met */
      recommendation_met: boolean;
      /** Recommended */
      recommended: string[];
      /** Recommended Minimum */
      recommended_minimum: number;
      /** Recommended Verified */
      recommended_verified: string[];
      /** Verified */
      verified: number;
    };
    /** ProvisioningAction */
    ProvisioningAction: {
      /** Endpoint */
      endpoint?: string | null;
      /** Href */
      href?: string | null;
      /** Kind */
      kind: 'none' | 'link' | 'job' | 'bootstrap';
      /** Label */
      label: string;
    };
    /** ProvisioningPhase */
    ProvisioningPhase: {
      /** Actual State */
      actual_state: string;
      /** Attempts */
      attempts: number;
      /** Detail */
      detail: string;
      /** Error */
      error: string;
      /** Label */
      label: string;
      /** Phase Id */
      phase_id: string;
      /** Updated At */
      updated_at: string;
    };
    /** ProvisioningResponse */
    ProvisioningResponse: {
      /** Available */
      available: boolean;
      blocked?: components['schemas']['ProvisioningPhase'] | null;
      /** Complete */
      complete: boolean;
      next_action?: components['schemas']['ProvisioningAction'] | null;
      /** Ok */
      ok: boolean;
      /** Phases */
      phases: components['schemas']['ProvisioningPhase'][];
      progress?: components['schemas']['ProvisioningResponseProgress'] | null;
      waiting?: components['schemas']['ProvisioningPhase'] | null;
    };
    /** ProvisioningResponseProgress */
    ProvisioningResponseProgress: {
      /** Completed */
      completed: number;
      /** Total */
      total: number;
    };
    /** QueuedJob */
    QueuedJob: {
      /** Created At */
      created_at?: string | null;
      /** Id */
      id: string;
      /** State */
      state: string;
    };
    /**
     * RecoverySheet
     * @description What a new computer needs to open the backups. Sent once, never cached.
     */
    RecoverySheet: {
      /** Copies */
      copies: string[];
      /** Generated At */
      generated_at: string;
      /** Kopia Image */
      kopia_image: string;
      /** Ok */
      ok: boolean;
      /** Password */
      password: string;
      /** Password Check */
      password_check: string;
      /** Repository */
      repository: string;
      /** Server */
      server: string;
      /** Version */
      version: string;
    };
    /**
     * RecoverySheetStatus
     * @description When an administrator last opened the recovery sheet; never its contents.
     */
    RecoverySheetStatus: {
      /** Opened At */
      opened_at: string;
      /** Opened By */
      opened_by: string;
    };
    /** Service */
    Service: {
      account?: components['schemas']['ServiceAccount'] | null;
      /** Allowed Actions */
      allowed_actions?:
        ('install' | 'retry_setup' | 'start' | 'stop' | 'restart' | 'uninstall' | 'uninstall_delete_data')[] | null;
      /** Auth */
      auth: 'oidc' | 'proxy' | 'trusted_header' | 'local' | 'excluded';
      /**
       * Backing Up
       * @default false
       */
      backing_up?: boolean;
      /** Blocking Check */
      blocking_check?: ('process' | 'route' | 'sign_in') | null;
      /** Category */
      category: string;
      checks?: components['schemas']['ServiceChecks'] | null;
      /** Compose Present */
      compose_present: boolean;
      compute?: components['schemas']['ServiceCompute'] | null;
      /** Configuration */
      configuration?: components['schemas']['ServiceConfigField'][] | null;
      /** Containers */
      containers?: components['schemas']['ServiceContainersItem'][] | null;
      /** Dependencies */
      dependencies: string[];
      /** Detail */
      detail: string;
      /**
       * Display State
       * @enum {string}
       */
      display_state:
        'running' | 'stopped' | 'working' | 'checking' | 'not_installed' | 'needs_setup' | 'needs_attention';
      /** Extra Routes */
      extra_routes?: components['schemas']['ServiceExtraRoute'][];
      /** Group */
      group: 'apps' | 'ai' | 'infrastructure';
      /** Health State */
      health_state: string;
      /** Https Port */
      https_port: number;
      /** Id */
      id: string;
      identity?: components['schemas']['ServiceIdentity'] | null;
      /** Identity Note */
      identity_note: string;
      initialization?: components['schemas']['ServiceInitialization'] | null;
      /** Installed */
      installed: boolean;
      /** Last Error */
      last_error:
        | string
        | {
            [key: string]: components['schemas']['JsonValue'];
          };
      last_job?: components['schemas']['Job'] | null;
      /** Last Job Id */
      last_job_id: string;
      /** Lifecycle */
      lifecycle: 'always_on' | 'shared' | 'optional';
      /** Links */
      links?: components['schemas']['ServiceLink'][];
      mobile?: components['schemas']['ServiceMobile0'] | null;
      /** Name */
      name: string;
      /** Observed At */
      observed_at: string;
      /** Parts */
      parts?: {
        [key: string]: string;
      };
      /** Private Https Port */
      private_https_port?: number | null;
      /** Reason */
      reason: string;
      /** Recommended Action */
      recommended_action?: string | null;
      recovery?: components['schemas']['ServiceRecovery'] | null;
      /** Required */
      required: boolean;
      /** Resource Guidance */
      resource_guidance: string;
      /** Routable */
      routable: boolean;
      /** Route Ready */
      route_ready: boolean;
      /** Setup Action */
      setup_action: string;
      /** Stage */
      stage: 'foundation' | 'core' | 'optional';
      /**
       * Stop Warning
       * @default
       */
      stop_warning?: string;
      ui?: components['schemas']['ServiceUi'] | null;
      update?: components['schemas']['ServiceUpdate'] | null;
      /** Url */
      url: string;
    };
    /** ServiceAccount */
    ServiceAccount: {
      /** Mode */
      mode: string;
      /** User Action */
      user_action: string;
    };
    /** ServiceActionRequest */
    ServiceActionRequest: {
      /**
       * Action
       * @enum {string}
       */
      action:
        | 'install'
        | 'retry_setup'
        | 'start'
        | 'stop'
        | 'restart'
        | 'uninstall'
        | 'uninstall_delete_data'
        | 'restore'
        | 'update'
        | 'recover'
        | 'keep_update'
        | 'switch_compute';
      /**
       * Confirm
       * @default
       */
      confirm?: string;
      /**
       * Snapshot Id
       * @default
       */
      snapshot_id?: string;
    };
    /** ServiceCheck */
    ServiceCheck: {
      /** Checked At */
      checked_at: string;
      /** Detail */
      detail: string;
      /** Failures */
      failures: number;
      /** Last Failure At */
      last_failure_at: string;
      /** Last Success At */
      last_success_at: string;
      /**
       * State
       * @enum {string}
       */
      state: 'pass' | 'checking' | 'fail' | 'pending' | 'stale' | 'not_required';
    };
    /** ServiceChecks */
    ServiceChecks: {
      process: components['schemas']['ServiceCheck'];
      route: components['schemas']['ServiceCheck'];
      sign_in: components['schemas']['ServiceCheck'];
    };
    /**
     * ServiceCompute
     * @description For apps with a GPU build: the build that runs and the one the compute mode asks for.
     */
    ServiceCompute: {
      /** Running */
      running: string;
      /** Wanted */
      wanted: string;
    };
    /** ServiceConfigField */
    ServiceConfigField: {
      /** Default */
      default?: string | boolean | number | null;
      /** Key */
      key: string;
      /** Label */
      label?: string | null;
      /** Options */
      options?: string[] | null;
      /** Required */
      required?: boolean | null;
      /** Secret Present */
      secret_present?: boolean | null;
      /** Type */
      type: 'string' | 'boolean' | 'integer' | 'enum' | 'secret';
      /** Value */
      value?: string | boolean | number | null;
    };
    /** ServiceConfigResponse */
    ServiceConfigResponse: {
      /** Fields */
      fields: components['schemas']['ServiceConfigField'][];
      /** Ok */
      ok: boolean;
      /** Restart Required */
      restart_required?: boolean | null;
      /** Revision */
      revision?: number | null;
      /** Service Id */
      service_id: string;
    };
    /** ServiceContainersItem */
    ServiceContainersItem: {
      /** Health */
      health: string;
      /** Image */
      image: string;
      /** Name */
      name: string;
      /** Service */
      service: string;
      /** State */
      state: string;
      /** Status */
      status: string;
    };
    /** ServiceExtraRoute */
    ServiceExtraRoute: {
      /** Label */
      label: string;
      /** Name */
      name: string;
      /** Private Https Port */
      private_https_port: number;
    };
    /** ServiceIdentity */
    ServiceIdentity: {
      /** Detail */
      detail: string;
      /** Error */
      error?: {
        [key: string]: components['schemas']['JsonValue'];
      } | null;
      /** Job Id */
      job_id: string;
      /** Last Verified At */
      last_verified_at: string;
      /** Launch Url */
      launch_url: string;
      /** Mode */
      mode: 'native_oidc' | 'trusted_header' | 'proxy_gate' | 'local' | 'none';
      /** State */
      state: 'unconfigured' | 'configuring' | 'migration_required' | 'ready' | 'degraded' | 'off' | 'unsupported';
    };
    /** ServiceInitialization */
    ServiceInitialization: {
      /** Job Id */
      job_id?: string | null;
      /** Last Error */
      last_error?: {
        [key: string]: components['schemas']['JsonValue'];
      } | null;
      /** Mode */
      mode: string;
      /** State */
      state: string;
      /** Verified At */
      verified_at?: string | null;
    };
    /** ServiceInitializationResponse */
    ServiceInitializationResponse: {
      initialization: components['schemas']['ServiceInitialization'];
      /** Ok */
      ok: boolean;
    };
    /**
     * ServiceLink
     * @description Another private address of an app (``extra_routes``), shown beside its Open button.
     */
    ServiceLink: {
      /** Label */
      label: string;
      /** Url */
      url: string;
    };
    /** ServiceLogsResponse */
    ServiceLogsResponse: {
      /** Container */
      container: string;
      /** Lines */
      lines: string[];
      /** Ok */
      ok: boolean;
      /** Service Id */
      service_id: string;
    };
    /** ServiceMobile0 */
    ServiceMobile0: {
      /** Clients */
      clients: components['schemas']['MobileClient'][];
      /** Primary */
      primary: string;
    };
    /**
     * ServiceRecovery
     * @description A restore or update that was interrupted; nothing continues until a person chooses.
     */
    ServiceRecovery: {
      /**
       * Can Keep
       * @default false
       */
      can_keep?: boolean;
      /** Detail */
      detail: string;
      /**
       * Kind
       * @enum {string}
       */
      kind: 'restore' | 'update';
      /** Since */
      since: string;
    };
    /** ServiceUi */
    ServiceUi: {
      /** Authentication */
      authentication: string;
      /** Label */
      label: string;
      /** Launch Label */
      launch_label?: string | null;
      /** Reason */
      reason: string | null;
      /** State */
      state: 'ready' | 'route_pending' | 'unavailable';
      /** Url */
      url: string | null;
    };
    /** ServiceUpdate */
    ServiceUpdate: {
      /** Approved Version */
      approved_version: string;
      /** Installed Version */
      installed_version: string;
      /** Repository */
      repository: string;
      /** Supporting Only */
      supporting_only: boolean;
      /** Update Available */
      update_available: boolean;
    };
    /** ServicesResponse */
    ServicesResponse: {
      /**
       * Observed At
       * @default
       */
      observed_at?: string;
      /** Ok */
      ok: boolean;
      /** Services */
      services: components['schemas']['Service'][];
      /** Tailnet Dns Name */
      tailnet_dns_name: string;
    };
    /** SessionResponse */
    SessionResponse: {
      /** Csrf Token */
      csrf_token: string;
      /** Ok */
      ok: boolean;
    };
    /** SnapshotResponse */
    SnapshotResponse: {
      audit: components['schemas']['AuditResponse'];
      /**
       * Backup Attention
       * @default []
       */
      backup_attention?: components['schemas']['BackupAttention'][];
      catalog: components['schemas']['CatalogResponse'];
      chat: components['schemas']['ChatStatus'];
      core: components['schemas']['CoreSetupResponse'];
      /**
       * Dismissed
       * @default []
       */
      dismissed?: string[];
      identity: components['schemas']['IdentityResponse'];
      jobs: components['schemas']['JobsResponse'];
      /**
       * Model Runner
       * @default
       */
      model_runner?: string;
      provisioning: components['schemas']['ProvisioningResponse'];
      services: components['schemas']['ServicesResponse'];
      system: components['schemas']['SystemResponse'];
    };
    /** SystemConfig */
    SystemConfig: {
      /** Available Modes */
      available_modes: string[];
      /** Compute Mode */
      compute_mode: 'auto' | 'cpu' | 'nvidia' | 'amd';
      /** Ok */
      ok: boolean;
      /** Resolved Compute Mode */
      resolved_compute_mode: 'cpu' | 'nvidia' | 'amd';
      /** Updated At */
      updated_at: string;
      /** Updated By */
      updated_by: string;
    };
    /** SystemResponse */
    SystemResponse: {
      backup: components['schemas']['BackupReadiness'];
      /** Container Memory */
      container_memory?: {
        [key: string]: number;
      } | null;
      /** Cpu Percent */
      cpu_percent: number;
      disk: components['schemas']['Metric'];
      disk_usage?: components['schemas']['DiskUsage'] | null;
      /** Docker Ready */
      docker_ready: boolean;
      gpu?: components['schemas']['GpuMemory'] | null;
      memory: components['schemas']['Metric'];
      /** Memory By Core Part */
      memory_by_core_part?: {
        [key: string]: number;
      } | null;
      /** Memory By Owner */
      memory_by_owner?: {
        [key: string]: number;
      } | null;
      /**
       * Observed At
       * @default
       */
      observed_at?: string;
      /** Ok */
      ok: boolean;
      /** Runtime Root */
      runtime_root: string;
      /** Tailnet Dns Name */
      tailnet_dns_name: string;
      tailscale?: components['schemas']['TailscaleStatus'] | null;
      /** Uptime Seconds */
      uptime_seconds?: number | null;
      /** Worker State */
      worker_state?: string | null;
    };
    /** TailscaleServeStatus */
    TailscaleServeStatus: {
      /** Ports */
      ports: number[];
      /** State */
      state: 'available' | 'unavailable';
    };
    /** TailscaleStatus */
    TailscaleStatus: {
      /** Backend State */
      backend_state: string;
      /** Detail */
      detail: string;
      /** Dns Name */
      dns_name: string;
      /** Online */
      online: boolean;
      serve: components['schemas']['TailscaleServeStatus'];
      /** State */
      state: 'connected' | 'disconnected' | 'unavailable';
    };
    /** UpdateResponse */
    UpdateResponse: {
      /** Added Services */
      added_services?: string[];
      /** Approved Version */
      approved_version: string;
      /** Blocked Reason */
      blocked_reason: string;
      /** Installed Version */
      installed_version: string;
      /** Ok */
      ok: boolean;
      /** Release Url */
      release_url: string;
      /** Removed Services */
      removed_services?: string[];
      /** Repository */
      repository: string;
      /** Supporting Only */
      supporting_only: boolean;
      /** Update Available */
      update_available: boolean;
      /** Update Enabled */
      update_enabled: boolean;
    };
    /** VaultAutomatic */
    VaultAutomatic: {
      /** Error */
      error: string | null;
      /** Last Run */
      last_run: string | null;
      /** Ok */
      ok: boolean | null;
      /** People */
      people: components['schemas']['VaultAutomaticPeople0Item'][] | null;
    };
    /** VaultAutomaticPeople0Item */
    VaultAutomaticPeople0Item: {
      /** Name */
      name: string;
      /** Saved */
      saved: number;
      /** State */
      state: 'up_to_date' | 'partly_saved' | 'waiting_for_account' | 'skipped' | '';
      /** Uid */
      uid: string;
      /** Waiting */
      waiting: number;
    };
    /** VaultSetupRequest */
    VaultSetupRequest: {
      /**
       * Email
       * @default
       */
      email?: string;
      /** Master Password */
      master_password: string;
      /**
       * Totp
       * @default
       */
      totp?: string;
    };
    /** VaultSetupResult */
    VaultSetupResult: {
      /** Created */
      created: string[];
      /** Ok */
      ok: boolean;
      /** Skipped */
      skipped: string[];
      /** Unchanged */
      unchanged: string[];
      /** Updated */
      updated: string[];
    };
    /** VaultStatus */
    VaultStatus: {
      automatic?: components['schemas']['VaultAutomatic'] | null;
      browser_extension?: components['schemas']['VaultStatusBrowser_extension'] | null;
      /**
       * Ok
       * @default true
       */
      ok?: boolean;
      /** Pending Logins */
      pending_logins?: number | null;
      /** Seeded */
      seeded: boolean;
      /** Seeded At */
      seeded_at?: string | null;
    };
    /** VaultStatusBrowser_extension */
    VaultStatusBrowser_extension: {
      /** Browsers */
      browsers: string[];
      /** Server Url */
      server_url: string;
      /** Signed In */
      signed_in?: boolean | null;
    };
    /** VaultSyncResponse */
    VaultSyncResponse: {
      automatic: components['schemas']['VaultAutomatic'];
      /** Ok */
      ok: boolean;
    };
    /** VoiceKeyCreated */
    VoiceKeyCreated: {
      /** Base Url */
      base_url: string;
      /** Key */
      key: string;
      /** Models */
      models: string[];
      /** Ok */
      ok: boolean;
    };
    /** VoiceKeyStatus */
    VoiceKeyStatus: {
      /** Available */
      available: boolean;
      /**
       * Base Url
       * @default
       */
      base_url?: string;
      /**
       * Created At
       * @default
       */
      created_at?: string;
      /** Exists */
      exists: boolean;
      /** Models */
      models: string[];
      /** Ok */
      ok: boolean;
    };
  };
  responses: never;
  parameters: never;
  requestBodies: never;
  headers: never;
  pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
  health_api_health_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['Health'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  ai_models_api_v1_ai_models_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['AiModels'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  change_ai_models_api_v1_ai_models_put: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        'application/json': components['schemas']['AiModelsRequest'];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['JobResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  change_voice_api_v1_ai_voice_put: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        'application/json': components['schemas']['AiVoiceRequest'];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['JobResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  app_sizes_api_v1_app_sizes_get: {
    parameters: {
      query?: {
        ids?: string;
      };
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['AppSizesResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  audit_api_v1_audit_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['AuditResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  backups_api_v1_backups_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['BackupReadiness'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  start_authorization_api_v1_calendar_authorization_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['CalendarAuthorization'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  cancel_authorization_api_v1_calendar_authorization__authorization_id__delete: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        authorization_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['OkResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  poll_authorization_api_v1_calendar_authorization__authorization_id__poll_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        authorization_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['CalendarAuthorization'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  auto_connect_api_v1_calendar_auto_connect_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['CalendarConnection'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  get_connection_api_v1_calendar_connection_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['CalendarConnection'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  select_connection_api_v1_calendar_connection_put: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        'application/json': components['schemas']['CalendarSelectRequest'];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['CalendarConnection'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  save_connection_api_v1_calendar_connection_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        'application/json': components['schemas']['CalendarConnectRequest'];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['CalendarConnection'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  delete_connection_api_v1_calendar_connection_delete: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['OkResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  list_events_api_v1_calendar_events_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['CalendarEvents'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  create_event_api_v1_calendar_events_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        'application/json': components['schemas']['CalendarEventRequest'];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['OkResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  update_event_api_v1_calendar_events__event_id__put: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        event_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        'application/json': components['schemas']['CalendarEventRequest'];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['OkResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  delete_event_api_v1_calendar_events__event_id__delete: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        event_id: string;
      };
      cookie?: never;
    };
    requestBody?: {
      content: {
        'application/json': components['schemas']['CalendarDeleteRequest'] | null;
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['OkResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  resolve_conflict_api_v1_calendar_events__event_id__resolve_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        event_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        'application/json': components['schemas']['CalendarResolveRequest'];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['OkResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  sync_api_v1_calendar_sync_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['CalendarSync'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  catalog_api_v1_catalog_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['CatalogResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  chat_apps_api_v1_chat_apps_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ChatApps'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  choose_chat_app_api_v1_chat_apps_put: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        'application/json': components['schemas']['ChatAppRequest'];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ChatApps'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  connect_chat_api_v1_chat_connect_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ChatConnectResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  poll_chat_api_v1_chat_connect_poll_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ChatPollResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  chat_status_api_v1_chat_status_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ChatStatus'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  dismiss_api_v1_dismissed_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        'application/json': components['schemas']['DismissRequest'];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['DismissedResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  identity_api_v1_identity_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['IdentityResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  list_jobs_api_v1_jobs_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['JobsResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  start_core_api_v1_jobs_core_install_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['JobResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  verify_core_api_v1_jobs_core_verify_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['JobResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  job_detail_api_v1_jobs__job_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        job_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['JobDetailResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  cancel_job_api_v1_jobs__job_id__cancel_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        job_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['JobResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  job_events_api_v1_jobs__job_id__events_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        job_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['JobEventsResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  retry_job_api_v1_jobs__job_id__retry_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        job_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['JobResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  list_servers_api_v1_mcp_servers_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['McpRegistryResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  put_configuration_api_v1_mcp_servers__server_id__configuration_put: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        server_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        'application/json': components['schemas']['ConfigurationRequest'];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['McpServerResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  server_jobs_api_v1_mcp_servers__server_id__jobs_get: {
    parameters: {
      query?: {
        limit?: number;
      };
      header?: never;
      path: {
        server_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['McpJobsResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  server_logs_api_v1_mcp_servers__server_id__logs_get: {
    parameters: {
      query?: {
        tail?: number;
      };
      header?: never;
      path: {
        server_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['McpLogsResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  list_tools_api_v1_mcp_servers__server_id__tools_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        server_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['McpToolsResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  server_updates_api_v1_mcp_servers__server_id__updates_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        server_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['McpUpdatesResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  queue_action_api_v1_mcp_servers__server_id___action__post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        server_id: string;
        action: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['JobResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  get_checklist_api_v1_me_checklist_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ChecklistResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  put_checklist_api_v1_me_checklist_put: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        'application/json': components['schemas']['ChecklistRequest'];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ChecklistResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  list_people_api_v1_people_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['PeopleResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  add_person_api_v1_people_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        'application/json': components['schemas']['PersonRequest'];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['PersonResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  change_person_api_v1_people__username___action__post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        username: string;
        action: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        'application/json': components['schemas']['PersonChangeRequest'];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['PersonResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  list_providers_api_v1_providers_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ProviderMetadataResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  save_provider_api_v1_providers_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        'application/json': components['schemas']['ProviderRequest'];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['JobResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  providers_catalog_api_v1_providers_catalog_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ProviderCatalogResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  remove_provider_api_v1_providers__provider_id__delete: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        provider_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['JobResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  disable_provider_api_v1_providers__provider_id__disable_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        provider_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['JobResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  enable_provider_api_v1_providers__provider_id__enable_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        provider_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['JobResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  provider_models_api_v1_providers__provider_id__models_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        provider_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ProviderModelsResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  verify_provider_api_v1_providers__provider_id__verify_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        provider_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['JobResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  provisioning_api_v1_provisioning_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ProvisioningResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  latest_install_batch_api_v1_service_install_batches_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['InstallBatchResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  get_install_batch_api_v1_service_install_batches__batch_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        batch_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['InstallBatchResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  cancel_install_batch_api_v1_service_install_batches__batch_id__cancel_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        batch_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['InstallBatchResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  pause_download_api_v1_service_install_batches__batch_id__downloads__service_id__pause_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        batch_id: string;
        service_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['InstallBatchResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  resume_download_api_v1_service_install_batches__batch_id__downloads__service_id__resume_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        batch_id: string;
        service_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['InstallBatchResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  reorder_batch_api_v1_service_install_batches__batch_id__order_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        batch_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        'application/json': components['schemas']['BatchOrderRequest'];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['InstallBatchResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  set_parallel_downloads_api_v1_service_install_batches__batch_id__parallel_downloads_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        batch_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        'application/json': components['schemas']['ParallelDownloadsRequest'];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['InstallBatchResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  reset_install_batch_api_v1_service_install_batches__batch_id__reset_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        batch_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['InstallBatchResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  resume_install_batch_api_v1_service_install_batches__batch_id__resume_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        batch_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['InstallBatchResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  list_services_api_v1_services_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ServicesResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  create_install_batch_api_v1_services_install_batch_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        'application/json': components['schemas']['InstallBatchRequest'];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['InstallBatchResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  service_action_api_v1_services__service_id__actions_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        service_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        'application/json': components['schemas']['ServiceActionRequest'];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['JobResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  service_backups_api_v1_services__service_id__backups_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        service_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['BackupsResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  back_up_service_api_v1_services__service_id__backups_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        service_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['BackupStarted'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  get_service_configuration_api_v1_services__service_id__configuration_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        service_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ServiceConfigResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  put_service_configuration_api_v1_services__service_id__configuration_put: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        service_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        'application/json': components['schemas']['ConfigurationRequest'];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ServiceConfigResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  confirm_service_initialization_api_v1_services__service_id__initialization_confirm_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        service_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ServiceInitializationResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  service_logs_api_v1_services__service_id__logs_get: {
    parameters: {
      query?: {
        tail?: number;
        container?: string;
      };
      header?: never;
      path: {
        service_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ServiceLogsResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  service_updates_api_v1_services__service_id__updates_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        service_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['UpdateResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  session_api_v1_session_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['SessionResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  core_setup_api_v1_setup_core_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['CoreSetupResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  snapshot_api_v1_snapshot_get: {
    parameters: {
      query?: {
        status?: boolean;
      };
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['SnapshotResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  system_api_v1_system_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['SystemResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  platform_backup_status_api_v1_system_backups_platform_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['PlatformBackupStatus'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  start_platform_backup_api_v1_system_backups_platform_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['BackupStarted'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  open_recovery_sheet_api_v1_system_backups_recovery_sheet_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['RecoverySheet'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  backup_schedule_status_api_v1_system_backups_schedule_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['BackupSchedule'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  update_backup_schedule_api_v1_system_backups_schedule_put: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        'application/json': components['schemas']['BackupScheduleRequest'];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['BackupSchedule'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  mu3lab_changelog_api_v1_system_changelog_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['Mu3LabChangelog'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  free_up_space_api_v1_system_cleanup_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        'application/json': components['schemas']['CleanupRequest'];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['JobResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  system_config_api_v1_system_config_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['SystemConfig'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  update_system_config_api_v1_system_config_put: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        'application/json': components['schemas']['ComputeRequest'];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['SystemConfig'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  mu3lab_update_api_v1_system_update_get: {
    parameters: {
      query?: {
        refresh?: boolean;
      };
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['Mu3LabUpdate'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  start_mu3lab_update_api_v1_system_update_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['JobResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  setup_vault_api_v1_vault_setup_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        'application/json': components['schemas']['VaultSetupRequest'];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['VaultSetupResult'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  vault_status_api_v1_vault_status_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['VaultStatus'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  sync_now_api_v1_vault_sync_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['VaultSyncResponse'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  voice_key_status_api_v1_voice_key_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['VoiceKeyStatus'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  create_voice_key_api_v1_voice_key_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['VoiceKeyCreated'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
  revoke_voice_key_api_v1_voice_key_delete: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['VoiceKeyStatus'];
        };
      };
      /** @description Bad Request */
      400: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unauthorized */
      401: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Forbidden */
      403: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Conflict */
      409: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Internal Server Error */
      500: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
      /** @description Service Unavailable */
      503: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          'application/json': components['schemas']['ApiErrorResponse'];
        };
      };
    };
  };
}
