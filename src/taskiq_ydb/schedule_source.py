from __future__ import annotations

import asyncio
import logging
import typing as tp
import uuid

import ydb  # type: ignore[import-untyped]
import ydb.aio  # type: ignore[import-untyped]
from pydantic import ValidationError
from taskiq import ScheduleSource
from taskiq.scheduler.scheduled_task import ScheduledTask
from taskiq.serializers import PickleSerializer

from taskiq_ydb.exceptions import DatabaseConnectionError


if tp.TYPE_CHECKING:
    from taskiq.abc.broker import AsyncBroker


logger = logging.getLogger(__name__)


class YdbScheduleSource(ScheduleSource):
    """Schedule source that uses YDB to store schedules in YDB database."""

    def __init__(
        self,
        broker: AsyncBroker,
        driver_config: ydb.aio.driver.DriverConfig,
        table_name: str = 'taskiq_schedules',
        pool_size: int = 5,
        connection_timeout: int = 5,
    ) -> None:
        """
        Construct new schedule source.

        Args:
            broker: The TaskIQ broker instance to use for finding and managing tasks.
            driver_config: YDB driver configuration.
            table_name: Table name for storing task results.
            pool_size: YDB session pool size.
            connection_timeout: Timeout for connection to database during startup.
        """
        self._broker: tp.Final = broker
        self._driver = ydb.aio.Driver(driver_config=driver_config)
        self._table_name: tp.Final = table_name
        self._pool_size: tp.Final = pool_size
        self._pool: ydb.aio.SessionPool
        self._connection_timeout: tp.Final = connection_timeout
        self._serializer: tp.Final = PickleSerializer()


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
                .with_column(ydb.Column('id', ydb.PrimitiveType.UUID))
                .with_column(ydb.Column('task_name', ydb.PrimitiveType.Utf8))
                .with_column(ydb.Column('schedule', ydb.PrimitiveType.String))
                .with_primary_key('id'),
            )
            logger.debug('Table %s created', self._table_name)
        else:
            logger.debug('Table %s already exists', self._table_name)

        # Load existing schedules from labels in tasks
        schedule_tasks = self._extract_scheduled_tasks_from_broker()
        for task in await self.get_schedules():
            await self.delete_schedule(task.schedule_id)
        for schedule_task in schedule_tasks:
            await self.add_schedule(schedule_task)

    async def shutdown(self) -> None:
        """Close the connection pool."""
        await asyncio.to_thread(self._driver.topic_client.close)
        if hasattr(self, '_pool'):
            await self._pool.stop(timeout=10)
        await self._driver.stop(timeout=10)

    def _extract_scheduled_tasks_from_broker(self) -> list[ScheduledTask]:
        """
        Extract schedules from tasks that were registered in broker.

        Returns:
            A list of ScheduledTask instances extracted from the task's labels.
        """
        scheduled_tasks_for_creation: list[ScheduledTask] = []
        for task_name, task in self._broker.get_all_tasks().items():
            if 'schedule' not in task.labels:
                logger.debug('Task %s has no schedule, skipping', task_name)
                continue
            if not isinstance(task.labels['schedule'], list):
                logger.warning(
                    'Schedule for task %s is not a list, skipping',
                    task_name,
                )
                continue
            for schedule in task.labels['schedule']:
                try:
                    new_schedule = ScheduledTask.model_validate(
                        {
                            'task_name': task_name,
                            'labels': schedule.get('labels', {}),
                            'args': schedule.get('args', []),
                            'kwargs': schedule.get('kwargs', {}),
                            'schedule_id': str(uuid.uuid4()),
                            'cron': schedule.get('cron', None),
                            'cron_offset': schedule.get('cron_offset', None),
                            'time': schedule.get('time', None),
                        },
                    )
                    scheduled_tasks_for_creation.append(new_schedule)
                except ValidationError:  # noqa: PERF203
                    logger.exception(
                        'Schedule for task %s is not valid, skipping',
                        task_name,
                    )
                    continue
        return scheduled_tasks_for_creation

    async def get_schedules(self) -> list[ScheduledTask]:
        """Get list of taskiq schedules."""
        query = f"""
        SELECT schedule FROM {self._table_name};
        """   # noqa: S608
        session = await self._pool.acquire()
        result_sets = await session.transaction().execute(
            await session.prepare(query),
            commit_tx=True,
        )
        await self._pool.release(session)
        scheduled_tasks = []
        for result_set in result_sets:
            rows = result_set.rows
            for row in rows:
                scheduled_tasks.append(  # noqa: PERF401
                    self._serializer.loadb(row.schedule),
                )
        return scheduled_tasks

    async def add_schedule(
        self,
        schedule: ScheduledTask,
    ) -> None:
        """
        Add a new schedule.

        This function is used to add new schedules.
        It's a convenient helper for people who want to add new schedules
        for the current source.

        Args:
            schedule: schedule to add.
        """
        schedule_id = uuid.UUID(schedule.schedule_id)
        query = f"""
        DECLARE $id AS Uuid;
        DECLARE $task_name AS Utf8;
        DECLARE $schedule AS String;

        UPSERT INTO {self._table_name} (id, task_name, schedule)
        VALUES ($id, $task_name, $schedule);
        """
        session = await self._pool.acquire()
        await session.transaction().execute(
            await session.prepare(query),
            {
                '$id': schedule_id,
                '$task_name': schedule.task_name,
                '$schedule': self._serializer.dumpb(schedule),
            },
            commit_tx=True,
        )
        await self._pool.release(session)

    async def delete_schedule(self, schedule_id: str) -> None:
        """
        Method to delete schedule by id.

        This is useful for schedule cancelation.

        Args:
            schedule_id: id of schedule to delete.
        """
        schedule_id_uuid = uuid.UUID(schedule_id)
        query = f"""
        DECLARE $id AS Uuid;

        DELETE FROM {self._table_name}
        WHERE id = $id;
        """  # noqa: S608
        session = await self._pool.acquire()
        await session.transaction().execute(
            await session.prepare(query),
            {
                '$id': schedule_id_uuid,
            },
            commit_tx=True,
        )
        await self._pool.release(session)

    async def post_send(
        self,
        task: ScheduledTask,
    ) -> None:
        """
        Delete schedule if it was one-time task.

        Args:
            task: task that just have sent
        """
        if task.time:
            await self.delete_schedule(task.schedule_id)
