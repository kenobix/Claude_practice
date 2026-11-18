"""Bound JSON ingress before FastAPI buffers or parses it, including chunked input."""
from asyncio import timeout

from starlette.responses import JSONResponse


class JsonBodyLimit:
    def __init__(self, app, *, path, limit, read_timeout=None):
        self.app = app
        self.path = path
        self.limit = limit
        self.read_timeout = read_timeout

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] != 'POST' or scope['path'] != self.path:
            await self.app(scope, receive, send)
            return
        headers = scope['headers']
        lengths = [v for k, v in headers if k.lower() == b'content-length']
        try:
            if len(lengths) > 1 or (lengths and (not lengths[0].isdigit())):
                raise ValueError()
            length = int(lengths[0]) if lengths else None
        except ValueError:
            await JSONResponse({'detail': 'invalid content length'}, 400)(scope, receive, send)
            return
        if length is not None and length > self.limit:
            await JSONResponse({'detail': 'body too large'}, 413)(scope, receive, send)
            return
        types = [v.split(b';', 1)[0].strip().lower() for k, v in headers if k.lower() == b'content-type']
        if types != [b'application/json']:
            await JSONResponse({'detail': 'application/json required'}, 415)(scope, receive, send)
            return
        body = bytearray()
        try:
            async with timeout(self.read_timeout):
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect':
                        return
                    chunk = message.get('body', b'')
                    if len(body) + len(chunk) > self.limit:
                        await JSONResponse({'detail': 'body too large'}, 413)(scope, receive, send)
                        return
                    body.extend(chunk)
                    if not message.get('more_body', False):
                        break
        except TimeoutError:
            await JSONResponse({'detail': 'body read timed out'}, 408)(scope, receive, send)
            return
        if length is not None and len(body) != length:
            await JSONResponse({'detail': 'content length mismatch'}, 400)(scope, receive, send)
            return

        raw = bytes(body)

        async def buffered_receive():
            return {'type': 'http.request', 'body': raw, 'more_body': False}

        await self.app(scope, buffered_receive, send)
