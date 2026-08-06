# VELIA Repowise mirror synchronization

## Purpose

Railway attaches the persistent volume directly to the `velia-repowise` service. Mirror synchronization therefore runs as a background component inside the same standalone service, while the public HTTP surface remains read-only.

The synchronization component can only:

- create a local bare mirror with `git clone --mirror`;
- update that mirror with `git fetch --prune`;
- enumerate local branch refs.

It has no push, commit, branch creation, pull request, merge, auto-merge or deployment operation.

## Required GitHub credential

Create a fine-grained personal access token restricted to selected repositories only.

Required repository permission:

```text
Contents: Read-only
```

Do not grant repository administration, actions write, contents write, pull requests write, workflows write or organization access.

The token is stored only in the `velia-repowise` Railway service as:

```text
VELIA_REPOWISE_GITHUB_READ_TOKEN=<secret>
```

Git receives the token through `GIT_ASKPASS`. It is not embedded in repository URLs, command arguments, health responses or logs.

## Repository configuration

Configure credential-free HTTPS remotes:

```text
VELIA_REPOWISE_GITHUB_REPOSITORIES_JSON={"SergeyTo95/deepalpha-bot":"https://github.com/SergeyTo95/deepalpha-bot.git"}
```

The service derives the local mirror path automatically:

```text
/data/velia-repowise/mirrors/SergeyTo95--deepalpha-bot.git
```

Later, Android can be added to the same JSON object after the token is explicitly granted read-only access to `SergeyTo95/deepalpha-android`.

## Optional synchronization limits

```text
VELIA_REPOWISE_SYNC_INTERVAL_SECONDS=60
VELIA_REPOWISE_SYNC_TIMEOUT_SECONDS=600
```

The API starts immediately. Synchronization runs in the background and never blocks `/health`. Until the requested SHA exists in the mirror, planning returns `sha_not_in_mirror` and the backend provider fails open to its existing GitHub evidence.

## Health response

`GET /health` exposes only bounded operational state:

```json
{
  "mirror_sync": {
    "configured": true,
    "enabled": true,
    "repositories": 1,
    "ready": 1,
    "failed": 0,
    "interval_seconds": 60,
    "last_success_at": 0
  }
}
```

It never returns repository URLs, filesystem paths or tokens.

## Production activation order

1. Deploy the mirror-sync code with context integration still disabled in `deepalpha-bot`.
2. Add the fine-grained read-only GitHub token to `velia-repowise`.
3. Add `VELIA_REPOWISE_GITHUB_REPOSITORIES_JSON`.
4. Confirm `/health` reports `ready=1` and `failed=0`.
5. Confirm the production branch head exists inside the local mirror through a real context request.
6. Add the private Railway URL and matching service token to `deepalpha-bot`.
7. Keep `VELIA_DEVELOPER_REPOWISE_CONTEXT_ENABLED=false` for the first connectivity deployment.
8. Run a real exact-SHA private-network test.
9. Enable Planning context only after that test succeeds.

No step enables Autopilot merge or deployment capabilities.
