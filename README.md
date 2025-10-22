# taskiq + ydb

[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/taskiq-ydb?style=for-the-badge&logo=python)](https://pypi.org/project/taskiq-ydb/)
[![PyPI](https://img.shields.io/pypi/v/taskiq-ydb?style=for-the-badge&logo=pypi)](https://pypi.org/project/taskiq-ydb/)
[![Checks](https://img.shields.io/github/actions/workflow/status/danfimov/taskiq-ydb/code_check.yml?style=for-the-badge&logo=pytest&label=checks)](https://github.com/danfimov/taskiq-ydb)

Plugin for taskiq that adds a new result backend, broker and schedule source based on YDB.

## Installation

This project can be installed using pip/poetry/uv (choose your preferred package manager):

```bash
pip install taskiq-ydb
```

## Quick start

### Basic task processing

1. Define your broker with [asyncpg](https://github.com/MagicStack/asyncpg):

  ```python
  # broker_example.py
  import asyncio
  from ydb.aio.driver import DriverConfig
  from taskiq_ydb import YdbBroker, YdbResultBackend


  driver_config = DriverConfig(
      endpoint='grpc://localhost:2136',
      database='/local',
  )
  broker = YdbBroker(
      driver_config=driver_config,
  ).with_result_backend(
      YdbResultBackend(driver_config=driver_config),
  )


  @broker.task('solve_all_problems')
  async def best_task_ever() -> None:
      """Solve all problems in the world."""
      await asyncio.sleep(2)
      print('All problems are solved!')


  async def main() -> None:
      await broker.startup()
      task = await best_task_ever.kiq()
      print(await task.wait_result())
      await broker.shutdown()


  if __name__ == '__main__':
      asyncio.run(main())
  ```

2. Start a worker to process tasks (by default taskiq runs two instances of worker):

  ```bash
  taskiq worker broker_example:broker
  ```

3. Run `broker_example.py` file to send a task to the worker:

  ```bash
  python broker_example.py
  ```

Your experience with other drivers will be pretty similar. Just change the import statement and that's it.

### Task scheduling

1. Define your broker and schedule source:

  ```python
  # scheduler_example.py
  import asyncio

  from taskiq import TaskiqScheduler
  from ydb.aio.driver import DriverConfig

  from taskiq_ydb import YdbBroker, YdbScheduleSource


  driver_config = DriverConfig(
      endpoint='grpc://localhost:2136',
      database='/local',
  )
  broker = YdbBroker(driver_config=driver_config)
  scheduler = TaskiqScheduler(
      broker=broker,
      sources=[
          YdbScheduleSource(
              driver_config=driver_config,
              broker=broker,
          ),
      ],
  )


  @broker.task(
      task_name='solve_all_problems',
      schedule=[
          {
              'cron': '*/1 * * * *',  # type: str, either cron or time should be specified.
              'cron_offset': None,  # type: str | timedelta | None, can be omitted.
              'time': None,  # type: datetime | None, either cron or time should be specified.
              'args': [],  # type list[Any] | None, can be omitted.
              'kwargs': {},  # type: dict[str, Any] | None, can be omitted.
              'labels': {},  # type: dict[str, Any] | None, can be omitted.
          },
      ],
  )
  async def best_task_ever() -> None:
      """Solve all problems in the world."""
      await asyncio.sleep(2)
      print('All problems are solved!')
  ```

2. Start worker processes:

  ```bash
  taskiq worker scheduler_example:broker
  ```

3. Run scheduler process:

  ```bash
  taskiq scheduler scheduler_example:scheduler
  ```
