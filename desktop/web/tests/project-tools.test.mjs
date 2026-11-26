import assert from 'node:assert/strict';
import {test} from 'node:test';
import {validatedCloudShellUrl} from '../project-tools.mjs';

test('Cloud Shell destination stays on Google and the expected GitHub repository', () => {
  const base = 'https://shell.cloud.google.com/cloudshell/editor';
  const query = new URLSearchParams({
    cloudshell_git_repo:'https://github.com/owner/game',
    cloudshell_git_branch:'feat/velia-game',
    cloudshell_workspace:'games/velia-5cc8b9ac-1f09-4c8f-ac1e-c04ea468470b',
    show:'ide',
  });
  const url = `${base}?${query}`;
  assert.equal(validatedCloudShellUrl(url,'owner/game'),url);
  assert.equal(validatedCloudShellUrl(url,'owner/private'), '');
  assert.equal(validatedCloudShellUrl(url + '&cloudshell_git_repo=https://evil.invalid/repo','owner/game'), '');
  assert.equal(validatedCloudShellUrl(url + '&cloudshell_image=example','owner/game'), '');
  assert.equal(validatedCloudShellUrl(url.replace('shell.cloud.google.com','example.com'),'owner/game'), '');
  assert.equal(validatedCloudShellUrl(undefined,'owner/game'), '');
});
