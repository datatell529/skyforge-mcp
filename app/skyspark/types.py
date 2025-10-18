"""
SkySpark/Haystack and Phable Data Types Module

This module documents both SkySpark/Haystack data types (kinds) and their Phable Python representations.
It's important to understand the distinction between these two:

1. SkySpark/Haystack Types (kinds):
   - These are the native types used within SkySpark/Haystack
   - Used in Axon expressions, queries, and API responses
   - Defined by Project Haystack specification
   - Example: @foo-bar (Ref), 45°F (Number), ^elec-meter (Symbol)

2. Phable Python Types:
   - These are the Python representations of Haystack types
   - Used when working with Phable library responses
   - Implemented in phable.kinds module
   - Example: phable.Ref("foo-bar"), phable.Number(45, "°F"), phable.Symbol("elec-meter")

Usage Guidelines:
- When writing Axon expressions or making API calls: Use Haystack types
- When handling Phable responses: Use Phable Python types
- When validating input: Use Haystack type validation
- When processing data: Use Phable type methods (e.g., Grid.to_pandas())

Mapping of Haystack Types to Phable Types:

Special Singleton Types:
Haystack          | Phable Python
------------------|---------------
Marker (M)        | phable.Marker
NA                | phable.NA
Remove (R)        | phable.Remove

Scalar Atomic Types:
Haystack          | Phable Python
------------------|---------------
Bool              | bool
Number            | phable.Number
Str               | str
Uri               | phable.Uri
Ref               | phable.Ref
Symbol            | phable.Symbol
Date              | datetime.date
Time              | datetime.time
DateTime          | datetime.datetime (timezone aware)
Coord             | phable.Coord
XStr              | phable.XStr

Collection Types:
Haystack          | Phable Python
------------------|---------------
List              | list
Dict              | dict
Grid              | phable.Grid

Phable-Specific Types (not in Haystack):
- phable.DateRange
- phable.DateTimeRange

Important Notes:
1. DateTime in Phable must be timezone aware (using ZoneInfo)
2. Grid objects have methods for data conversion:
   - to_pandas(): Convert to Pandas DataFrame
   - to_polars(): Convert to Polars DataFrame
   - to_pandas_all(): Get (meta, data) tuple
   - to_polars_all(): Get (meta, data) tuple
3. Number objects can have units but Phable doesn't validate them
4. Grid metadata is preserved in both meta and cols attributes
"""

import re
from datetime import date, datetime, time
from enum import Enum
from typing import Any, Optional

from phable import NA, Coord, DateRange, DateTimeRange, Grid, Marker, Number, Ref, Remove, Symbol, Uri, XStr


# ============================================================================
# Extended Phable Classes with Axon Serialization
# ============================================================================


class MarkerExt(Marker):
    """Extended Marker with Axon serialization methods"""

    __instance = None

    def __new__(cls):
        if MarkerExt.__instance is None:
            MarkerExt.__instance = object.__new__(cls)
        return MarkerExt.__instance

    def toStr(self) -> str:
        """Return string representation matching SkySpark's toStr()"""
        return "marker"

    def toAxon(self) -> str:
        """Return Axon expression: marker()"""
        return "marker()"


class NAExt(NA):
    """Extended NA with Axon serialization methods"""

    __instance = None

    def __new__(cls):
        if NAExt.__instance is None:
            NAExt.__instance = object.__new__(cls)
        return NAExt.__instance

    def toStr(self) -> str:
        """Return string representation matching SkySpark's toStr()"""
        return "NA"

    def toAxon(self) -> str:
        """Return Axon expression: na()"""
        return "na()"


class RemoveExt(Remove):
    """Extended Remove with Axon serialization methods"""

    __instance = None

    def __new__(cls):
        if RemoveExt.__instance is None:
            RemoveExt.__instance = object.__new__(cls)
        return RemoveExt.__instance

    def toStr(self) -> str:
        """Return string representation matching SkySpark's toStr()"""
        return "remove"

    def toAxon(self) -> str:
        """Return Axon expression: removeMarker()"""
        return "removeMarker()"


class NumberExt(Number):
    """Extended Number with Axon serialization methods"""

    def toStr(self) -> str:
        """Return string representation for LLM/display usage"""
        if self.unit is not None:
            return f"{self.val}{self.unit}"
        else:
            return f"{self.val}"

    def toAxon(self) -> str:
        """Return Axon expression: number or number with unit"""
        if self.unit is not None:
            return f"{self.val}{self.unit}"
        else:
            return f"{self.val}"


