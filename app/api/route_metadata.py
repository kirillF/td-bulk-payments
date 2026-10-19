"""Derive telemetry input at the same public boundary that installs HTTP routes."""

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Match, Route, WebSocketRoute
from starlette.types import Scope

from app.core.request_context import RequestRoute


class ProbeRoute(APIRoute):
    """API-owned policy for operational routes, independent of their URLs."""

    telemetry_probe = True


async def _not_dispatched(request: Request) -> Response:
    raise RuntimeError("Metadata matchers never dispatch requests")


@dataclass(frozen=True)
class _ConfiguredRoute:
    matcher: Route
    is_probe: bool


class RouteComposition:
    """Install and describe routes together; freeze before serving an app instance.

    Read public leaf declarations before FastAPI turns included routers into lazy nodes.
    Matchers are generated copies of configuration, never a second handwritten inventory.
    """

    def __init__(self, application: FastAPI) -> None:
        self.application = application
        self._routes: list[_ConfiguredRoute] = []
        self._frozen = False
        self._add(application.routes, "")

    def _add(self, routes: list[BaseRoute], prefix: str) -> None:
        for route in routes:
            if isinstance(route, WebSocketRoute):
                continue
            if not isinstance(route, Route):
                raise ValueError("Compose public HTTP route declarations before including routers")
            matcher = Route(prefix + route.path, _not_dispatched, methods=route.methods)
            # Starlette adds HEAD for GET; FastAPI declarations may intentionally omit it.
            matcher.methods = set(route.methods) if route.methods is not None else None
            self._routes.append(
                _ConfiguredRoute(matcher, bool(getattr(route, "telemetry_probe", False)))
            )

    def include(self, router: APIRouter, *, prefix: str = "") -> None:
        if self._frozen:
            raise ValueError("Route configuration is frozen; construct a new application")
        self._add(router.routes, prefix)
        self.application.include_router(router, prefix=prefix)

    def freeze(self) -> Callable[[Scope], RequestRoute]:
        self._frozen = True
        routes = tuple(self._routes)
        methods = frozenset(method for item in routes for method in item.matcher.methods or ())

        def describe(scope: Scope) -> RequestRoute:
            partial: _ConfiguredRoute | None = None
            for item in routes:
                match, _ = item.matcher.matches(scope)
                if match == Match.FULL:
                    return RequestRoute(item.matcher.path_format, scope["method"], item.is_probe)
                if match == Match.PARTIAL and partial is None:
                    partial = item
            if partial is not None:
                return RequestRoute(partial.matcher.path_format, "OTHER", partial.is_probe)
            method = scope.get("method", "")
            return RequestRoute(method=method if method in methods else "OTHER")

        return describe
