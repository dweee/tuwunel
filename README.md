# Tuwunel media-redirect fork

A preserved-history fork of [Tuwunel](https://github.com/matrix-construct/tuwunel), based on release v1.9.3. This fork adds authenticated redirects to existing private object-storage media while retaining the proxy fallback. Upstream's documentation is preserved in [README-upstream.md](README-upstream.md).

Authenticated media and thumbnail requests may return a five-minute signed redirect when `media_allow_redirect` is enabled and the stored object's headers are compatible. Authentication runs before object lookup. Responses use `Cache-Control: private, no-store`. Missing objects, legacy metadata, alternate filenames and strict framing policies retain the normal proxy path. This does not migrate a database or change bucket permissions.

## Nix packages

The flake supports native `x86_64-linux` and `aarch64-linux` builds:

```sh
nix build .#matrix-tuwunel
nix build .#tuwunel-deps
```

Upstream's Crane packaging remains in [upstream-flake.nix](upstream-flake.nix). Dependency compilation is a separate cacheable derivation. The production package retains nixpkgs-compatible features, builds Rust unit tests serially, and then starts a disposable server against a local object-storage fixture. Authentication, redirect signatures/expiry, thumbnails, multipart metadata and proxy fallbacks must pass before the exported package is realized. Test fixtures use synthetic credentials and loopback networking only.

Consumers should use an immutable fork commit and the same nixpkgs pin when they want the exact cached derivation. Overriding inputs or features may require a rebuild.

To use the existing nixpkgs service, import `inputs.tuwunel.nixosModules.default`, enable `services.matrix-tuwunel`, and configure its documented settings and credential files. This fork's module selects the package only; it does not configure a server identity, network addresses, storage credentials or deployment.

## CI and cache

GitHub Actions builds dependency packages first, publishes them from isolated jobs, and then builds validated server packages natively on Linux x86 and ARM runners. Successful dependency publication survives a later server build failure. Builds receive public read settings only. Separate disposable publisher jobs receive a token restricted to this repository's Attic cache. Checksummed closure archives are retained independently as release backups. Pull requests never receive the upload credential.

Configure the repository variables and scoped secret described in [ci/ATTIC.md](ci/ATTIC.md). The scheduled upstream controller is pinned to a reviewed commit; upstream release candidates must build, test and cache before any promotion is enabled. Protected-path conflicts require review. Forgejo keeps the canonical copy and performs lightweight validation without publishing fleet configuration.

The source fork preserves the upstream Apache-2.0 license and attribution. No private fleet repository history is imported.