class UriExt(Uri):
    """Extended Uri with Axon serialization methods"""

    def toStr(self) -> str:
        """Return string representation matching SkySpark's toStr()"""
        return self.val

    def toAxon(self) -> str:
        """Return Axon expression: `uri`"""
        return f"`{self.val}`"


class RefExt(Ref):
    """Extended Ref with Axon serialization methods"""

    def toStr(self) -> str:
        """Return string representation matching SkySpark's toStr()"""
        return self.val  # SkySpark returns just the ID without @

    def toAxon(self) -> str:
        """Return Axon expression: @refId"""
        return f"@{self.val}"


class SymbolExt(Symbol):
    """Extended Symbol with Axon serialization methods"""

    def toStr(self) -> str:
        """Return string representation matching SkySpark's toStr()"""
        return self.val  # SkySpark returns just the name without ^

    def toAxon(self) -> str:
        """Return Axon expression: ^symbol"""
        return f"^{self.val}"


class CoordExt(Coord):
    """Extended Coord with Axon serialization methods"""

    def toStr(self) -> str:
        """Return string representation matching SkySpark's toStr()"""
        # Format to 4 decimal places (standard coordinate precision), no spaces
        lat_str = f"{float(self.lat):.4f}".rstrip('0').rstrip('.')
        lng_str = f"{float(self.lng):.4f}".rstrip('0').rstrip('.')
        return f"C({lat_str},{lng_str})"

    def toAxon(self) -> str:
        """Return Axon expression: coord(lat, lng)"""
        # Format to 4 decimal places (standard coordinate precision), no spaces
        lat_str = f"{float(self.lat):.4f}".rstrip('0').rstrip('.')
        lng_str = f"{float(self.lng):.4f}".rstrip('0').rstrip('.')
        return f"coord({lat_str},{lng_str})"


class DateRangeExt(DateRange):
    """Extended DateRange with Axon serialization methods"""

    def toStr(self) -> str:
        """Return string representation for LLM/display usage"""
        return self.start.isoformat() + "," + self.end.isoformat()

    def toAxon(self) -> str:
        """Return Axon expression: (start..end)"""
        return f"({self.start.isoformat()}..{self.end.isoformat()})"


class DateTimeRangeExt(DateTimeRange):
    """Extended DateTimeRange with Axon serialization methods"""

    def toStr(self) -> str:
        """Return string representation for LLM/display usage"""
        if self.end is None:
            return _to_haystack_datetime(self.start)
        else:
            return (
                _to_haystack_datetime(self.start)
                + ","
                + _to_haystack_datetime(self.end)
            )

    def toAxon(self) -> str:
        """Return Axon expression: dateTime(..)..dateTime(..)"""
        start_dt = self.start
        start_date = start_dt.date().isoformat()
        start_time = start_dt.time().isoformat()
        start_tz = str(start_dt.tzinfo) if start_dt.tzinfo else "null"

        if self.end is None:
            return f"dateTime({start_date}, {start_time}, tz:{start_tz})"
        else:
            end_dt = self.end
            end_date = end_dt.date().isoformat()
            end_time = end_dt.time().isoformat()
            end_tz = str(end_dt.tzinfo) if end_dt.tzinfo else "null"

            return f"dateTime({start_date}, {start_time}, tz:{start_tz})..dateTime({end_date}, {end_time}, tz:{end_tz})"


class XStrExt(XStr):
    """Extended XStr with Axon serialization methods"""

    def toStr(self) -> str:
        """Return string representation matching SkySpark's toStr()"""
        # Special case for Span type - just return value
        if self.type == "Span":
            return self.val
        # Format: Type("value")
        return f'{self.type}("{self.val}")'

    def toAxon(self) -> str:
        """Return Axon expression: xstr("type", "value") or toSpan() for Span type"""
        # Special case for Span type - SkySpark uses toSpan()
        if self.type == "Span":
            return f'toSpan("{self.val}")'
        return f'xstr("{self.type}", "{self.val}")'


class DateExt:
    """Wrapper for date with Axon serialization methods"""

    def __init__(self, val: date):
        self.val = val

    def toStr(self) -> str:
        """Return string representation matching SkySpark's toStr()"""
        return self.val.isoformat()

    def toAxon(self) -> str:
        """Return Axon expression: YYYY-MM-DD"""
        return self.val.isoformat()

    def __repr__(self) -> str:
        return f"DateExt({self.val.isoformat()})"


