{ pkgs, unwrapped }:
pkgs.symlinkJoin {
  inherit (unwrapped) name pname version;
  paths = [ unwrapped ];
  nativeBuildInputs = [ pkgs.python3 ];
  postBuild = ''
    SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt \
      python3 ${../tests/test_authenticated_media.py} "$out/bin/tuwunel"
  '';
  passthru = { inherit unwrapped; };
  meta = unwrapped.meta // { mainProgram = "tuwunel"; };
}
