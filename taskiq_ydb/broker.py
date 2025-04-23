import asyncio
import json
import logging
import typing as tp

import ydb  # type: ignore[import-untyped]
import ydb.aio  # type: ignore[import-untyped]
from taskiq import AckableMessage, AsyncBroker, BrokerMessage

from taskiq_ydb.exceptions import DatabaseConnectionError


logger = logging.getLogger(__name__)


class YdbBroker(AsyncBroker):
    """Broker for TaskIQ based on YDB."""

    def __init__(
        self,
        driver_config: ydb.aio.driver.DriverConfig,
        topic_path: str = "taskiq/tasks",
    ) -> None:
        """
        Construct new broker.

        :param driver_config: YDB driver configuration.
        :param topic_path: Path to the topic where tasks will be stored.
        """
        super().__init__()
        self._driver = ydb.aio.Driver(driver_config=driver_config)
        self._topic_path = topic_path
        self._consumer = "taskiq_consumer"

    async def startup(self) -> None:
        """
        Initialize the broker.

        Wait for YDB driver to be ready
        and create new topic for tasks if not exists.
        """
        try:
            logger.debug("Waiting for YDB driver to be ready")
            await self._driver.wait(fail_fast=True, timeout=10)
        except (ydb.issues.ConnectionLost, TimeoutError) as exception:
            raise DatabaseConnectionError from exception

        try:
            await self._driver.topic_client.describe_topic(self._topic_path)
        except ydb.issues.SchemeError:
            await self._driver.topic_client.create_topic(self._topic_path)

        return await super().startup()

    async def shutdown(self) -> None:
        """Close the topic client and stop the driver."""
        self._driver.topic_client.close()
        await self._driver.stop(timeout=10)
        return await super().shutdown()

    async def kick(self, message: BrokerMessage) -> None:
        """Send message to the topic."""
        async with self._driver.topic_client.writer(self._topic_path) as writer:
            message_for_topic = ydb.TopicWriterMessage(
                data=message.message,
                metadata_items={
                    "task_id": message.task_id,
                    "task_name": message.task_name,
                    "labels": json.dumps(message.labels),
                },
            )
            await writer.write(message_for_topic)

    async def listen(self) -> tp.AsyncGenerator[bytes | AckableMessage, None]:
        """Listen for messages from the topic."""
        async with self._driver.topic_client.reader(self._topic_path, consumer=self._consumer) as reader:
            while True:
                try:
                    message_from_topic = await asyncio.wait_for(reader.receive_message(), 5)
                    reader.commit(message_from_topic)
                    logger.debug("Received task with id: %s", message_from_topic.metadata_items["task_id"])
                    yield message_from_topic.data
                except TimeoutError:
                    pass
