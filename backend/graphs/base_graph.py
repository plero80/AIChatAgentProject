"""Common execution contract; each graph declares its request and result types."""

import asyncio
from abc import ABC, abstractmethod
from typing import Generic, TypeVar


RequestT = TypeVar("RequestT")
ResultT = TypeVar("ResultT")


class BaseGraph(ABC, Generic[RequestT, ResultT]):
    @abstractmethod
    async def arun(self, request: RequestT) -> ResultT:
        """Execute one request using this graph's workflow."""

    def run(self, request: RequestT) -> ResultT:
        """Blocking entry point for synchronous callers outside an event loop."""
        return asyncio.run(self.arun(request))
