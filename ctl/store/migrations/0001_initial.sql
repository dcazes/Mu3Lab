-- Initial clean-install schema. Future changes use numbered migrations.
CREATE TABLE app_sizes (
                        service_id TEXT PRIMARY KEY, layers_json TEXT NOT NULL DEFAULT '[]',
                        error TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
                    );

CREATE TABLE audit (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, actor TEXT NOT NULL,
                        event TEXT NOT NULL, created_at TEXT NOT NULL, detail TEXT NOT NULL,
                        FOREIGN KEY(job_id) REFERENCES jobs(id)
                    );

CREATE TABLE calendar_connections (
                        owner_uid TEXT PRIMARY KEY,
                        username_hint TEXT NOT NULL,
                        selected_calendar_id TEXT NOT NULL DEFAULT '',
                        calendars_json TEXT NOT NULL DEFAULT '[]',
                        state TEXT NOT NULL DEFAULT 'connected',
                        last_error TEXT NOT NULL DEFAULT '',
                        last_success_at TEXT NOT NULL DEFAULT '',
                        updated_at TEXT NOT NULL
                    );

CREATE TABLE call_keys (
            idempotency_key TEXT PRIMARY KEY, created_at TEXT NOT NULL
          );

CREATE TABLE calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT, server_id TEXT NOT NULL,
            tool_name TEXT NOT NULL, source TEXT NOT NULL, actor TEXT NOT NULL,
            outcome TEXT NOT NULL, duration_ms INTEGER NOT NULL,
            created_at TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
            source_ref TEXT
          );

CREATE TABLE category_switches (
            server_id TEXT NOT NULL, category_id TEXT NOT NULL,
            enabled INTEGER NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(server_id,category_id)
          );

CREATE TABLE image_downloads (
                        service_id TEXT PRIMARY KEY, job_id TEXT NOT NULL DEFAULT '',
                        state TEXT NOT NULL, total_bytes INTEGER NOT NULL DEFAULT 0,
                        done_bytes INTEGER NOT NULL DEFAULT 0, rate_bps INTEGER NOT NULL DEFAULT 0,
                        images_total INTEGER NOT NULL DEFAULT 0, images_done INTEGER NOT NULL DEFAULT 0,
                        connections INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
                    );

CREATE TABLE install_batch_items (
                        batch_id TEXT NOT NULL, service_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                        explicitly_selected INTEGER NOT NULL, state TEXT NOT NULL,
                        depends_on_json TEXT NOT NULL DEFAULT '[]',
                        job_id TEXT NOT NULL DEFAULT '', error_json TEXT NOT NULL DEFAULT '{}',
                        started_at TEXT NOT NULL DEFAULT '', completed_at TEXT NOT NULL DEFAULT '', priority INTEGER NOT NULL DEFAULT 0, download_state TEXT NOT NULL DEFAULT '', download_error TEXT NOT NULL DEFAULT '',
                        PRIMARY KEY(batch_id, ordinal),
                        FOREIGN KEY(batch_id) REFERENCES install_batches(id)
                    );

CREATE TABLE install_batches (
                        id TEXT PRIMARY KEY, actor TEXT NOT NULL, owner_uid TEXT NOT NULL,
                        state TEXT NOT NULL, current_ordinal INTEGER NOT NULL DEFAULT 0,
                        idempotency_key TEXT, error_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    , parallel_downloads INTEGER NOT NULL DEFAULT 3);

CREATE TABLE job_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                        event TEXT NOT NULL, created_at TEXT NOT NULL, detail TEXT NOT NULL,
                        FOREIGN KEY(job_id) REFERENCES jobs(id)
                    );

CREATE TABLE jobs (
                        id TEXT PRIMARY KEY, kind TEXT NOT NULL, service_id TEXT NOT NULL,
                        action TEXT NOT NULL, state TEXT NOT NULL, actor TEXT NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, detail TEXT NOT NULL
                    , idempotency_key TEXT, step_id TEXT NOT NULL DEFAULT '', lease_owner TEXT NOT NULL DEFAULT '', lease_expires_at TEXT NOT NULL DEFAULT '', heartbeat_at TEXT NOT NULL DEFAULT '', error_code TEXT NOT NULL DEFAULT '', cancel_requested_at TEXT NOT NULL DEFAULT '', params_json TEXT NOT NULL DEFAULT '{}');

