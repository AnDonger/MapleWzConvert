# MapleWzConvert SDK

This directory contains the project-local WZ SDK used by the command-line
tools in this repository.

## Contents

- `wzpy/`: vendored WZ reader/writer runtime from `wz-python`, with local
  fixes needed by this project.
- `WZPY_LICENSE`: upstream license for the vendored `wzpy` code.

## Maintenance Rules

- Treat `maplewz_sdk/wzpy` as the maintained runtime for this project.
- Do not depend on `.tools/wz-python` for normal script execution.
- Do not add shortcuts that copy original `.wz` bytes instead of exercising
  the parser/writer when the user is testing true unpack/repack behavior.
- Keep WZ data semantics unchanged unless the user explicitly asks for a
  conversion rule.
