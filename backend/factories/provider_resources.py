"""Own provider transports explicitly instead of closing library-shared clients."""

from contextlib import AsyncExitStack
import os


def own_http_clients(resources: AsyncExitStack, sync_type, async_type, *, proxy=None):
    sync = sync_type(proxy=proxy)
    resources.callback(sync.close)
    asynchronous = async_type(proxy=proxy)
    resources.push_async_callback(asynchronous.aclose)
    return sync, asynchronous


def openai_transport_options(resources: AsyncExitStack | None) -> dict:
    if resources is None:
        return {}
    from openai import DefaultAsyncHttpxClient, DefaultHttpxClient

    sync, asynchronous = own_http_clients(
        resources, DefaultHttpxClient, DefaultAsyncHttpxClient,
        proxy=os.environ.get("OPENAI_PROXY") or None,
    )
    return {
        "http_client": sync,
        "http_async_client": asynchronous,
        # The owned transports above already apply this proxy. LangChain rejects
        # combining its own proxy construction with injected HTTP clients.
        "openai_proxy": "",
    }