CREATE TABLE mcp_servers (
                        server_id TEXT PRIMARY KEY,
                        service_id TEXT NOT NULL,
                        enabled INTEGER NOT NULL DEFAULT 0,
                        state TEXT NOT NULL DEFAULT 'disabled',
                        tool_snapshot_json TEXT NOT NULL DEFAULT '[]',
                        last_verified_at TEXT NOT NULL DEFAULT '',
                        last_error_json TEXT NOT NULL DEFAULT '{}',
                        updated_at TEXT NOT NULL
                    );

CREATE TABLE provider_connections (
                        provider_id TEXT PRIMARY KEY,
                        label TEXT NOT NULL,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        state TEXT NOT NULL DEFAULT 'saved',
                        model_samples_json TEXT NOT NULL DEFAULT '[]',
                        last_attempt_at TEXT NOT NULL DEFAULT '',
                        last_verified_at TEXT NOT NULL DEFAULT '',
                        last_error_json TEXT NOT NULL DEFAULT '{}',
                        active_job_id TEXT NOT NULL DEFAULT '',
                        config_revision INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL
                    );

CREATE TABLE provisioning_steps (
                        workflow_version INTEGER NOT NULL,
                        phase_id TEXT NOT NULL,
                        desired_state TEXT NOT NULL,
                        actual_state TEXT NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        detail TEXT NOT NULL DEFAULT '',
                        error TEXT NOT NULL DEFAULT '',
                        inputs_json TEXT NOT NULL DEFAULT '{}',
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (workflow_version, phase_id)
                    );

CREATE TABLE service_identity_state (
                        service_id TEXT PRIMARY KEY,
                        mode TEXT NOT NULL,
                        state TEXT NOT NULL,
                        last_job_id TEXT NOT NULL DEFAULT '',
                        detail TEXT NOT NULL DEFAULT '',
                        last_error_json TEXT NOT NULL DEFAULT '{}',
                        last_verified_at TEXT NOT NULL DEFAULT '',
                        updated_at TEXT NOT NULL
                    );

CREATE TABLE service_initializations (
                        service_id TEXT PRIMARY KEY,
                        mode TEXT NOT NULL,
                        state TEXT NOT NULL,
                        job_id TEXT NOT NULL DEFAULT '',
                        last_error_json TEXT NOT NULL DEFAULT '{}',
                        verified_at TEXT NOT NULL DEFAULT '',
                        updated_at TEXT NOT NULL
                    );

CREATE TABLE service_installations (
                        service_id TEXT PRIMARY KEY,
                        state TEXT NOT NULL,
                        manifest_version TEXT NOT NULL DEFAULT '',
                        image_digests_json TEXT NOT NULL DEFAULT '{}',
                        config_revision INTEGER NOT NULL DEFAULT 0,
                        route_state TEXT NOT NULL DEFAULT 'unknown',
                        last_job_id TEXT NOT NULL DEFAULT '',
                        last_error_json TEXT NOT NULL DEFAULT '{}',
                        installed_at TEXT NOT NULL DEFAULT '',
                        updated_at TEXT NOT NULL
                    );

CREATE TABLE system_config (
                        key TEXT PRIMARY KEY,
                        value_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        updated_by TEXT NOT NULL
                    );

CREATE TABLE tool_permissions (
            server_id TEXT NOT NULL, tool_name TEXT NOT NULL,
            permission TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(server_id,tool_name)
          );

CREATE TABLE write_confirmations (
            nonce TEXT PRIMARY KEY, server_id TEXT NOT NULL, tool_name TEXT NOT NULL,
            actor TEXT NOT NULL, input_hash TEXT NOT NULL, expires_at TEXT NOT NULL
          );

CREATE INDEX calls_server_id_id ON calls(server_id,id DESC);

CREATE UNIQUE INDEX calls_source_ref ON calls(source_ref) WHERE source_ref IS NOT NULL;

CREATE INDEX install_batch_items_job ON install_batch_items(job_id);

CREATE UNIQUE INDEX install_batches_idempotency
                        ON install_batches(idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE UNIQUE INDEX jobs_idempotency_key ON jobs(idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE secrets (
 scope TEXT NOT NULL, name TEXT NOT NULL, ciphertext BLOB NOT NULL,
 updated_at REAL NOT NULL, expires_at REAL,
 PRIMARY KEY(scope, name)
);
CREATE TABLE records (
 scope TEXT NOT NULL, name TEXT NOT NULL, value_json TEXT NOT NULL,
 updated_at REAL NOT NULL, PRIMARY KEY(scope, name)
);
CREATE TABLE app_owner (
 service_id TEXT PRIMARY KEY, owner_uid TEXT NOT NULL,
 username TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '',
 display_name TEXT NOT NULL DEFAULT ''
);
