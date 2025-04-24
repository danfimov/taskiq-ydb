import typing as tp
import uuid

import pytest
import taskiq
import taskiq_redis
import ydb  # type: ignore[import-untyped]
import ydb.aio  # type: ignore[import-untyped]

import taskiq_ydb


class TestResultBackend:
    async def test_when_database_is_unreachable__then_raise_database_connection_error(self):
        invalid_driver_config = ydb.aio.driver.DriverConfig(
            endpoint='invalid_endpoint:2135',
            database='/local',
        )
        result_backend = taskiq_ydb.YdbResultBackend(
            driver_config=invalid_driver_config,
            connection_timeout=0,  # for faster test
        )
        taskiq_redis.ListQueueBroker(
            url='redis://localhost:6379',
        ).with_result_backend(result_backend)

        with pytest.raises(taskiq_ydb.exceptions.DatabaseConnectionError):
            await result_backend.startup()

    @pytest.mark.parametrize(
        'is_table_already_exists',
        [
            pytest.param(True, id='table_already_exists'),
            pytest.param(False, id='table_not_exists'),
        ],
    )
    async def test_when_backend_started__then_table_for_result_created(
        self,
        is_table_already_exists: bool,
        redis_broker: taskiq_redis.ListQueueBroker,
        ydb_session: ydb.Session,
    ):
        # given
        default_table_path = '/local/taskiq_results'
        if is_table_already_exists:
            await ydb_session.create_table(
                default_table_path,
                ydb.TableDescription()
                .with_column(ydb.Column('task_id', ydb.OptionalType(ydb.PrimitiveType.Utf8)))
                .with_column(ydb.Column('result', ydb.OptionalType(ydb.PrimitiveType.String)))
                .with_primary_key('task_id'),
            )
        else:
            await ydb_session.drop_table(default_table_path)

        # when
        await redis_broker.startup()

        # then
        assert await ydb_session.describe_table(default_table_path)

    @pytest.mark.parametrize(
        'is_result_saved',
        [
            pytest.param(True, id='result_saved'),
            pytest.param(False, id='result_not_saved'),
        ],
    )
    async def test_when_result_saved__then_result_should_be_ready(
        self,
        is_result_saved: bool,
        result_backend: taskiq_ydb.YdbResultBackend,
        default_taskiq_result: taskiq.TaskiqResult[tp.Any],
    ):
        # given
        task_id = uuid.uuid4().hex
        await result_backend.startup()
        if is_result_saved:
            await result_backend.set_result(task_id, default_taskiq_result)
        # when
        ready = await result_backend.is_result_ready(task_id)
        # then
        assert ready == is_result_saved

    async def test_when_result_saved__then_get_result_should_return_result(
        self,
        result_backend: taskiq_ydb.YdbResultBackend,
        default_taskiq_result: taskiq.TaskiqResult[tp.Any],
    ):
        # given
        task_id = uuid.uuid4().hex
        await result_backend.startup()
        await result_backend.set_result(task_id, default_taskiq_result)
        # when
        result = await result_backend.get_result(task_id)
        # then
        assert result == default_taskiq_result

    async def test_when_result_not_saved__then_get_result_should_raise_error(
        self,
        result_backend: taskiq_ydb.YdbResultBackend,
    ):
        # given
        task_id = uuid.uuid4().hex
        await result_backend.startup()
        # when
        with pytest.raises(taskiq_ydb.exceptions.ResultIsMissingError):
            await result_backend.get_result(task_id)
