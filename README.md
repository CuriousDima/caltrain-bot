Run locally:

```shell
uv run caltrain-bot
```

This uses the in-memory schedule database by default.

Run in debug mode with the on-disk `data/schedule.db` cache:

```shell
DEBUG=1 uv run caltrain-bot
```

Run tests:

```shell
uv run pytest
```
