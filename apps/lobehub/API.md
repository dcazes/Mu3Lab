Mu3Lab uses LobeHub v2.2.18's supported v1 API with each person's approval.
`JWKS_KEY` is a JSON Web Key Set, not a single bare JWK. The pinned source
`packages/env/src/auth.ts` enables OIDC whenever it is set. The built-in
`lobehub-cli` client supports device authorization.

The least-privilege connection key includes `chat:read` as well as agent and
MCP read/write scopes. Reading topics is necessary to preserve conversations
when an app is removed. API envelopes contain `success` and `data`.

`FEATURE_FLAGS=-provider_settings,-openai_api_key` hides provider settings and
custom OpenAI key inputs. In this version's `packages/env/src/` there is no
server-side environment switch that forbids all user-supplied provider keys.
The removed database triggers enforced that extra restriction; the supported
configuration now hides those controls but cannot guarantee the same server
restriction. Upstream API keys and users remain independent of Authentik.

Sources: https://github.com/lobehub/lobehub/tree/v2.2.18/packages/env/src,
https://github.com/lobehub/lobehub/blob/v2.2.18/src/libs/oidc-provider/config.ts,
https://github.com/lobehub/lobehub/blob/v2.2.18/docs/self-hosting/advanced/feature-flags.mdx.
