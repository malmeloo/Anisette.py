{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  packages = with pkgs; [
    python312
    uv
    nodejs_23
    wrangler
  ];

  shellHook = ''
  if [[ -d .venv/ ]]; then
    source .venv/bin/activate
  fi
  '';
}