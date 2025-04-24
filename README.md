# taskiq + ydb

[![Python](https://img.shields.io/badge/python-3.10_|_3.11_|_3.12_|_3.13-blue)](https://www.python.org/)
[![Linters](https://github.com/danfimov/taskiq-ydb/actions/workflows/code_check.yml/badge.svg)](https://github.com/danfimov/taskiq-ydb/actions/workflows/code_check.yml)

Plugin for taskiq that adds a new result backend and broker based on YDB.

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

from ydb.aio.driver import DriverConfig

from taskiq_ydb import YdbBroker, YdbResultBackend


driver_config = DriverConfig(
    endpoint='grpc://localhost:2136',
    database='/local',
)

broker = YdbBroker(
    driver_config=driver_config,
).with_result_backend(YdbResultBackend(driver_config=driver_config))


@broker.task(task_name='best_task_ever')
async def best_task_ever() -> str:
    """Solve all problems in the world."""
    return 'Problems solved!'


async def main() -> None:
    """Start the application with broker."""
    await broker.startup()
    task = await best_task_ever.kiq()
    result = await task.wait_result()
    print(f'Task result: {result.return_value}')
    await broker.shutdown()


if __name__ == '__main__':
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

**Broker:**

- `driver_config`: connection config for YDB client, you can read more about it in [YDB documentation](https://ydb.tech/docs/en/concepts/connect);
- `topic_path`: path to the topic where tasks will be stored, default is `/taskiq-tasks`;
- `connection_timeout`: timeout for connection to database during startup, default is 5 seconds.
- `read_timeout`: timeout for read topic operation, default is 5 seconds.

**Result backend:**

- `driver_config`: connection config for YDB client, you can read more about it in [YDB documentation](https://ydb.tech/docs/en/concepts/connect);
- `table_name`: name of the table in PostgreSQL to store TaskIQ results;
- `serializer`: type of `TaskiqSerializer` default is `PickleSerializer`;
- `pool_size`: size of the connection pool for YDB client, default is `5`;
- `connection_timeout`: timeout for connection to database during startup, default is 5 seconds.
