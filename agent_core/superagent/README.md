# VELIA developer role

The existing VELIA Agent Core builds DeepSeek Harness **0.2.1-rc.3** at
`d02ea370ee15556a643207eeb21810c29ca8c437` and the MIT-licensed
`deepseek-ai/dsh-superagent-prototype` at
`6337fc823ce20251c8de8085948165c81aaa701e`. Both source revisions are checked
before building. The extension is staged with its production dependencies;
Harness supplies its own service peers, so the plugin does not load a second
copy of those services. Licenses and provenance are copied into runtime notices.

The developer role uses its own `velia-developer` profile and process on the same
Agent Core runtime, with VELIA's existing authenticated model gateway and token
refresh proxy. The Browser Agent profile does not load the developer extension.

## Activation

Run on a dedicated, unprivileged Linux/macOS OS account containing only the
worker's credentials and checkout. The published prototype uses POSIX commands
and absolute POSIX paths, so its worker is not a native Windows feature.
Independent worktrees are task separation, **not a security sandbox**. Never run
this process in the same trust boundary as other users' browser profiles,
production database credentials, wallet keys or deployment credentials.

The upstream GitHub Source needs a GitHub **user account** for the agent, with
write access to the selected repository. An installation token/GitHub App cannot
replace it: the Source reads the user's GitHub notifications. Configure a `gh`
wrapper signed in only as that agent. Its identity must differ from the people
on `allowedUsers`.

Create an operator-owned config outside the repository (no secrets in it):

```json
{
  "home": "/srv/velia-developer/state",
  "checkout": "/srv/velia-developer/repository",
  "githubCLI": "/srv/velia-developer/bin/gh-agent",
  "repository": "SergeyTo95/deepalpha-bot",
  "allowedUsers": ["SergeyTo95"],
  "gateway": "https://deepalpha-ai.com/desktop-api/v1",
  "baseBranch": "feature/velia-desktop-harness",
  "model": "velia-flash"
}
```

`checkout` must be an existing clean clone of that repository with its HTTPS
origin, separate from the task worktree root. Install git and GitHub CLI on the
host. Then, from `desktop/`:

```sh
npm ci
npm run build:harness
npm run superagent -- /path/to/operator-config.json --check
npm run superagent -- /path/to/operator-config.json --pair
```

`--pair` reads a VELIA device connection code interactively and keeps the rotating
session in a mode-600 file inside the mode-700 worker home. Subsequent starts
omit `--pair`. The process runs in the foreground; supervise it on the host
without publishing the loopback management panel. No infrastructure is created
by these commands. The emitted panel link contains a local login token and must
not be posted publicly.

On first boot the launcher configures one worker, the explicit sender list,
proactive intake **off**, and the selected VELIA model. `velia-flash` is the
default; switching to `velia-pro` is explicit and can incur the existing
provider's charges. No new model subscription or automatic paid fallback is
added. Settings are preserved on subsequent starts, including an operator pause.

Ask the agent from an allowed GitHub account by commenting
`@<agent-login> <task>` on the selected repository. The source polls mentions
and the worker returns to the original thread. Prompts require draft PRs and
manual merging, with no deployments or repository-setting changes. These are
agent instructions; enforce mandatory approvals separately with GitHub branch
protection. Do not expose this prototype directly as a multi-tenant service.

## Verification

```sh
npm test
npm run prepare:runtime
npm run qualify:superagent
```

The qualification launches the real packaged Harness and extension, authenticates
to the management API, configures a local GitHub CLI fixture, verifies the
policy, and restarts the process to check persistence and preservation of an
operator pause. It makes no GitHub writes and uses no paid inference. Unit tests
also exercise real git worktrees, ownership of settings, and management-cookie
boundaries. These checks do **not** establish real-model coding quality.

## Current release boundary

This is an opt-in worker integration, not an activated production service.
Production activation requires the agent account's CLI authentication and a
separate execution trust boundary. The current hosted Browser Agent has no
GitHub agent credentials. Native clients do not gain terminal access from this
change. A VELIA-chat task Source/bridge and per-user execution environments must
be implemented and verified before advertising conversational coding to all
users; this integration currently receives tasks through GitHub.

The prototype retains historical SQLite records indefinitely. Monitor disk use;
do not delete its live database or worktrees to reclaim space.

## Initial verification, 2026-11-01

- 27 Node adapter tests and 41 Python gateway/browser-session/storage tests passed.
- Harness source artifacts: 7 build stages passed; Host and Client aggregate
  TypeScript checks passed. Production runtime packaging passed.
- Packaged-runtime checks passed: local file tool round trip through the VELIA
  proxy (2 scripted inference rounds), authenticated Web startup, real extension
  startup/settings/bootstrap and restart/pause persistence. Inference and GitHub
  in these checks use local fixtures, not external accounts.
- The unmodified upstream extension's typecheck and build passed. Its complete
  unit suite reported 1196 passes and one failure in Feishu event-consumer health
  reporting (`tests/sources.spec.ts:1097`); the isolated source test repeated
  that failure. Feishu is disabled in this VELIA profile.
- No live coding task, GitHub PR creation by the worker, native Windows worker,
  or production activation is claimed by these results.
