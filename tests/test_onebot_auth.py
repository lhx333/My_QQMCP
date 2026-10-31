"""OneBot authentication regressions for the LLOneBot backend; no live QQ messages are sent."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from aiohttp import web

from llonebot_mcp.client import LLOneBotClient
from llonebot_mcp.config import ensure_onebot_config
from llonebot_mcp.token_store import ensure_onebot_token, load_onebot_token, token_path


class OneBotAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_token_is_outside_project_and_reused_by_both_clients(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'LOCALAPPDATA': directory}), \
                patch('llonebot_mcp.token_store.restrict_credential_acl'):
            first = ensure_onebot_token()
            self.assertEqual(first, ensure_onebot_token())
            self.assertEqual(first, load_onebot_token())
            self.assertEqual(len(first), 64)
            self.assertTrue(Path(token_path()).is_relative_to(directory))
            client = LLOneBotClient(auto_heal=False)
            self.assertEqual(client._get_headers()['Authorization'], f'Bearer {first}')
            await client.close()

    async def test_config_uses_llonebot_ob11_http_and_ws_token_fields(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'LOCALAPPDATA': directory}), \
                patch('llonebot_mcp.token_store.restrict_credential_acl'):
            home = Path(directory) / 'llbot'
            (home / 'data').mkdir(parents=True)
            (home / 'llbot.js').write_text('entry', encoding='utf-8')
            with patch.multiple(
                'llonebot_mcp.config',
                LLBOT_HOME=str(home),
                LLBOT_CONFIG_DIR=str(home),
                LLBOT_DATA_DIR=str(home / 'data'),
                LLBOT_DEFAULT_CONFIG=str(home / 'default_config.json'),
                LLBOT_ENTRY_JS=str(home / 'llbot.js'),
                resolve_install_root=lambda: str(home),
            ):
                self.assertTrue(ensure_onebot_config('123456'))
                credential = load_onebot_token()
                config = json.loads((home / 'data' / 'config_123456.json').read_text(encoding='utf-8'))
                connect = config['ob11']['connect']
                http = next(item for item in connect if item['type'] == 'http')
                ws = next(item for item in connect if item['type'] == 'ws')
                self.assertTrue(config['ob11']['enable'])
                self.assertEqual(http['token'], credential)
                self.assertEqual(ws['token'], credential)
                self.assertEqual(http['port'], 3000)
                self.assertEqual(ws['port'], 3001)
                self.assertNotIn('secret', http)

    async def test_running_client_picks_up_new_token_on_http_and_ws_reconnect(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'LOCALAPPDATA': directory}), \
                patch('llonebot_mcp.token_store.restrict_credential_acl'):
            client = LLOneBotClient(auto_heal=False)
            self.assertNotIn('Authorization', client._get_headers())
            credential = ensure_onebot_token()
            self.assertEqual(client._get_headers()['Authorization'], f'Bearer {credential}')
            await client.close()

    async def test_403_recovers_only_after_candidate_passes_read_only_probe(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'LOCALAPPDATA': directory}), \
                patch('llonebot_mcp.token_store.restrict_credential_acl'), \
                patch('llonebot_mcp.client.get_current_account', return_value='123456'):
            home = Path(directory) / 'llbot'
            data = home / 'data'
            data.mkdir(parents=True)
            (home / 'llbot.js').write_text('entry', encoding='utf-8')
            old = ensure_onebot_token()
            candidate = 'verified-candidate-token-12345678901234567890'
            config_path = data / 'config_123456.json'
            config = {'ob11': {'enable': True, 'connect': [
                {'type': 'http', 'enable': True, 'port': 3000,
                 'token': 'unverified-candidate-token-1234567890'},
                {'type': 'ws', 'enable': True, 'port': 3001,
                 'token': 'unverified-candidate-token-1234567890'},
            ]}}
            config_path.write_text(json.dumps(config), encoding='utf-8')

            async def login(request):
                if request.headers.get('Authorization') != f'Bearer {candidate}':
                    return web.json_response({'message': 'token verify failed!'}, status=403)
                return web.json_response({'status': 'ok', 'retcode': 0, 'data': {'user_id': 123456}})

            app = web.Application()
            app.router.add_post('/get_login_info', login)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, '127.0.0.1', 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            client = LLOneBotClient(http_url=f'http://127.0.0.1:{port}', token=old, auto_heal=False)
            try:
                with patch('llonebot_mcp.client.DEFAULT_HTTP_PORT', port), \
                     patch('llonebot_mcp.config.find_installations', return_value=[str(home)]), \
                     patch('llonebot_mcp.client.LLBOT_HOME', str(home)):
                    rejected = await client.call_api('get_login_info', timeout=5)
                    self.assertEqual(rejected['retcode'], 403)
                    self.assertEqual(load_onebot_token(), old)
                    for item in config['ob11']['connect']:
                        item['token'] = candidate
                    config_path.write_text(json.dumps(config), encoding='utf-8')
                    result = await client.call_api('get_login_info', timeout=5)
                self.assertEqual(result['status'], 'ok')
                self.assertEqual(client.token, candidate)
                self.assertEqual(load_onebot_token(), candidate)
            finally:
                await client.close()
                await runner.cleanup()


if __name__ == '__main__':
    unittest.main()
