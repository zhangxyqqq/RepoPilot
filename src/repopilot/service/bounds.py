"""Bound request bodies before JSON parsing, including chunked HTTP requests."""
from uuid import uuid4
from starlette.responses import JSONResponse


class BodyLimit:
    def __init__(self, app, limit=131072):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in ('POST','PUT','PATCH'):
            return await self.app(scope,receive,send)
        messages, size = [], 0
        while True:
            message = await receive()
            if message['type']=='http.disconnect':
                return
            size += len(message.get('body',b''))
            if size > self.limit:
                request_id = str(scope.get('state',{}).get('request_id',uuid4()))
                response = JSONResponse({'detail':'request body too large','code':'payload_too_large','request_id':request_id},status_code=413)
                return await response(scope,receive,send)
            messages.append(message)
            if not message.get('more_body',False):
                break
        iterator = iter(messages)
        async def replay():
            try:
                return next(iterator)
            except StopIteration:
                return await receive()
        await self.app(scope,replay,send)
