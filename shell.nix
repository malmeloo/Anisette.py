{ pkgs ? import <nixpkgs> {} }:

let
  unstable = import (fetchTarball https://channels.nixos.org/nixos-unstable/nixexprs.tar.xz) { };
in
pkgs.mkShell {
  packages = with pkgs; [
    (unstable.python312.withPackages (ps: [ps.uv]))
    nodejs_23
    wrangler
  ];

  shellHook = ''
  if [[ -d .venv/ ]]; then
    source .venv/bin/activate
  fi
  '';
}