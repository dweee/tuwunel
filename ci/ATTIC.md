# Package builders and Attic publication

Native build workflows are installed in `.github/workflows/`. Every successful
build uploads a Nix runtime-closure archive before any cache publisher runs.
Archives remain downloadable for one day whether uploads are disabled or fail.

## Installer and platform support

All builders use `nixbuild/nix-quick-install-action` v35, pinned to commit
`9f63be77f412a248c9d9a65a4c82cf066cdf8f0c`, and explicitly select Nix 2.34.7.
This action installs single-user Nix on GitHub-hosted runners. The workflows run on GitHub-hosted Apple Silicon macOS and x86/ARM Linux after
publication, rather than existing self-hosted Forgejo workers.

Source: https://github.com/nixbuild/nix-quick-install-action/tree/9f63be77f412a248c9d9a65a4c82cf066cdf8f0c

## Configure at go-live

Configure the following in each repository that will run package jobs:

* The workflow's existing CI enable variable: `DAVE_PACKAGES_CI_ENABLED=true`
  in the catalog, or `DAVE_FORK_CI_ENABLED=true` in application forks.
* Variable `ATTIC_PUSH_ENABLED=true` to publish successful builds.
* Variable `ATTIC_ENDPOINT`: the HTTPS URL of your existing Attic server.
* Variable `ATTIC_CACHE`: the existing cache name, without a server prefix.
* Secret `ATTIC_TOKEN`: a dedicated, expiring token scoped to pull/push that
  cache. It does not need cache creation, configuration, deletion or admin rights.

An administrator can issue that scoped token on the Attic server with
`atticadm make-token --sub github-packages --validity 90d --pull CACHE --push CACHE`.
Keep the result private and enter it using the hosting service's secret UI.
Use a candidate/staging cache; production promotion remains a separate trusted
local operation. Do not supply a production or personal-cache write token.
No existing host credentials are copied into the repositories. The upload token
will be available to GitHub's runner when the publication step goes live.

Optional variables `ATTIC_SUBSTITUTER` (the cache's public Nix substituter URL)
and `ATTIC_PUBLIC_KEY` (its public signing key) let builds reuse existing cache
entries. Configure both for an anonymously readable cache. Private cache reads
are not configured here; no write token is exposed to the build step. Without
these variables builds read from cache.nixos.org and still upload to Attic.

Dispatch a manual run on the repository's default branch, selecting one package
for the first timing and end-to-end cache check. Application forks own their
own jobs; the catalog excludes their aliases. The catalog uses pinned public fork inputs.

## Publication behavior

Only successful push/manual runs on the default branch can upload. Builds on another
branch remain build-only. A failed build never reaches publication. Enabling
uploads with missing/invalid settings or a failed Attic upload fails the job;
there is no success claim or silent fallback for a package that was not cached.

The build job never receives an Attic upload credential. It exports only the
selected output closures as NAR data into an immutable artifact named for the
platform, package and source commit, retained for one day. A separate disposable Linux
publisher installs `attic-client` and its closure from cache.nixos.org before
importing that data; source builds are forbidden in the publisher. It does not
run any transferred program. Only the final publication step receives the token
and passes the validated output roots to `ci/push-attic.py`.
After import, official signatures are restored from `cache.nixos.org` before
the token is supplied. Attic skips those already published upstream dependencies
and uploads the custom outputs and remaining runtime dependencies. It does not upload the entire runner
store, install deployment hooks or change fleet cache promotion rules.

A temporary mode-0600 Attic configuration under the runner temporary directory
holds the step's token. The helper removes the token from the child environment,
never passes it as a command argument, and removes the temporary configuration
after both successful and failed uploads. No token enters Nix configuration,
Nix derivations, Git files or artifacts.

Build summaries record package build duration and successful upload duration.
The publisher is a separate job with a 45-minute timeout; upload has a 30-minute
timeout. NAR export/import is bounded to 4 GiB of uncompressed data by default.
Variable `ATTIC_MAX_CLOSURE_MIB` can explicitly select 1–4096 MiB. Oversize
closures fail before publication; measure artifact storage, transfer time and
runner disk use during the first pilot before raising the cap.
There are no automatic retries of authentication or other upload failures.

A successful upload does not prove a later machine can substitute the package:
verify the same output store path is readable from the selected cache with the
intended consumer credentials and signing-key trust. Consumers also need matching
inputs and recipes. No fleet configuration is changed by these workflows.

Reference CLI behavior: https://docs.attic.rs/reference/attic-cli.html
Token scopes: https://docs.attic.rs/reference/atticadm-cli.html

## Recover a package without rebuilding

A separate trusted workflow copies successful package artifact ZIPs to the
`packages-RUN_ID` prerelease, independently of the build's final status or Attic.
It verifies release asset sizes/checksums before deleting the duplicate Actions
artifact. Backup copying has no Attic credentials and never runs package content.
Large ZIPs are split into numbered parts below the release-asset size limit.
Backup tags mark the default branch when the archive is published. The release
notes, original workflow run and SHA in each asset name identify the built source;
the automatic source ZIP/tarball attached by GitHub is not the package archive.

Download that prerelease's assets with `gh release download packages-RUN_ID`.
For split archives, concatenate the ordered `.zip.part-0000` files into the
original `.zip`, then verify its `.sha256` file. Unzip it and verify `SHA256SUMS`.
Restore its runtime closure with `gzip -dc closure.nar.gz | nix-store --import`;
then use `outputs.txt` with the Attic publisher after cache access is repaired.
The `Preserve package backups` workflow can also be dispatched manually with an
older run ID while its original Actions artifacts are still available.
