"""
How to run:

    1) Run worker in one terminal:
        uv run taskiq worker examples.example_with_broker:broker --workers 1

    2) Run this script in another terminal:
        uv run python -m examples.example_with_broker
"""

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
