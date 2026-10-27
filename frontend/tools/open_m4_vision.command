#!/bin/sh
set -eu

tool_path=$0
if [ -L "$tool_path" ]; then
  tool_path=$(/usr/bin/readlink "$tool_path")
fi
tool_directory=$(CDPATH= cd -- "$(dirname -- "$tool_path")" && pwd)
repository_root=$(CDPATH= cd -- "$tool_directory/../.." && pwd)
# The atlas lives on the notes branch, checked out at notes-file_manager/ (AGENTS.md).
exec /usr/bin/open -a Safari \
  "$repository_root/notes-file_manager/planning/frontend/visual/frontend-concept-atlas.html"
