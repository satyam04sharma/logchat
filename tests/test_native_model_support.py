"""Explicit verified metadata responses for native generation transport tests."""
import httpx


def guarded_transport(handler):
    async def respond(request):
        if request.url.path == '/api/version':
            return httpx.Response(200, json={'version': '0.34.4'})
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'details': {'format': 'gguf'},
                                           'capabilities': ['completion'], 'messages': []})
        response = handler(request)
        if hasattr(response, '__await__'):
            response = await response
        return response
    return httpx.MockTransport(respond)
