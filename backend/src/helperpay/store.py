"""Persistence for the system of record.

One household, so one partition: every item has pk="HH" and an sk prefixed by
its type. `MemoryStore` backs tests and local dev; `DynamoStore` backs the
deployed app. Both enforce the same write conditions.
"""

from __future__ import annotations

import json
import threading
from decimal import Decimal
from typing import Any, Protocol

PK = "HH"


class ConditionFailed(Exception):
    """A conditional write was refused (e.g. the bill is already approved)."""


class Store(Protocol):
    def get(self, sk: str) -> dict | None: ...
    def query(self, prefix: str) -> list[dict]: ...
    def put(self, sk: str, item: dict, *, if_absent: bool = False) -> None: ...
    def put_unless(self, sk: str, item: dict, *, field: str, value: str) -> None:
        """Replace the item unless its current `field` equals `value`."""
    def update_if(self, sk: str, changes: dict, *, field: str, expected: str) -> None:
        """Set `changes` only if the item's current `field` equals `expected`."""


def _plain(item: dict) -> dict:
    """JSON round trip so stored items never alias caller objects."""
    return json.loads(json.dumps(item, default=str))


class MemoryStore:
    def __init__(self) -> None:
        self._items: dict[str, dict] = {}
        self._lock = threading.Lock()

    def get(self, sk: str) -> dict | None:
        item = self._items.get(sk)
        return _plain(item) if item else None

    def query(self, prefix: str) -> list[dict]:
        return [_plain(v) for k, v in sorted(self._items.items()) if k.startswith(prefix)]

    def put(self, sk: str, item: dict, *, if_absent: bool = False) -> None:
        with self._lock:
            if if_absent and sk in self._items:
                raise ConditionFailed(sk)
            self._items[sk] = _plain({**item, "sk": sk})

    def put_unless(self, sk: str, item: dict, *, field: str, value: str) -> None:
        with self._lock:
            cur = self._items.get(sk)
            if cur and cur.get(field) == value:
                raise ConditionFailed(sk)
            self._items[sk] = _plain({**item, "sk": sk})

    def update_if(self, sk: str, changes: dict, *, field: str, expected: str) -> None:
        with self._lock:
            cur = self._items.get(sk)
            if not cur or cur.get(field) != expected:
                raise ConditionFailed(sk)
            self._items[sk] = _plain({**cur, **changes})


class DynamoStore:
    """Items are stored as a JSON document in `doc` so Decimals and nesting survive."""

    def __init__(self, table_name: str, client: Any = None) -> None:
        import boto3

        self._ddb = client or boto3.client("dynamodb")
        self._table = table_name

    @staticmethod
    def _row(sk: str, item: dict) -> dict:
        row = {"pk": {"S": PK}, "sk": {"S": sk}, "doc": {"S": json.dumps({**item, "sk": sk}, default=str)}}
        for k in ("status",):
            if k in item and item[k] is not None:
                row[k] = {"S": str(item[k])}
        return row

    def get(self, sk: str) -> dict | None:
        r = self._ddb.get_item(TableName=self._table, Key={"pk": {"S": PK}, "sk": {"S": sk}}, ConsistentRead=True)
        return json.loads(r["Item"]["doc"]["S"]) if "Item" in r else None

    def query(self, prefix: str) -> list[dict]:
        out, kwargs = [], {}
        while True:
            r = self._ddb.query(
                TableName=self._table,
                KeyConditionExpression="pk = :pk AND begins_with(sk, :p)",
                ExpressionAttributeValues={":pk": {"S": PK}, ":p": {"S": prefix}},
                ConsistentRead=True,
                **kwargs,
            )
            out += [json.loads(i["doc"]["S"]) for i in r["Items"]]
            if "LastEvaluatedKey" not in r:
                return out
            kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]

    def _conditional(self, fn, sk: str, **kwargs) -> None:
        try:
            fn(TableName=self._table, **kwargs)
        except self._ddb.exceptions.ConditionalCheckFailedException as e:
            raise ConditionFailed(sk) from e

    def put(self, sk: str, item: dict, *, if_absent: bool = False) -> None:
        kwargs: dict = {"Item": self._row(sk, item)}
        if if_absent:
            kwargs["ConditionExpression"] = "attribute_not_exists(sk)"
        self._conditional(self._ddb.put_item, sk, **kwargs)

    def put_unless(self, sk: str, item: dict, *, field: str, value: str) -> None:
        self._conditional(
            self._ddb.put_item,
            sk,
            Item=self._row(sk, item),
            ConditionExpression="attribute_not_exists(sk) OR #f <> :v",
            ExpressionAttributeNames={"#f": field},
            ExpressionAttributeValues={":v": {"S": value}},
        )

    def update_if(self, sk: str, changes: dict, *, field: str, expected: str) -> None:
        # Read-modify-write guarded by the condition; the doc is rewritten whole.
        cur = self.get(sk)
        if not cur or cur.get(field) != expected:
            raise ConditionFailed(sk)
        self._conditional(
            self._ddb.put_item,
            sk,
            Item=self._row(sk, {**cur, **changes}),
            ConditionExpression="#f = :v",
            ExpressionAttributeNames={"#f": field},
            ExpressionAttributeValues={":v": {"S": expected}},
        )


def to_decimal(x: Any) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))
