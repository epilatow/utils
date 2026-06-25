# utils

A personal collection of command-line utilities. Each entry under `bin/` is a
self-contained tool run through [uv](https://docs.astral.sh/uv/). See
[DEVELOPMENT.md](DEVELOPMENT.md) for how the repo is laid out, built, and
tested.

## Installation

To use the utilities in this repo:

```bash
# Update PATH and MANPATH
PATH=$PATH:$HOME/.local/bin
MANPATH=$MANPATH:$HOME/.local/share/man

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone this repo
git clone https://github.com/epilatow/utils

# Use linkfiles to link the utilities and man pages into ~/.local
utils/bin/linkfiles install utils/bin $HOME/.local/bin
utils/bin/linkfiles install utils/share $HOME/.local/share
```
