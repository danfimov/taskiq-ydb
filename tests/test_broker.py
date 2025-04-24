import asyncio
import uuid

import pytest
import taskiq
import ydb  # type: ignore[import-untyped]
import ydb.aio  # type: ignore[import-untyped]

import taskiq_ydb


async def get_message(
    broker: taskiq.AsyncBroker,
) -> bytes | taskiq.AckableMessage:
    """
    Get a message from the broker.

    :param broker: async message broker.
    :return: first message from listen method.
    """
    async for message in broker.listen():
        return message
    return b''


@pytest.fixture
def valid_broker_message() -> taskiq.BrokerMessage:
    """
    Generate valid broker message for tests.

    :returns: broker message.
    """
    return taskiq.BrokerMessage(
        task_id=uuid.uuid4().hex,
        task_name=uuid.uuid4().hex,
        message=b'my_msg',
        labels={
            'label1': 'val1',
        },
    )


class TestBroker:
    async def test_when_database_is_unreachable__then_raise_database_connection_error(self):
        # given
        invalid_driver_config = ydb.aio.driver.DriverConfig(
            endpoint='invalid_endpoint:2135',
            database='/local',
        )
        broker = taskiq_ydb.YdbBroker(
            driver_config=invalid_driver_config,
            connection_timeout=0,  # for faster test
        )
        # when & then
        with pytest.raises(taskiq_ydb.exceptions.DatabaseConnectionError):
            await broker.startup()

    @pytest.mark.parametrize(
        'is_topic_already_exists',
        [
            pytest.param(True, id='topic_already_exists'),
            pytest.param(False, id='topic_not_exists'),
        ],
    )
    async def test_when_broker_startup__when_topic_created(
        self,
        is_topic_already_exists: bool,
        ydb_broker: taskiq_ydb.YdbBroker,
        ydb_driver: ydb.aio.driver.Driver,
    ):
        # given
        default_topic_path = 'taskiq-tasks'
        if is_topic_already_exists:
            await ydb_driver.topic_client.create_topic(default_topic_path)
        else:
            await ydb_driver.topic_client.drop_topic(default_topic_path)

        # when
        await ydb_broker.startup()

        # then
        assert await ydb_driver.topic_client.describe_topic(default_topic_path)

    async def test_when_broker_deliver_message__then_worker_receive_message(
        self,
        ydb_broker: taskiq_ydb.YdbBroker,
        valid_broker_message: taskiq.BrokerMessage,
    ):
        # given
        await ydb_broker.startup()
        worker_task = asyncio.create_task(get_message(ydb_broker))
        await asyncio.sleep(0.2)

        # when
        await ydb_broker.kick(valid_broker_message)
        await asyncio.sleep(0.2)

        # then
        message = worker_task.result()
        assert message == valid_broker_message.message

    async def test_when_two_workers_are_listening__then_one_worker_receive_message(
        self,
        ydb_broker: taskiq_ydb.YdbBroker,
        valid_broker_message: taskiq.BrokerMessage,
    ):
        # given
        await ydb_broker.startup()
        worker1_task = asyncio.create_task(get_message(ydb_broker))
        worker2_task = asyncio.create_task(get_message(ydb_broker))
        await asyncio.sleep(0.3)

        # when
        await ydb_broker.kick(valid_broker_message)
        await asyncio.sleep(0.3)

        recieved = 0
        for task in [worker1_task, worker2_task]:
            try:
                task.result()
                recieved += 1
            except asyncio.exceptions.InvalidStateError:
                pass

        # then
        assert recieved == 1

        worker1_task.cancel()
        worker2_task.cancel()
