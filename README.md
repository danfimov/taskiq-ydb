# taskiq + ydb

[![Python](https://img.shields.io/badge/python-3.10_|_3.11_|_3.12_|_3.13-blue)](https://www.python.org/)
[![Linters](https://github.com/danfimov/taskiq-ydb/actions/workflows/code_check.yml/badge.svg)](https://github.com/danfimov/taskiq-ydb/actions/workflows/code_check.yml)

Plugin for taskiq that adds a new result backend based on YDB.

## Installation

This project can be installed using pip/poetry/uv (choose your preferred package manager):

```bash
pip install taskiq-ydb
```

## Usage

Let's see the example with the redis broker and YDB result backend:

```Python
# example.py
import asyncio
import logging

import taskiq_redis
from ydb.aio.driver import DriverConfig

from taskiq_ydb import YdbResultBackend


logger = logging.getLogger(__name__)

result_backend = YdbResultBackend(
    driver_config=DriverConfig(
        endpoint="grpc://localhost:2136",
        database="/local",
    ),
)

# Or you can use PubSubBroker if you need broadcasting
broker = taskiq_redis.ListQueueBroker(
    url="redis://redis.taskiq-ydb.orb.local:6379",
).with_result_backend(result_backend)


@broker.task(task_name="best_task_ever")
async def best_task_ever() -> None:
    """Solve all problems in the world."""
    logger.info("Task started")
    await asyncio.sleep(2.0)
    logger.info("Task finished")


async def main() -> None:
    logger.info("Starting application")
    await broker.startup()
    logger.info("Broker started")
    await best_task_ever.kiq()
    logger.info("Task queued")
    await broker.shutdown()


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
```

Example can be run using the following command:

```bash
# Start broker
python3 -m example
```

```bash
# Start worker for executing command
taskiq worker example:broker
```

## Configuration

- `driver_config`: connection config for YDB client, you can read more about it in [YDB documentation](https://ydb.tech/docs/en/concepts/connect);
- `table_name`: name of the table in PostgreSQL to store TaskIQ results;
- `serializer`: type of `TaskiqSerializer` default is `PickleSerializer`;
- `pool_size`: size of the connection pool for YDB client, default is `10`.
