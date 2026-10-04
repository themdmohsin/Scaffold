# Scaffold repo binding (`.scaffold/project.json`)

`scaffold init` / `scaffold link` bind a repo to its Scaffold project. The file is
**secret-free** and meant to be **committed** so every teammate's client targets the same project.

```json
{
  "version": 1,
  "project_id": "<project uuid>",
  "project_name": "optional display name",
  "engine_url": "https://engine.example.com",
  "dashboard_url": "https://dashboard.example.com",
  "enforce_contracts": true
}
```

Required: `version`, `project_id`, `engine_url`. Optional: the rest. Never put tokens here.

## Credentials (separate, never in the repo)

PATs live in the user's OS config dir: `~/.config/scaffold/credentials.json`
(`%USERPROFILE%\.config\scaffold` on Windows; override with `SCAFFOLD_CONFIG_DIR`):

```json
{ "version": 1, "tokens": { "https://engine.example.com": "scaffold_..." } }
```

Resolution order: `SCAFFOLD_TOKEN` env, then the file entry keyed by the binding's engine URL.
Env overrides: `SCAFFOLD_ENGINE_URL` (engine) and `SCAFFOLD_PROJECT_ID` (project) take precedence over
the binding - unset a stale `SCAFFOLD_ENGINE_URL` if the client talks to the wrong engine.

## .gitignore guidance

Commit `.scaffold/project.json`; do NOT ignore `.scaffold/`. Ignore local scratch only:
`.scaffold/*.local.json`.
