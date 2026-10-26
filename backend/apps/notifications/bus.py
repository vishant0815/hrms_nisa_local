"""The message bus. Messages are just notification ids; the data stays in the database.

  kafka:  real Kafka (production and docker-compose)
  memory: an in-process list, for tests and for trying things without Kafka

Delivery is at-least-once, so the consumer ignores an id it has already handled.
"""
import logging

from django.conf import settings

log = logging.getLogger(__name__)


class MemoryBus:
    messages = []

    @classmethod
    def reset(cls):
        cls.messages = []

    def publish(self, ids):
        type(self).messages.extend(ids)
        return list(ids)

    def consume(self, handler, once=False):
        while type(self).messages:
            handler(type(self).messages.pop(0))


class KafkaBus:
    def __init__(self, servers=None, topic=None, group=None):
        self.servers = servers or settings.KAFKA_BOOTSTRAP_SERVERS
        self.topic = topic or settings.NOTIFICATION_TOPIC
        self.group = group or settings.NOTIFICATION_CONSUMER_GROUP

    def publish(self, ids):
        """Put ids on the topic. Returns only the ids Kafka confirmed it received."""
        from confluent_kafka import Producer

        confirmed = []

        def done(error, message):
            if error is None:
                confirmed.append(message.key().decode())
            else:
                log.warning("Kafka did not accept a notification: %s", error)

        producer = Producer({"bootstrap.servers": self.servers, "acks": "all", "enable.idempotence": True})
        for notification_id in ids:
            producer.produce(self.topic, key=notification_id, value=notification_id, callback=done)
        producer.flush(15)
        return confirmed

    def consume(self, handler, once=False, idle_seconds=10):
        """Hand each id to `handler`, then commit it. A crash before the commit means it is redelivered.

        With once=True, stop after `idle_seconds` with nothing to read. It has to be long enough for the consumer
        to join its group, which takes a few seconds on a real broker; the first poll often comes back empty.
        """
        from confluent_kafka import Consumer
        import time

        consumer = Consumer({
            "bootstrap.servers": self.servers, "group.id": self.group,
            "enable.auto.commit": False, "auto.offset.reset": "earliest",
        })
        consumer.subscribe([self.topic])
        last_seen = time.monotonic()
        try:
            while True:
                message = consumer.poll(1.0)
                if message is None:
                    if once and time.monotonic() - last_seen >= idle_seconds:
                        return
                    continue
                last_seen = time.monotonic()
                if message.error():
                    log.warning("Kafka error: %s", message.error())
                    continue
                try:
                    handler(message.value().decode())
                except Exception:  # the row stays pending and the relay will publish it again
                    log.exception("Could not handle a notification message")
                consumer.commit(message=message, asynchronous=False)
        finally:
            consumer.close()


def get_bus():
    return MemoryBus() if settings.NOTIFICATIONS_BUS == "memory" else KafkaBus()
