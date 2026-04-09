# nix/packages.nix — KClaw Agent package built with uv2nix
{ inputs, ... }: {
  perSystem = { pkgs, system, ... }:
    let
      kclawVenv = pkgs.callPackage ./python.nix {
        inherit (inputs) uv2nix pyproject-nix pyproject-build-systems;
      };

      # Import bundled skills, excluding runtime caches
      bundledSkills = pkgs.lib.cleanSourceWith {
        src = ../skills;
        filter = path: _type:
          !(pkgs.lib.hasInfix "/index-cache/" path);
      };

      runtimeDeps = with pkgs; [
        nodejs_20 ripgrep git openssh ffmpeg
      ];

      runtimePath = pkgs.lib.makeBinPath runtimeDeps;
    in {
      packages.default = pkgs.stdenv.mkDerivation {
        pname = "kclaw";
        version = (builtins.fromTOML (builtins.readFile ../pyproject.toml)).project.version;

        dontUnpack = true;
        dontBuild = true;
        nativeBuildInputs = [ pkgs.makeWrapper ];

        installPhase = ''
          runHook preInstall

          mkdir -p $out/share/kclaw $out/bin
          cp -r ${bundledSkills} $out/share/kclaw/skills

          ${pkgs.lib.concatMapStringsSep "\n" (name: ''
            makeWrapper ${kclawVenv}/bin/${name} $out/bin/${name} \
              --suffix PATH : "${runtimePath}" \
              --set KCLAW_BUNDLED_SKILLS $out/share/kclaw/skills
          '') [ "kclaw" "kclaw" "kclaw-acp" ]}

          runHook postInstall
        '';

        meta = with pkgs.lib; {
          description = "AI agent with advanced tool-calling capabilities";
          homepage = "https://github.com/NousResearch/kclaw";
          mainProgram = "kclaw";
          license = licenses.mit;
          platforms = platforms.unix;
        };
      };
    };
}
