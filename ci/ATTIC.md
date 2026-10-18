# Native Tuwunel builds and private cache publication

The two native Linux platforms retain their staged build graph: build and
publish `tuwunel-deps` first, then build the authenticated-media-validated
`matrix-tuwunel` package. Native runners are `ubuntu-24.04` and
`ubuntu-24.04-arm`; no Darwin platform is added.

All native publishers write only to **https://attic.monke.fail/flakey**, a private
Attic cache. Its public signing key is
`flakey:mDx4DiNpH5w1Pl8OAKFFWq1ZsypA9lbaFKNiRWhqYE8=`.

## Credentials and reads

The administrator issues a dedicated token with pull and push on `flakey` only,
valid for at most 90 days:

```sh
atticadm make-token --sub github-tuwunel --validity 90d --pull flakey --push flakey
```

The repository owner installs that token from a private file, without printing
it or putting it in command arguments:

```sh
token_file=/secure/path/flakey-publisher.token
gh secret set ATTIC_TOKEN --repo dweee/tuwunel < "$token_file"
gh variable set ATTIC_ENDPOINT --repo dweee/tuwunel --body https://attic.monke.fail
gh variable set ATTIC_CACHE --repo dweee/tuwunel --body flakey
gh variable set ATTIC_SUBSTITUTER --repo dweee/tuwunel --body https://attic.monke.fail/flakey
gh variable set ATTIC_PUBLIC_KEY --repo dweee/tuwunel --body 'flakey:mDx4DiNpH5w1Pl8OAKFFWq1ZsypA9lbaFKNiRWhqYE8='
```

Existing `DAVE_FORK_CI_ENABLED` and `ATTIC_PUSH_ENABLED` gates remain effective.
The workflow pins the cache endpoint and name independently of the compatibility
variables. Rotate the scoped secret before it expires.

Only default-branch push or manual-dispatch builds receive the token. Their read
helper creates a mode-0600 netrc under `RUNNER_TEMP` and configures Nix's
`netrc-file`. The token never enters Nix expressions, derivations, the store or
artifacts. Pull requests and non-default branches receive no Attic secret and
use upstream substitutes. Only successful default-branch push/dispatch runs can
publish, for both dependency and server stages.

## Public artifact boundary

Both stages check the entire recursive derivation graph before building with
logs. The policy rejects disc-dependent Wii outputs, VeraCrypt, NVIDIA 580,
ffmpeg-full and subnetter, including transitive references. Nixpkgs imports
explicitly reject unfree packages. Evaluation failures produce a generic
diagnostic instead of exposing captured evaluator output.

Before creating any NAR archive, the transfer helper checks the selected roots'
full runtime closure again. A separate publisher installs the trusted pinned
Attic client from the official cache with local builds forbidden, imports only
NAR data without executing it, checks the imported runtime closure and restores
upstream signatures before receiving the write credential.

Publication first pushes selected roots with `--no-closure` and
`--ignore-upstream-cache-filter`, ensuring their narinfo exists in `flakey` even
when upstream already signed them. A second normal closure push retains upstream
filtering for dependencies. Either upload failing fails publication. Temporary
Attic configuration is private, the token is omitted from client arguments and
environment, and cleanup occurs on success or failure.

Selected free closure archives remain immutable Actions artifacts for one day.
The existing checksum-verified `Preserve package backups` workflow can retain
those same public artifacts as release backups; it receives no Attic token.
The default uncompressed transfer cap is 4 GiB, configurable with
`ATTIC_MAX_CLOSURE_MIB` from 1 through 4096. No whole-store export occurs.

The pinned shared upstream synchronization controller exports public closures
without these guards. Its job is paused unless
`GUARDED_UPSTREAM_SYNC_ENABLED == 'true'`; cache publication is disabled and no
Attic token is forwarded to it. Resume only after its own build/export policy
has been reviewed and brought into line with the native workflows.

## Credential-free checks

```sh
python3 ci/test-push-attic.py
python3 ci/test-transfer-attic.py
python3 ci/test-public-closure.py
python3 ci/test-configure-attic-read.py
```
