# Mu3Lab wording review

Reviewed September 29, 2026.

All 16 catalog entries now have dedicated descriptions of their purpose and main capabilities. The catalog previously used sign-in or installation instructions as descriptions. Descriptions now appear in Installed, Discover, and each app’s About section; sign-in notes remain separate.

| Area | Changes and accuracy findings |
| --- | --- |
| Apps | Describe actual features and each service’s role in Mu3Lab. Installed includes stopped apps, so its introduction no longer says everything is running. Blocked apps say “Unavailable” rather than implying a release commitment. |
| App details and setup | Account setup can involve registration, sign-in, or saving recovery credentials. Single sign-on requires account ownership checks before local login is disabled. |
| Home and onboarding | Remove the claim that every app password is already saved. Explain Vaultwarden’s separate master password. Use clearer action labels and completion wording. |
| Home agenda | Distinguish an unavailable calendar from one with no upcoming events. Cached empty results are described as the last sync. |
| Devices | Describe authorized Tailscale access. Browser shortcuts and installation depend on the app, browser, and device. |
| AI providers | Specify supported providers. Quotas vary; additional providers offer fallback capacity without guaranteeing availability. Suggested vault entries do not create provider accounts. |
| Sign-in | Distinguish shared Authentik sign-in from separate app accounts and additional access gates. User access also depends on policies. |
| Calendar | Explain the encrypted app credential used by Mu3Lab, including automatic connection. Users do not need to enter their Nextcloud password. |
| Chat integrations | Automatic credential creation is a capability, not a guarantee that every integration stays connected. Distinguish reading, preparing drafts, and changing data. |
| Security and backups | Credential handoff expiry removes access to the saved copy, not the actual app password. An unverified backup does not necessarily mean no repository exists. Preserve the existing warning about local backups and machine loss. |
| System | Clarify network authorization and describe monitoring and maintenance tasks. |
| Chat, navigation, activity, status screens | Reviewed the dashboard-owned labels and empty states. Retained clear, accurate wording. Text inside the embedded LobeChat application is maintained upstream. |

## Description sources

Descriptions were checked against official project documentation or repositories and the deployment configuration in this checkout. Mu3Lab-specific claims use services.yaml, Compose definitions, and the control-plane implementation. Features from newer upstream versions were not assumed to be enabled in Mu3Lab.

- [Caddy](https://github.com/caddyserver/caddy), [Authentik](https://github.com/goauthentik/authentik), [Vaultwarden](https://github.com/dani-garcia/vaultwarden)
- [Ollama](https://ollama.com/), [LiteLLM](https://docs.litellm.ai/), [FreeLLMAPI](https://github.com/TashfeenAhmed/FreeLLMAPI), [LobeChat](https://github.com/lobehub/lobehub)
- [SurfSense](https://github.com/MODSetter/SurfSense), [Firecrawl](https://github.com/firecrawl/firecrawl)
- [Actual Budget](https://actualbudget.org/), [Immich](https://github.com/immich-app/immich), [Mealie](https://github.com/mealie-recipes/mealie)
- [AdventureLog](https://github.com/seanmorley15/AdventureLog), [Paperless-ngx](https://github.com/paperless-ngx/paperless-ngx), [Nextcloud](https://github.com/nextcloud/server), [Baby Buddy](https://github.com/babybuddy/babybuddy)

## Copy maintenance

Add or update each service’s summary in services.yaml when its supported capabilities change. Keep deployment instructions and identity notes in their existing fields. Recheck external provider quotas and companion-client instructions when versions change. Backend diagnostic messages and wording inside third-party apps remain outside this dashboard editorial pass.
