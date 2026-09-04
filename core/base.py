"""
core/base.py
Base classes for collectors and transformers.
Extend these to add support for new Avi object types.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any

from core.avi_client import AviClient
from core.events import EventBus, Phase


class BaseCollector(ABC):
    """
    Base class for all Avi object collectors.
    Each subclass handles one Avi API object type.
    """
    object_type: str = ""       # e.g. "virtualservice"
    display_name: str = ""      # e.g. "Virtual Services"
    collect_runtime: bool = False  # also fetch runtime health state

    def __init__(self, client: AviClient, bus: EventBus):
        self._client = client
        self._bus    = bus

    def collect(self) -> list[dict]:
        """
        Fetch all objects of this type from Avi.
        Returns enriched list (raw API data + optional runtime state).
        """
        self._bus.info(Phase.COLLECT, f"Collecting {self.display_name}...",
                       object_type=self.object_type)
        try:
            items = self._client.get_all(self.object_type)
        except Exception as e:
            self._bus.error(Phase.COLLECT,
                            f"Failed to collect {self.display_name}: {e}",
                            object_type=self.object_type,
                            detail={"error": str(e)})
            return []

        self._bus.info(Phase.COLLECT,
                       f"Collected {len(items)} {self.display_name}",
                       object_type=self.object_type)

        if self.collect_runtime:
            items = self._enrich_with_runtime(items)

        return self._post_process(items)

    def _enrich_with_runtime(self, items: list[dict]) -> list[dict]:
        """Fetch runtime state and attach to each object."""
        enriched = []
        for item in items:
            uuid = item.get("uuid", "")
            try:
                rt = self._client.get_runtime(self.object_type, uuid)
                item["_runtime"] = rt or {}
            except Exception:
                item["_runtime"] = {}
            enriched.append(item)
        return enriched

    def _post_process(self, items: list[dict]) -> list[dict]:
        """Override in subclasses to add additional enrichment."""
        return items


class BaseTransformer(ABC):
    """
    Base class for all Avi→FortiADC transformers.
    Each subclass handles one object type.
    """
    object_type: str = ""
    fortiadc_path: str = ""    # FortiADC API path for this object type

    def __init__(self, bus: EventBus):
        self._bus = bus

    @abstractmethod
    def transform(self, avi_object: dict) -> dict | None:
        """
        Transform one Avi object to FortiADC format.
        Return None if this object cannot be migrated (emit MANUAL event first).
        Return dict with keys:
          'fortiadc_path': API path
          'payload':       FortiADC API payload
          'name':          Object name in FortiADC
          'depends_on':    list of (fortiadc_path, name) this depends on
        """
        ...

    def transform_all(self, avi_objects: list[dict]) -> list[dict]:
        """Transform a list of Avi objects, collecting results and events."""
        results = []
        for obj in avi_objects:
            name = obj.get("name", obj.get("uuid", "?"))
            try:
                result = self.transform(obj)
                if result is not None:
                    results.append(result)
                    self._bus.info(
                        Phase.TRANSFORM,
                        f"Transformed {self.object_type}/{name}",
                        object_type=self.object_type,
                        object_name=name,
                        object_uuid=obj.get("uuid", ""),
                    )
            except Exception as e:
                self._bus.error(
                    Phase.TRANSFORM,
                    f"Transform failed for {self.object_type}/{name}: {e}",
                    object_type=self.object_type,
                    object_name=name,
                    object_uuid=obj.get("uuid", ""),
                    detail={"error": str(e), "avi_object": name},
                )
        return results

    def _flag_unsupported(self, avi_object: dict, reason: str,
                          suggestion: str = "") -> None:
        """Emit a MANUAL event for an object that cannot be auto-migrated."""
        name = avi_object.get("name", "?")
        self._bus.manual(
            Phase.TRANSFORM,
            f"Cannot auto-migrate {self.object_type}/{name}: {reason}",
            object_type=self.object_type,
            object_name=name,
            object_uuid=avi_object.get("uuid", ""),
            detail={
                "reason":     reason,
                "suggestion": suggestion,
                "avi_config": {k: v for k, v in avi_object.items()
                               if not k.startswith("_")},
            },
        )
