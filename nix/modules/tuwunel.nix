{ self }:
{ lib, pkgs, ... }:
{
  # Reuse nixpkgs' service, settings and credential handling.
  services.matrix-tuwunel.package = lib.mkDefault
    self.packages.${pkgs.stdenv.hostPlatform.system}.matrix-tuwunel;
}
