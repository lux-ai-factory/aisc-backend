"""
Schemas for connections and adapters (CON-01).
"""
import uuid

from ninja import Schema


class ConnectionOut(Schema):
    pid: uuid.UUID
    name: str
    component_type: str
    adapter: str
    adapter_version: str = ""
    helper_role: str = ""
    endpoint: str = ""
    key_set: bool = False


class ConnectionListOut(Schema):
    adapters: list[str]
    connections: list[ConnectionOut]


class ConnectionUpdateIn(Schema):
    adapter: str
    adapter_version: str = ""
    helper_role: str = ""
