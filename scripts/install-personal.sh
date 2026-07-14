#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
codex_home=${CODEX_HOME:-"$HOME/.codex"}
skills_home="$codex_home/skills"
agents_home="$codex_home/agents"
runtime_root="$codex_home/orchestration"
runtime_home="$runtime_root/scripts"
config_home="$runtime_root/config"
ownership_file="$runtime_root/owned-paths.txt"
mode=install

if [ "${1:-}" = "--upgrade" ]; then
  mode=upgrade
elif [ "$#" -ne 0 ]; then
  echo "Usage: $0 [--upgrade]" >&2
  exit 2
fi

stage=$(mktemp -d "${TMPDIR:-/tmp}/codex-orchestration-install.XXXXXX")
trap 'rm -rf "$stage"' EXIT HUP INT TERM
mkdir -p "$stage/skills" "$stage/agents" "$stage/scripts" "$stage/config"
for source in "$root"/skills/*; do cp -R "$source" "$stage/skills/"; done
for source in "$root"/agents/*.toml; do cp "$source" "$stage/agents/"; done
cp "$root/scripts/orchestrate.py" "$root/scripts/resolve_config.py" "$stage/scripts/"
cp "$root/skills/scale-agent-pool/references/defaults.toml" "$stage/config/defaults.toml"

refuse() {
  echo "Refusing to replace unverified destination: $1" >&2
  exit 1
}

for source in "$stage"/skills/*; do
  name=$(basename "$source")
  destination="$skills_home/$name"
  if [ -e "$destination" ]; then
    [ "$mode" = upgrade ] || refuse "$destination"
    if [ -f "$ownership_file" ]; then
      grep -Fxq "skills/$name" "$ownership_file" || refuse "$destination"
    else
      [ -f "$destination/SKILL.md" ] || refuse "$destination"
      [ -f "$destination/agents/openai.yaml" ] || refuse "$destination"
      grep -Eq "^name:[[:space:]]*$name$" "$destination/SKILL.md" || refuse "$destination"
    fi
  fi
done
for source in "$stage"/agents/*.toml; do
  name=$(basename "$source" .toml)
  destination="$agents_home/$name.toml"
  if [ -e "$destination" ]; then
    [ "$mode" = upgrade ] || refuse "$destination"
    if [ -f "$ownership_file" ]; then
      grep -Fxq "agents/$name.toml" "$ownership_file" || refuse "$destination"
    else
      grep -Eq "^name[[:space:]]*=[[:space:]]*\"$name\"$" "$destination" || refuse "$destination"
      grep -Eq '^model[[:space:]]*=[[:space:]]*"gpt-5\.6-' "$destination" || refuse "$destination"
    fi
  fi
done
for name in orchestrate.py resolve_config.py; do
  destination="$runtime_home/$name"
  if [ -e "$destination" ]; then
    [ "$mode" = upgrade ] || refuse "$destination"
    if [ -f "$ownership_file" ]; then
      grep -Fxq "orchestration/scripts/$name" "$ownership_file" || refuse "$destination"
    elif [ "$name" = orchestrate.py ]; then
      grep -q "Durable, bounded orchestration" "$destination" || refuse "$destination"
    else
      grep -q "def resolve(" "$destination" || refuse "$destination"
    fi
  fi
done
if [ -e "$config_home/defaults.toml" ]; then
  [ "$mode" = upgrade ] || refuse "$config_home/defaults.toml"
  if [ -f "$ownership_file" ]; then
    grep -Fxq "orchestration/config/defaults.toml" "$ownership_file" || refuse "$config_home/defaults.toml"
  else
    grep -q 'route_attestation = "requested-via-cli-arguments-not-runtime-attested"' \
      "$config_home/defaults.toml" || refuse "$config_home/defaults.toml"
  fi
fi

mkdir -p "$skills_home" "$agents_home" "$runtime_home" "$config_home"
for source in "$stage"/skills/*; do
  name=$(basename "$source")
  destination="$skills_home/$name"
  rm -rf "$destination"
  cp -R "$source" "$destination"
done
for source in "$stage"/agents/*.toml; do
  destination="$agents_home/$(basename "$source")"
  rm -f "$destination"
  cp "$source" "$destination"
done
for source in "$stage"/scripts/*.py; do
  destination="$runtime_home/$(basename "$source")"
  rm -f "$destination"
  cp "$source" "$destination"
done
cp "$stage/config/defaults.toml" "$config_home/defaults.toml"

ownership_tmp="$runtime_root/.owned-paths.$$"
{
  for source in "$stage"/skills/*; do echo "skills/$(basename "$source")"; done
  for source in "$stage"/agents/*.toml; do echo "agents/$(basename "$source")"; done
  echo "orchestration/scripts/orchestrate.py"
  echo "orchestration/scripts/resolve_config.py"
  echo "orchestration/config/defaults.toml"
} > "$ownership_tmp"
mv "$ownership_tmp" "$ownership_file"

echo "Installed orchestration skills in $skills_home"
echo "Installed orchestration agents in $agents_home"
echo "Installed orchestration runner and defaults in $runtime_root"
echo "Restart Codex before using them."
