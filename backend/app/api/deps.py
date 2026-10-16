from fastapi import Request
from app.container import Container
from app.orchestrator import Orchestrator


def get_container(request: Request) -> Container:
    return request.app.state.container


def get_orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator
