from __future__ import annotations

import asyncio
import logging
import typing as tp
import uuid

import ydb  # type: ignore[import-untyped]
import ydb.aio  # type: ignore[import-untyped]
from taskiq import AsyncResultBackend, TaskiqResult
from taskiq.serializers import PickleSerializer

from taskiq_ydb.exceptions import DatabaseConnectionError, ResultIsMissingError


if tp.TYPE_CHECKING:
    from taskiq.abc.serializer import TaskiqSerializer


logger = logging.getLogger(__name__)
_ReturnType = tp.TypeVar('_ReturnType')


class YdbResultBackend(AsyncResultBackend[_ReturnType]):
    """Result backend for TaskIQ based on YDB."""

    def __init__(  # noqa: PLR0913
        self,
        driver_config: ydb.aio.driver.DriverConfig,
        table_name: str = 'taskiq_results',
        table_primary_key_type: tp.Literal[ydb.PrimitiveType.UUID, ydb.PrimitiveType.Utf8] = ydb.PrimitiveType.UUID,  # type: ignore[valid-type]
        serializer: TaskiqSerializer | None = None,
        pool_size: int = 5,
        connection_timeout: int = 5,
    ) -> None:
        """
        Construct new result backend.

        Args:
            driver_config: YDB driver configuration.
            table_name: Table name for storing task results.
            table_primary_key_type: Type of primary key in table.
            serializer: Serializer for task results.
            pool_size: YDB session pool size.
            connection_timeout: Timeout for connection to database during startup.
        """
        self._driver = ydb.aio.Driver(driver_config=driver_config)
        self._table_name: tp.Final = table_name
        self._table_primary_key_type: tp.Final = table_primary_key_type
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
                .with_column(ydb.Column('task_id', self._table_primary_key_type))
                .with_column(ydb.Column('result', ydb.OptionalType(ydb.PrimitiveType.String)))
                .with_primary_key('task_id'),
            )
            logger.debug('Table %s created', self._table_name)
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
        task_id: str,
        result: TaskiqResult[_ReturnType],
    ) -> None:
        """
        Set result to the YDB table.

        Args:
            task_id: ID of the task.
            result: result of the task
        """
        task_id_in_ydb = uuid.UUID(task_id) if self._table_primary_key_type == ydb.PrimitiveType.UUID else task_id
        query = f"""
            DECLARE $taskId AS {self._table_primary_key_type};
            DECLARE $resultString AS String;

            UPSERT INTO {self._table_name} (task_id, result)
            VALUES ($taskId, $resultString);
        """
        session = await self._pool.acquire()
        await session.transaction().execute(
            await session.prepare(query),
            {
                '$taskId': task_id_in_ydb,
                '$resultString': self._serializer.dumpb(result),
            },
            commit_tx=True,
        )
        await self._pool.release(session)

    async def is_result_ready(
        self,
        task_id: str,
    ) -> bool:
        """
        Return whether the result is ready.

        Args:
            task_id: ID of the task.
        """
        task_id_in_ydb = uuid.UUID(task_id) if self._table_primary_key_type == ydb.PrimitiveType.UUID else task_id
        query = f"""
            DECLARE $taskId AS {self._table_primary_key_type};

            SELECT task_id FROM {self._table_name}
            WHERE task_id = $taskId;
        """  # noqa: S608
        session = await self._pool.acquire()
        result_sets = await session.transaction().execute(
            await session.prepare(query),
            {
                '$taskId': task_id_in_ydb,
            },
            commit_tx=True,
        )
        await self._pool.release(session)
        return bool(result_sets[0].rows)

    async def get_result(
        self,
        task_id: str,
        with_logs: bool = False,  # noqa: FBT002, FBT001
    ) -> TaskiqResult[_ReturnType]:
        """
        Retrieve result from the task.

        Args:
            task_id: task's id.
            with_logs: if True it will download task's logs.

        Raises:
            ResultIsMissingError: if there is no result when trying to get it.

        Returns:
            TaskiqResult.
        """
        task_id_in_ydb = uuid.UUID(task_id) if self._table_primary_key_type == ydb.PrimitiveType.UUID else task_id
        query = f"""
            DECLARE $taskId AS {self._table_primary_key_type};

            SELECT result FROM {self._table_name}
            WHERE task_id = $taskId;
        """  # noqa: S608
        session = await self._pool.acquire()
        result_sets = await session.transaction().execute(
            await session.prepare(query),
            {
                '$taskId': task_id_in_ydb,
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
