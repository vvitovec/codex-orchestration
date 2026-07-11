#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
codex_home=${CODEX_HOME:-"$HOME/.codex"}
skills_home="$codex_home/skills"
agents_home="$codex_home/agents"

mkdir -p "$skills_home" "$agents_home"

for source in "$root"/skills/*; do
  name=$(basename "$source")
  destination="$skills_home/$name"
  if [ -e "$destination" ]; then
    echo "Refusing to overwrite $destination" >&2
    exit 1
  fi
done

for source in "$root"/agents/*.toml; do
  name=$(basename "$source")
  destination="$agents_home/$name"
  if [ -e "$destination" ]; then
    echo "Refusing to overwrite $destination" >&2
    exit 1
  fi
done

for source in "$root"/skills/*; do
  cp -R "$source" "$skills_home/"
done

for source in "$root"/agents/*.toml; do
  cp "$source" "$agents_home/"
done

echo "Installed orchestration skills in $skills_home"
echo "Installed orchestration agents in $agents_home"
echo "Restart Codex before using them."
