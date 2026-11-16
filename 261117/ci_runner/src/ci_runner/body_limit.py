"""Bound POST /runs before FastAPI buffers or parses JSON, including chunked input."""
from starlette.responses import JSONResponse

from .settings import BODY_LIMIT


class RunBodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] != 'POST' or scope['path'] != '/runs':
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
        if length is not None and length > BODY_LIMIT:
            await JSONResponse({'detail': 'body too large'}, 413)(scope, receive, send)
            return
        types = [v.split(b';', 1)[0].strip().lower() for k, v in headers if k.lower() == b'content-type']
        if types != [b'application/json']:
            await JSONResponse({'detail': 'application/json required'}, 415)(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            chunk = message.get('body', b'')
            if len(body) + len(chunk) > BODY_LIMIT:
                await JSONResponse({'detail': 'body too large'}, 413)(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get('more_body', False):
                break
        if length is not None and len(body) != length:
            await JSONResponse({'detail': 'content length mismatch'}, 400)(scope, receive, send)
            return

        async def buffered_receive():
            return {'type': 'http.request', 'body': bytes(body), 'more_body': False}

        await self.app(scope, buffered_receive, send)
