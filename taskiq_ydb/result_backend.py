import asyncio
import logging
import typing as tp

import ydb  # type: ignore[import-untyped]
import ydb.aio  # type: ignore[import-untyped]
from taskiq import AsyncResultBackend, TaskiqResult
from taskiq.abc.serializer import TaskiqSerializer
from taskiq.serializers import PickleSerializer

from taskiq_ydb.exceptions import DatabaseConnectionError, ResultIsMissingError


logger = logging.getLogger(__name__)
_ReturnType = tp.TypeVar('_ReturnType')


class YdbResultBackend(AsyncResultBackend[_ReturnType]):
    """Result backend for TaskIQ based on YDB."""

    def __init__(
        self,
        driver_config: ydb.aio.driver.DriverConfig,
        table_name: str = 'taskiq_results',
        serializer: TaskiqSerializer | None = None,
        pool_size: int = 5,
        connection_timeout: int = 5,
    ) -> None:
        """
        Construct new result backend.

        :param driver_config: YDB driver configuration.
        :param table_name: Table name for storing task results.
        :param serializer: Serializer for task results.
        :param pool_size: YDB session pool size.
        :param connection_timeout: Timeout for connection to database during startup.

        """
        self._driver = ydb.aio.Driver(driver_config=driver_config)
        self._table_name: tp.Final = table_name
        self._serializer: tp.Final = serializer or PickleSerializer()
        self._pool_size: tp.Final = pool_size
        self._pool: ydb.aio.SessionPool
        self._connection_timeout: tp.Final = connection_timeout

    async def startup(self) -> None:
        """
        Initialize the result backend.

        Construct new connection pool
        and create new table for results if not exists.
        """
        try:
            logger.debug('Waiting for YDB driver to be ready')
            await self._driver.wait(fail_fast=True, timeout=self._connection_timeout)
        except (ydb.issues.ConnectionLost, asyncio.exceptions.TimeoutError) as exception:
            raise DatabaseConnectionError from exception
        self._pool = ydb.aio.SessionPool(self._driver, size=self._pool_size)
        session = await self._pool.acquire()

        table_path = f'{self._driver._driver_config.database}/{self._table_name}'  # noqa: SLF001
        try:
            logger.debug('Checking if table %s exists', self._table_name)
            existing_table = await session.describe_table(table_path)
        except ydb.issues.SchemeError:
            existing_table = None
        if not existing_table:
            logger.debug('Table %s does not exist, creating...', self._table_name)
            await session.create_table(
                table_path,
                ydb.TableDescription()
                .with_column(ydb.Column('task_id', ydb.OptionalType(ydb.PrimitiveType.Utf8)))
                .with_column(ydb.Column('result', ydb.OptionalType(ydb.PrimitiveType.String)))
                .with_primary_key('task_id'),
            )
            logger.debug('Table created')
        else:
            logger.debug('Table %s already exists', self._table_name)

    async def shutdown(self) -> None:
        """Close the connection pool."""
        await asyncio.to_thread(self._driver.topic_client.close)
        if hasattr(self, '_pool'):
            await self._pool.stop(timeout=10)
        await self._driver.stop(timeout=10)

    async def set_result(
        self,
        task_id: tp.Any,  # noqa: ANN401
        result: TaskiqResult[_ReturnType],
    ) -> None:
        """
        Set result to the YDB table.

        Args:
            task_id (Any): ID of the task.
            result (TaskiqResult[_ReturnType]):  result of the task.

        """
        query = f"""
            DECLARE $taskId AS Utf8;
            DECLARE $resultString AS String;

            UPSERT INTO {self._table_name} (task_id, result)
            VALUES ($taskId, $resultString);
        """
        session = await self._pool.acquire()
        await session.transaction().execute(
            await session.prepare(query),
            {
                '$taskId': task_id,
                '$resultString': self._serializer.dumpb(result),
            },
            commit_tx=True,
        )
        await self._pool.release(session)

    async def is_result_ready(
        self,
        task_id: tp.Any,  # noqa: ANN401
    ) -> bool:
        """
        Return whether the result is ready.

        Args:
            task_id (Any): ID of the task.

        Returns:
            bool: True if the result is ready else False.

        """
        query = f"""
            DECLARE $taskId AS Utf8;

            SELECT task_id FROM {self._table_name}
            WHERE task_id = $taskId;
        """  # noqa: S608
        session = await self._pool.acquire()
        result_sets = await session.transaction().execute(
            await session.prepare(query),
            {
                '$taskId': task_id,
            },
            commit_tx=True,
        )
        await self._pool.release(session)
        return bool(result_sets[0].rows)

    async def get_result(
        self,
        task_id: tp.Any,  # noqa: ANN401
        with_logs: bool = False,  # noqa: FBT002, FBT001
    ) -> TaskiqResult[_ReturnType]:
        """
        Retrieve result from the task.

        :param task_id: task's id.
        :param with_logs: if True it will download task's logs.
        :raises ResultIsMissingError: if there is no result when trying to get it.
        :return: TaskiqResult.
        """
        query = f"""
            DECLARE $taskId AS Utf8;

            SELECT result FROM {self._table_name}
            WHERE task_id = $taskId;
        """  # noqa: S608
        session = await self._pool.acquire()
        result_sets = await session.transaction().execute(
            await session.prepare(query),
            {
                '$taskId': task_id,
            },
            commit_tx=True,
        )
        await self._pool.release(session)
        if not result_sets[0].rows:
            msg = f'No result found for task {task_id} in YDB'
            raise ResultIsMissingError(msg)
        taskiq_result: TaskiqResult[_ReturnType] = self._serializer.loadb(
            result_sets[0].rows[0].result,
        )
        if not with_logs:
            taskiq_result.log = None

        return taskiq_result