class TimeExt:
    """Wrapper for time with Axon serialization methods"""

    def __init__(self, val: time):
        self.val = val

    def toStr(self) -> str:
        """Return string representation matching SkySpark's toStr()"""
        # Format with max 3 decimal places for milliseconds
        if self.val.microsecond == 0:
            return self.val.strftime("%H:%M:%S")
        else:
            # Convert microseconds to milliseconds (3 decimal places)
            ms = self.val.microsecond // 1000
            return self.val.strftime(f"%H:%M:%S.{ms:03d}")

    def toAxon(self) -> str:
        """Return Axon expression: HH:MM:SS[.fff]"""
        # Format with max 3 decimal places for milliseconds
        if self.val.microsecond == 0:
            return self.val.strftime("%H:%M:%S")
        else:
            # Convert microseconds to milliseconds (3 decimal places)
            ms = self.val.microsecond // 1000
            return self.val.strftime(f"%H:%M:%S.{ms:03d}")

    def __repr__(self) -> str:
        return f"TimeExt({self.val.isoformat()})"


class DateTimeExt:
    """Wrapper for datetime with Axon serialization methods"""

    def __init__(self, val: datetime):
        self.val = val

    def toStr(self) -> str:
        """Return string representation matching SkySpark's toStr()"""
        return _to_haystack_datetime(self.val)

    def toAxon(self) -> str:
        """Return Axon expression: parseDateTime("...")"""
        # SkySpark uses parseDateTime() with the Haystack datetime string
        haystack_str = _to_haystack_datetime(self.val)
        return f'parseDateTime("{haystack_str}")'

    def __repr__(self) -> str:
        return f"DateTimeExt({self.val.isoformat()})"


class ListExt(list):
    """Extended list with Axon serialization methods"""

    def toStr(self) -> str:
        """Return string representation matching SkySpark's toStr()"""
        parts = []
        for item in self:
            if hasattr(item, 'toStr'):
                parts.append(item.toStr())
            elif isinstance(item, bool):
                parts.append("true" if item else "false")
            elif isinstance(item, str):
                parts.append(item)
            else:
                parts.append(str(item))
        return "[" + ", ".join(parts) + "]"

    def toAxon(self) -> str:
        """Return Axon expression: [item1, item2, ...]"""
        parts = []
        for item in self:
            if hasattr(item, 'toAxon'):
                parts.append(item.toAxon())
            elif isinstance(item, bool):
                parts.append("true" if item else "false")
            elif isinstance(item, str):
                parts.append(f'"{item}"')
            else:
                parts.append(str(item))
        return "[" + ", ".join(parts) + "]"


class DictExt(dict):
    """Extended dict with Axon serialization methods"""

    def toStr(self) -> str:
        """Return string representation matching SkySpark's toStr()"""
        parts = []
        for k, v in self.items():
            if hasattr(v, 'toStr'):
                v_str = v.toStr()
            elif isinstance(v, bool):
                v_str = "true" if v else "false"
            elif isinstance(v, str):
                v_str = v
            else:
                v_str = str(v)
            parts.append(f"{k}:{v_str}")
        return "{" + ", ".join(parts) + "}"

    def toAxon(self) -> str:
        """Return Axon expression: {key1:val1, key2:val2, ...}"""
        parts = []
        for k, v in self.items():
            if hasattr(v, 'toAxon'):
                v_axon = v.toAxon()
            elif isinstance(v, bool):
                v_axon = "true" if v else "false"
            elif isinstance(v, str):
                v_axon = f'"{v}"'
            else:
                v_axon = str(v)
            parts.append(f"{k}:{v_axon}")
        return "{" + ", ".join(parts) + "}"


def _to_haystack_datetime(x: datetime) -> str:
    """Convert datetime to Haystack format string for LLM/display usage"""
    iana_tz = str(x.tzinfo)
    if "/" in iana_tz:
        haystack_tz = iana_tz.split("/")[-1]
    else:
        haystack_tz = iana_tz

    if x.microsecond == 0:
        dt = x.isoformat(timespec="seconds")
    else:
        dt = x.isoformat(timespec="milliseconds")

    return f"{dt} {haystack_tz}"

