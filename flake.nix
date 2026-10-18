{
  description = "Tuwunel fork with authenticated object-storage media redirects";
  inputs = {
    attic.url = "github:zhaofengli/attic?ref=main";
    cachix.url = "github:cachix/cachix?ref=master";
    complement = {
      url = "github:matrix-construct/complement?ref=main";
      flake = false;
    };
    crane = {
      url = "github:ipetkov/crane?ref=master";
    };
    fenix = {
      url = "github:nix-community/fenix?ref=main";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    flake-compat = {
      url = "github:edolstra/flake-compat?ref=master";
      flake = false;
    };
    flake-utils.url = "github:numtide/flake-utils?ref=main";
    nix-filter.url = "github:numtide/nix-filter?ref=main";
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    rocksdb = {
      url = "github:matrix-construct/rocksdb?ref=tuwunel-changes";
      flake = false;
    };
    liburing = {
      url = "github:axboe/liburing?ref=master";
      flake = false;
    };
  };

  outputs = inputs:
    let
      original = (import ./upstream-flake.nix).outputs inputs;
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forSystems = inputs.nixpkgs.lib.genAttrs systems;
      unwrappedFor = system: original.packages.${system}.default.override {
        default_features = false;
        features = [
          "brotli_compression" "direct_tls" "element_hacks" "gzip_compression"
          "media_thumbnail" "release_max_log_level" "systemd" "url_preview"
          "zstd_compression" "jemalloc" "jemalloc_conf" "io_uring" "ldap"
        ];
      };
      packageFor = system: import ./nix/package.nix {
        pkgs = import inputs.nixpkgs {
          inherit system;
          config.allowUnfree = false;
        };
        unwrapped = unwrappedFor system;
      };
    in original // {
      packages = original.packages // forSystems (system:
        original.packages.${system} // {
          default = packageFor system;
          matrix-tuwunel = packageFor system;
          tuwunel-deps = (unwrappedFor system).cargoArtifacts;
        });
      checks = forSystems (system: { authenticated-media = packageFor system; });
      nixosModules.default = import ./nix/modules/tuwunel.nix { self = inputs.self; };
      nixosModules.tuwunel = import ./nix/modules/tuwunel.nix { self = inputs.self; };
    };
}
