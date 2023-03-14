{ pkgs ? import <nixpkgs> {} }:
pkgs.mkShell {
  name = "homelab";
  buildInputs = with pkgs; [
    _1password
    gnupg
    sops
  ];
}
