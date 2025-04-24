import typing as tp

import pytest
import taskiq
import ydb  # type: ignore[import-untyped]
import ydb.aio  # type: ignore[import-untyped]

import taskiq_ydb


if tp.TYPE_CHECKING:
    import asyncio


@pytest.fixture(scope='session')
def event_loop() -> 'tp.Generator[asyncio.AbstractEventLoop, None]':
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    yield loop
    loop.close()



@pytest.fixture
def driver_config() -> ydb.aio.driver.DriverConfig:
    return ydb.aio.driver.DriverConfig(
        endpoint='grpc://localhost:2136',
        database='/local',
    )


@pytest.fixture
async def result_backend(
    driver_config: ydb.aio.driver.DriverConfig,
) -> tp.AsyncGenerator[taskiq_ydb.YdbResultBackend, None]:
    backend: taskiq_ydb.YdbResultBackend = taskiq_ydb.YdbResultBackend(
        driver_config=driver_config,
    )
    try:
        yield backend
    finally:
        await backend.shutdown()


@pytest.fixture
async def ydb_broker(
    result_backend: taskiq_ydb.YdbResultBackend,
    driver_config: ydb.aio.driver.DriverConfig,
) -> tp.AsyncGenerator[taskiq_ydb.YdbBroker, None]:
    broker = taskiq_ydb.YdbBroker(
        driver_config=driver_config,
    ).with_result_backend(result_backend)
    try:
        yield broker
    finally:
        await broker.shutdown()


@pytest.fixture
async def ydb_driver(driver_config: ydb.aio.driver.DriverConfig) -> tp.AsyncGenerator[ydb.aio.Driver, None]:
    driver = ydb.aio.Driver(
        driver_config=driver_config,
    )
    await driver.wait(fail_fast=True)
    try:
        yield driver
    finally:
        driver.topic_client.close()
        await driver.stop()


@pytest.fixture
async def ydb_pool(ydb_driver: ydb.aio.Driver) -> tp.AsyncGenerator[ydb.aio.SessionPool, None]:
    pool = ydb.aio.SessionPool(ydb_driver, size=10)
    try:
        yield pool
    finally:
        await pool.stop()


@pytest.fixture
async def ydb_session(ydb_pool: ydb.aio.SessionPool) -> tp.AsyncGenerator[ydb.Session, None]:
    session = await ydb_pool.acquire()
    try:
        yield session
    finally:
        await ydb_pool.release(session)


@pytest.fixture
def default_taskiq_result() -> taskiq.TaskiqResult[tp.Any]:
    return taskiq.TaskiqResult(
        is_err=False,
        log=None,
        return_value='Best test ever.',
        execution_time=0.1,
    )
