"""Exercise provider policy in Postgres without changing application data.

Run with MU3LAB_TEST_POSTGRES_CONTAINER pointing at a PostgreSQL container.
All objects are temporary and all statements are rolled back.
"""

from __future__ import annotations

import os
import subprocess
import unittest

from ctl.lobehub_ops import _provider_guard_sql


@unittest.skipUnless(os.environ.get("MU3LAB_TEST_POSTGRES_CONTAINER"), "requires a PostgreSQL test container")
class ProviderGuardTests(unittest.TestCase):
    def test_builtin_initialization_and_private_override_protection(self):
        guard = _provider_guard_sql().replace(
            "FUNCTION mu3lab_provider_guard()", "FUNCTION pg_temp.mu3lab_provider_guard()"
        ).replace("ON ai_providers", "ON mu3lab_provider_probe")
        sql = (
            """
BEGIN;
CREATE TEMP TABLE mu3lab_provider_probe (
  id text PRIMARY KEY, enabled boolean, key_vaults text, config jsonb, settings jsonb
);
"""
            + guard
            + """
-- The exact empty-config shape used by LobeHub's getAiProviderById.
INSERT INTO mu3lab_provider_probe (id, settings, config) VALUES ('openai', '{}', '{}');
-- BEFORE INSERT also runs on the repeated ON CONFLICT path.
INSERT INTO mu3lab_provider_probe (id, settings, config) VALUES ('openai', '{}', '{}')
ON CONFLICT DO NOTHING;
UPDATE mu3lab_provider_probe SET enabled = true WHERE id = 'openai';
DELETE FROM mu3lab_provider_probe;
-- Null remains a valid server-default configuration.
INSERT INTO mu3lab_provider_probe (id) VALUES ('openai');
DO $$
DECLARE statement text; rejected boolean;
BEGIN
  FOR statement IN SELECT unnest(ARRAY[
    'INSERT INTO mu3lab_provider_probe (id, config) VALUES (''openai'', ''{"baseURL":"https://override.invalid"}'') ON CONFLICT DO NOTHING',
    'INSERT INTO mu3lab_provider_probe (id, key_vaults) VALUES (''openai'', ''credential'') ON CONFLICT DO NOTHING',
    'INSERT INTO mu3lab_provider_probe (id, config) VALUES (''openai'', ''[]'') ON CONFLICT DO NOTHING',
    'INSERT INTO mu3lab_provider_probe (id, enabled) VALUES (''other'', true)',
    'UPDATE mu3lab_provider_probe SET config = ''{"baseURL":"https://override.invalid"}'' WHERE id = ''openai''',
    'UPDATE mu3lab_provider_probe SET key_vaults = ''credential'' WHERE id = ''openai'''
  ]) LOOP
    rejected := false;
    BEGIN
      EXECUTE statement;
    EXCEPTION WHEN raise_exception THEN
      rejected := true;
    END;
    IF NOT rejected THEN
      RAISE EXCEPTION 'Provider policy accepted a private override: %', statement;
    END IF;
  END LOOP;
END $$;
ROLLBACK;
"""
        )
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                os.environ["MU3LAB_TEST_POSTGRES_CONTAINER"],
                "psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "postgres",
                "-d",
                "lobehub",
            ],
            input=sql,
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
