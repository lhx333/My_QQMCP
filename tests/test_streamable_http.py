"""Streamable HTTP MCP transport end-to-end tests (local aiohttp, no live QQ)."""
import asyncio
import json
import os
import sys
import unittest

import aiohttp

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from llonebot_mcp.client import LLOneBotClient
from llonebot_mcp.server import LLOneBotMCPServer
from llonebot_mcp.transport import StreamableHTTPTransport, SESSION_HEADER, PROTOCOL_HEADER


class StreamableHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = LLOneBotClient(auto_heal=False, token='mcp-test')
        self.server = LLOneBotMCPServer(client=self.client)
        self.transport = StreamableHTTPTransport(
            server=self.server, host='127.0.0.1', port=0, path='/mcp', auth_token='mcp-secret'
        )
        await self.transport.start()
        self.base = f'http://127.0.0.1:{self.transport.port}'
        self.http = aiohttp.ClientSession()
        self.auth = {'Authorization': 'Bearer mcp-secret'}

    async def asyncTearDown(self):
        await self.http.close()
        await self.transport.stop()
        await self.client.close()

    async def rpc(self, payload, session=None, headers=None):
        final_headers = {
            'Accept': 'application/json, text/event-stream',
            'Content-Type': 'application/json',
            **self.auth,
        }
        if session:
            final_headers[SESSION_HEADER] = session
        if headers:
            final_headers.update(headers)
        return await self.http.post(f'{self.base}/mcp', json=payload, headers=final_headers)

    async def test_initialize_session_and_tools_list(self):
        init = {
            'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
            'params': {'protocolVersion': '2025-06-18',
                       'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}
        }
        async with await self.rpc(init) as resp:
            self.assertEqual(resp.status, 200)
            session = resp.headers.get(SESSION_HEADER)
            self.assertTrue(session)
            self.assertEqual(resp.headers.get(PROTOCOL_HEADER), '2025-06-18')
            data = await resp.json()
        self.assertEqual(data['result']['serverInfo']['name'], 'omniqq-mcp')

        async with await self.rpc({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}, session) as resp:
            self.assertEqual(resp.status, 200)
            data = await resp.json()
        self.assertEqual(len(data['result']['tools']), 26)
        names = {tool['name'] for tool in data['result']['tools']}
        self.assertIn('check_llonebot_environment', names)
        self.assertIn('check_napcat_environment', names)

        async with await self.rpc({'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                                  session) as resp:
            self.assertEqual(resp.status, 202)

        async with await self.rpc({'jsonrpc': '2.0', 'id': 3, 'method': 'ping'}, session) as resp:
            self.assertEqual(resp.status, 200)
            data = await resp.json()
        self.assertEqual(data['result'], {})

    async def test_session_is_required_and_delete_invalidates(self):
        init = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                'params': {'protocolVersion': '2025-03-26'}}
        async with await self.rpc(init) as resp:
            session = resp.headers[SESSION_HEADER]
            self.assertEqual(resp.headers.get(PROTOCOL_HEADER), '2025-03-26')

        # 无会话头的普通请求按 Streamable HTTP 规范返回 400
        async with await self.rpc({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}) as resp:
            self.assertEqual(resp.status, 400)
            body = await resp.json()
            self.assertEqual(body['error']['code'], -32002)

        # 未知会话返回 404
        async with await self.rpc({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/list'},
                                  'not-a-real-session') as resp:
            self.assertEqual(resp.status, 404)

        # DELETE 终止会话后再次请求返回 404
        async with self.http.delete(f'{self.base}/mcp', headers={**self.auth, SESSION_HEADER: session}) as resp:
            self.assertEqual(resp.status, 204)
        async with await self.rpc({'jsonrpc': '2.0', 'id': 4, 'method': 'ping'}, session) as resp:
            self.assertEqual(resp.status, 404)

    async def test_invalid_protocol_version_and_auth(self):
        init = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                'params': {'protocolVersion': '2025-06-18'}}
        headers = {'MCP-Protocol-Version': '2099-01-01'}
        async with await self.rpc(init, headers=headers) as resp:
            self.assertEqual(resp.status, 400)
            body = await resp.json()
            self.assertIn('2099-01-01', body['error']['message'])

        no_auth_headers = {'Accept': 'application/json, text/event-stream',
                           'Content-Type': 'application/json'}
        async with self.http.post(f'{self.base}/mcp', json=init, headers=no_auth_headers) as resp:
            self.assertEqual(resp.status, 401)

    async def test_get_sse_health_and_server_message_notification(self):
        init = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                'params': {'protocolVersion': '2025-06-18'}}
        async with await self.rpc(init) as resp:
            session = resp.headers[SESSION_HEADER]

        async with self.http.get(f'{self.base}/mcp', headers={
            **self.auth, SESSION_HEADER: session, 'Accept': 'text/event-stream'
        }) as resp:
            self.assertEqual(resp.status, 200)
            self.assertTrue(resp.headers['Content-Type'].startswith('text/event-stream'))
            line = await asyncio.wait_for(resp.content.readline(), timeout=3)
            self.assertIn(b'connected', line)

        async with self.http.get(f'{self.base}/health', headers=self.auth) as resp:
            self.assertEqual(resp.status, 200)
            body = await resp.json()
            self.assertEqual(body['status'], 'ok')
            self.assertEqual(body['transport'], 'streamable-http')

    async def test_legacy_tool_alias_through_streamable_http(self):
        init = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                'params': {'protocolVersion': '2025-06-18'}}
        async with await self.rpc(init) as resp:
            session = resp.headers[SESSION_HEADER]
        call = {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                'params': {'name': 'list_supported_napcat_actions', 'arguments': {'category': 'all'}}}
        async with await self.rpc(call, session) as resp:
            self.assertEqual(resp.status, 200)
            data = await resp.json()
        payload = json.loads(data['result']['content'][0]['text'])
        self.assertGreaterEqual(payload['total_count'], 125)

    async def test_stateless_mode_does_not_require_session(self):
        transport = StreamableHTTPTransport(
            server=self.server, host='127.0.0.1', port=0, path='/mcp', stateless=True
        )
        await transport.start()
        base = f'http://127.0.0.1:{transport.port}'
        try:
            init = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                    'params': {'protocolVersion': '2025-06-18'}}
            headers = {'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json'}
            async with self.http.post(f'{base}/mcp', json=init, headers=headers) as resp:
                self.assertEqual(resp.status, 200)
                self.assertNotIn(SESSION_HEADER, resp.headers)
            async with self.http.post(f'{base}/mcp',
                                      json={'jsonrpc': '2.0', 'id': 2, 'method': 'ping'},
                                      headers=headers) as resp:
                self.assertEqual(resp.status, 200)
                data = await resp.json()
            self.assertEqual(data['result'], {})
        finally:
            await transport.stop()


if __name__ == '__main__':
    unittest.main()
