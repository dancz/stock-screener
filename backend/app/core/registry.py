from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from typing import Any, TypeVar

from app.core.contracts import (
    AnalyzerPlugin,
    NotifierPlugin,
    OptionStrategyPlugin,
    SourcePlugin,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class PluginRegistry:
    """Auto-discovers and caches plugin instances from backend/app/plugins/*."""

    def __init__(self) -> None:
        self.sources: dict[str, SourcePlugin] = {}
        self.analyzers: dict[str, AnalyzerPlugin] = {}
        self.options: dict[str, OptionStrategyPlugin] = {}
        self.notifiers: dict[str, NotifierPlugin] = {}
        self._discovered = False

    def discover_all(self, force: bool = False) -> None:
        if self._discovered and not force:
            return
        self.sources = self._scan_package("app.plugins.sources", SourcePlugin)
        self.analyzers = self._scan_package("app.plugins.analyzers", AnalyzerPlugin)
        self.options = self._scan_package("app.plugins.options", OptionStrategyPlugin)
        self.notifiers = self._scan_package("app.plugins.notifiers", NotifierPlugin)
        self._discovered = True
        logger.info(
            "Discovered plugins: %d sources, %d analyzers, %d options, %d notifiers",
            len(self.sources),
            len(self.analyzers),
            len(self.options),
            len(self.notifiers),
        )

    def _scan_package(self, package_name: str, base_cls: type[T]) -> dict[str, T]:
        found: dict[str, T] = {}
        try:
            package = importlib.import_module(package_name)
        except Exception as exc:
            logger.warning("Failed to import plugin package %s: %s", package_name, exc)
            return found

        package_path = getattr(package, "__path__", None)
        if not package_path:
            return found

        for _, mod_name, is_pkg in pkgutil.iter_modules(package_path):
            if is_pkg or mod_name.startswith("_"):
                continue
            full_name = f"{package_name}.{mod_name}"
            try:
                module = importlib.import_module(full_name)
            except Exception as exc:
                logger.exception("Error importing plugin module %s: %s", full_name, exc)
                continue

            for _, obj in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(obj, base_cls)
                    and obj is not base_cls
                    and not inspect.isabstract(obj)
                    and obj.__module__ == module.__name__
                ):
                    try:
                        instance = obj()
                        plugin_id = getattr(instance, "plugin_id", "") or mod_name
                        found[plugin_id] = instance
                    except Exception as exc:
                        logger.exception("Error instantiating plugin %s: %s", obj.__name__, exc)
        return found

    def get_analyzers(self, enabled_map: dict[str, bool] | None = None) -> list[AnalyzerPlugin]:
        self.discover_all()
        if enabled_map is None:
            return list(self.analyzers.values())
        return [p for pid, p in self.analyzers.items() if enabled_map.get(pid, True)]

    def get_sources(self, enabled_map: dict[str, bool] | None = None) -> list[SourcePlugin]:
        self.discover_all()
        if enabled_map is None:
            return list(self.sources.values())
        return [p for pid, p in self.sources.items() if enabled_map.get(pid, True)]

    def get_option_plugins(self, enabled_map: dict[str, bool] | None = None) -> list[OptionStrategyPlugin]:
        self.discover_all()
        if enabled_map is None:
            return list(self.options.values())
        return [p for pid, p in self.options.items() if enabled_map.get(pid, True)]

    def get_notifiers(self) -> list[NotifierPlugin]:
        self.discover_all()
        return list(self.notifiers.values())

    def describe_all(self, enabled_map: dict[str, bool] | None = None) -> list[dict[str, Any]]:
        self.discover_all()
        items: list[dict[str, Any]] = []
        enabled_map = enabled_map or {}

        for pid, p in self.analyzers.items():
            items.append(
                {
                    "plugin_id": pid,
                    "display_name": p.display_name or pid,
                    "description": p.description,
                    "kind": "analyzer",
                    "category": p.category,
                    "default_weight": p.default_weight,
                    "requires_deep_data": p.requires_deep_data,
                    "enabled": enabled_map.get(pid, True),
                }
            )
        for pid, p in self.sources.items():
            items.append(
                {
                    "plugin_id": pid,
                    "display_name": p.display_name or pid,
                    "description": p.description,
                    "kind": "source",
                    "enabled": enabled_map.get(pid, True),
                }
            )
        for pid, p in self.options.items():
            items.append(
                {
                    "plugin_id": pid,
                    "display_name": p.display_name or pid,
                    "description": p.description,
                    "kind": "options",
                    "enabled": enabled_map.get(pid, True),
                }
            )
        for pid, p in self.notifiers.items():
            items.append(
                {
                    "plugin_id": pid,
                    "display_name": p.display_name or pid,
                    "description": p.description,
                    "kind": "notifier",
                    "enabled": enabled_map.get(pid, True),
                }
            )
        return items


registry = PluginRegistry()
