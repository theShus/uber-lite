from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Point(_message.Message):
    __slots__ = ("lat", "lon")
    LAT_FIELD_NUMBER: _ClassVar[int]
    LON_FIELD_NUMBER: _ClassVar[int]
    lat: float
    lon: float
    def __init__(self, lat: _Optional[float] = ..., lon: _Optional[float] = ...) -> None: ...

class QuoteRequest(_message.Message):
    __slots__ = ("pickup", "dropoff", "rider_id")
    PICKUP_FIELD_NUMBER: _ClassVar[int]
    DROPOFF_FIELD_NUMBER: _ClassVar[int]
    RIDER_ID_FIELD_NUMBER: _ClassVar[int]
    pickup: Point
    dropoff: Point
    rider_id: str
    def __init__(self, pickup: _Optional[_Union[Point, _Mapping]] = ..., dropoff: _Optional[_Union[Point, _Mapping]] = ..., rider_id: _Optional[str] = ...) -> None: ...

class QuoteReply(_message.Message):
    __slots__ = ("quote_id", "amount_cents", "currency", "surge", "distance_km")
    QUOTE_ID_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_CENTS_FIELD_NUMBER: _ClassVar[int]
    CURRENCY_FIELD_NUMBER: _ClassVar[int]
    SURGE_FIELD_NUMBER: _ClassVar[int]
    DISTANCE_KM_FIELD_NUMBER: _ClassVar[int]
    quote_id: str
    amount_cents: int
    currency: str
    surge: float
    distance_km: float
    def __init__(self, quote_id: _Optional[str] = ..., amount_cents: _Optional[int] = ..., currency: _Optional[str] = ..., surge: _Optional[float] = ..., distance_km: _Optional[float] = ...) -> None: ...
