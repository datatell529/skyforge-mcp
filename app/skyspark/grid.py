import json
from datetime import date, datetime, time
from functools import wraps
from typing import Any, Callable, TypeVar, Union

from phable import Grid, Marker, NA, Remove, Number, Uri, Ref, Symbol, Coord, DateRange, DateTimeRange, XStr
from phable.parsers.json import grid_to_json
from phable.parsers.zinc_writer import ZincWriter

from .types import (
    MarkerExt,
    NAExt,
    RemoveExt,
    NumberExt,
    UriExt,
    RefExt,
    SymbolExt,
    CoordExt,
    DateRangeExt,
    DateTimeRangeExt,
    XStrExt,
    DateExt,
    TimeExt,
    DateTimeExt,
    ListExt,
    DictExt,
)

T = TypeVar("T")


class HGrid:
    """Wrapper for phable Grid with conversion and access methods

    Attributes:
        grid: Underlying phable Grid object
    """

    def __init__(self, grid: Grid):
        """Initialize HGrid from phable Grid

        Args:
            grid: phable Grid object
        """
        self.grid = grid

    @staticmethod
    def _convert_value(value: Any) -> Any:
        """Convert phable types to extended types recursively

        Args:
            value: Value to convert

        Returns:
            Converted value with extended types
        """
        # Singletons
        if isinstance(value, Marker):
            return MarkerExt()
        elif isinstance(value, NA):
            return NAExt()
        elif isinstance(value, Remove):
            return RemoveExt()

        # Dataclasses - need to extract fields and reconstruct
        elif isinstance(value, Number):
            return NumberExt(val=value.val, unit=value.unit)
        elif isinstance(value, Uri):
            return UriExt(val=value.val)
        elif isinstance(value, Ref):
            return RefExt(val=value.val, dis=value.dis)
        elif isinstance(value, Symbol):
            return SymbolExt(val=value.val)
        elif isinstance(value, Coord):
            return CoordExt(lat=value.lat, lng=value.lng)
        elif isinstance(value, DateRange):
            return DateRangeExt(start=value.start, end=value.end)
        elif isinstance(value, DateTimeRange):
            return DateTimeRangeExt(start=value.start, end=value.end)
        elif isinstance(value, XStr):
            return XStrExt(type=value.type, val=value.val)

        # Python datetime types - wrap them
        elif isinstance(value, datetime):  # Check datetime before date (datetime is subclass of date)
            return DateTimeExt(val=value)
        elif isinstance(value, date):
            return DateExt(val=value)
        elif isinstance(value, time):
            return TimeExt(val=value)

        # Collections - recurse and convert to extended types
        elif isinstance(value, dict):
            converted = DictExt({k: HGrid._convert_value(v) for k, v in value.items()})
            return converted
        elif isinstance(value, list):
            converted = ListExt([HGrid._convert_value(v) for v in value])
            return converted

        # Primitives - pass through
        return value

    def toJson(self) -> dict:
        """Convert Grid to JSON dict

        Returns:
            Dict representation of grid
        """
        return grid_to_json(self.grid)

    def toZinc(self) -> str:
        """Convert Grid to Zinc format string

        Returns:
            Zinc string representation
        """
        return ZincWriter.grid_to_str(self.grid)

    @property
    def rows(self) -> list:
        """Get all rows from grid with extended types

        Returns:
            List of rows with phable types converted to extended types
        """
        return [self._convert_value(row) for row in self.grid.rows]

    @property
    def firstRow(self) -> Any:
        """Get first row from grid with extended types

        Returns:
            First row with extended types or None if empty
        """
        if self.grid.rows:
            return self._convert_value(self.grid.rows[0])
        return None

    @property
    def firstVal(self) -> Any:
        """Get first value from first row with extended types

        Returns:
            First value from first row with extended types, or None if empty
        """
        if self.grid.rows and len(self.grid.rows) > 0:
            first_row = self.grid.rows[0]
            if hasattr(first_row, '__dict__') and first_row.__dict__:
                val = next(iter(first_row.__dict__.values()))
                return self._convert_value(val)
            elif isinstance(first_row, dict) and first_row:
                val = next(iter(first_row.values()))
                return self._convert_value(val)
        return None

    def __str__(self) -> str:
        """String representation returns Zinc format"""
        return self.toJson()

    def __repr__(self) -> str:
        """Repr shows HGrid wrapper"""
        return f"HGrid(rows={len(self.grid.rows)})"
