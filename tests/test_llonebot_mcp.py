"""
LLOneBot MCP Server 自动化单元测试与 stdio 兼容模式端到端验证套件
"""
import os
import sys
import json
import subprocess
import unittest
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from llonebot_mcp.server import LLOneBotMCPServer
from llonebot_mcp.message_buffer import MessageBuffer


class TestLLOneBotMCP(unittest.IsolatedAsyncioTestCase):

    async def test_structured_message_segments_are_forwarded_unchanged(self):
        from llonebot_mcp.tools.messaging import send_private_msg, send_group_msg
        client = type('Client', (), {'call_api': AsyncMock(return_value={'status':'ok'})})()
        message = [{'type':'face','data':{'id':'14'}},
                   {'type':'mface','data':{'emoji_id':'77','key':'k','summary':'微笑'}}]
        await send_private_msg(client, 103, message)
        client.call_api.assert_awaited_with('send_private_msg',
            {'user_id':103,'message':message,'auto_escape':False})
        await send_group_msg(client, 999, message)
        client.call_api.assert_awaited_with('send_group_msg',
            {'group_id':999,'message':message,'auto_escape':False})
        tools = self.server.registry.list_tools()
        send_schema = next(tool['inputSchema'] for tool in tools if tool['name'] == 'send_private_msg')
        self.assertEqual({part['type'] for part in send_schema['properties']['message']['anyOf']},
                         {'string','array'})
        cleaned = MessageBuffer.extract_clean_text({'message': message})
        self.assertIn('表情:14', cleaned)
        self.assertIn('商城表情:微笑', cleaned)

    async def asyncSetUp(self):
        self.release_patch = patch('llonebot_mcp.deployer.fetch_github_latest_release', AsyncMock(return_value={'reachable':False,'error':'offline test'}))
        self.release_patch.start()
        self.addCleanup(self.release_patch.stop)
        self.port_patch = patch('llonebot_mcp.tools.env_lifecycle.is_port_listening',return_value=False)
        self.port_patch.start()
        self.addCleanup(self.port_patch.stop)
        self.server = LLOneBotMCPServer()
        # 测试中禁用自动拉起真实进程，防止单元测试产生副作用
        self.server.client.auto_heal = False

    async def asyncTearDown(self):
        await self.server.client.close()

    async def test_01_initialize_and_ping(self):
        """验证 MCP initialize 与 ping 协议响应"""
        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"}
            }
        }
        resp = await self.server.handle_request(init_req)
        self.assertEqual(resp["id"], 1)
        self.assertEqual(resp["result"]["serverInfo"]["name"], "omniqq-mcp")
        self.assertIn("tools", resp["result"]["capabilities"])

        # Notification 不应返回响应
        notif_resp = await self.server.handle_request({
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        })
        self.assertIsNone(notif_resp)

        # Ping
        ping_resp = await self.server.handle_request({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "ping"
        })
        self.assertEqual(ping_resp["result"], {})

    async def test_02_tools_list_and_schemas(self):
        """验证默认 26 个核心/兼容工具及全量 82 个工具的注册、Annotations、OutputSchema 及 JSON Schema 合法性"""
        resp = await self.server.handle_request({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/list"
        })
        core_tools = resp["result"]["tools"]
        self.assertEqual(len(core_tools), 26)

        core_names = {t["name"] for t in core_tools}
        expected_core = {
            "check_llonebot_environment",
            "check_napcat_environment",
            "deploy_or_update_llonebot",
            "deploy_or_update_napcat",
            "list_qq_accounts",
            "login_new_qq_by_qrcode",
            "get_login_qrcode_image",
            "switch_qq_account",
            "start_llonebot_service",
            "start_napcat_service",
            "stop_llonebot_service",
            "stop_napcat_service",
            "send_private_msg",
            "send_group_msg",
            "delete_msg",
            "upload_private_file",
            "upload_group_file",
            "get_login_info",
            "get_group_list",
            "get_recent_messages",
            "get_recent_notices_and_requests",
            "clear_recent_messages_buffer",
            "call_llonebot_api",
            "call_napcat_api",
            "list_supported_llonebot_actions",
            "list_supported_napcat_actions"
        }
        self.assertEqual(core_names, expected_core)

        # 验证 OMNIQQ_TOOL_PROFILE=full 全量模式下暴露全部 82 个工具（含 8 个旧名兼容别名）
        with patch.dict(os.environ, {"OMNIQQ_TOOL_PROFILE": "full"}):
            full_resp = await self.server.handle_request({
                "jsonrpc": "2.0",
                "id": 30,
                "method": "tools/list"
            })
        all_tools = full_resp["result"]["tools"]
        self.assertEqual(len(all_tools), 82)

        all_names = {t["name"] for t in all_tools}
        self.assertTrue({
            "list_qq_accounts",
            "get_login_qrcode_image",
            "start_llonebot_service",
            "stop_llonebot_service",
            "restart_llonebot_service",
            "set_group_kick",
            "set_group_ban",
            "restart_napcat_service",
            "get_napcat_status",
        }.issubset(all_names))

        for t in core_tools:
            self.assertNotIn("restart_napcat_service", t["description"])

        for t in all_tools:
            self.assertTrue(t["description"], f"Tool {t['name']} 缺少描述")
            self.assertNotIn(":param ", t["description"], f"Tool {t['name']} 描述中残留重复的 :param 行")
            self.assertEqual(t["inputSchema"]["type"], "object")
            self.assertIn("properties", t["inputSchema"])
            for p_name, p_schema in t["inputSchema"]["properties"].items():
                self.assertTrue(p_schema.get("description"), f"Tool {t['name']} 参数 {p_name} 缺少 schema 描述")
            self.assertIn("outputSchema", t)
            self.assertEqual(t["outputSchema"]["type"], "object")
            self.assertIn("annotations", t)
            for hint in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"):
                self.assertIsInstance(t["annotations"].get(hint), bool)

    async def test_03_deploy_safety_gate(self):
        """验证 deploy_or_update_llonebot 在 confirmed=False 时严格拦截并返回预览"""
        resp = await self.server.handle_request({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "deploy_or_update_llonebot",
                "arguments": {
                    "action": "deploy",
                    "confirmed": False
                }
            }
        })
        self.assertFalse(resp["result"]["isError"])
        payload = json.loads(resp["result"]["content"][0]["text"])
        self.assertEqual(payload["status"], "confirmation_required")
        self.assertTrue(payload["requires_user_permission"])

    async def test_04_account_list_and_qrcode_tools(self):
        """验证多账号列表查询与二维码信息工具"""
        resp = await self.server.handle_request({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "list_qq_accounts",
                "arguments": {}
            }
        })
        payload = json.loads(resp["result"]["content"][0]["text"])
        self.assertEqual(payload["status"], "ok")
        self.assertIn("cached_accounts", payload)
        self.assertIn("default_account", payload)

        qr_resp = await self.server.handle_request({
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "get_login_qrcode_image",
                "arguments": {"open_image": False}
            }
        })
        qr_payload = json.loads(qr_resp["result"]["content"][0]["text"])
        self.assertIn(qr_payload["status"], ("ok", "not_found"))

    async def test_05_list_supported_actions(self):
        """验证 140+ 原生接口目录查询工具"""
        resp = await self.server.handle_request({
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "list_supported_llonebot_actions",
                "arguments": {"category": "all"}
            }
        })
        payload = json.loads(resp["result"]["content"][0]["text"])
        self.assertGreaterEqual(payload["total_count"], 125)
        self.assertIn("messaging", payload["categories"])
        self.assertIn("group_management", payload["categories"])

        # 验证 call_llonebot_api 遇到未知 action 时返回 list_supported_llonebot_actions 引导
        with patch.object(
            self.server.client,
            "call_api",
            AsyncMock(return_value={
                "status": "failed",
                "retcode": 1404,
                "data": None,
                "message": "不支持的Api no_such_action_v2",
                "wording": "不支持的Api no_such_action_v2",
            })
        ):
            bad_resp = await self.server.handle_request({
                "jsonrpc": "2.0",
                "id": 71,
                "method": "tools/call",
                "params": {
                    "name": "call_llonebot_api",
                    "arguments": {"action": "no_such_action_v2"}
                }
            })
        bad_payload = json.loads(bad_resp["result"]["content"][0]["text"])
        self.assertIn("list_supported_llonebot_actions", bad_payload.get("advice", ""))
        self.assertIn("list_supported_llonebot_actions", bad_payload.get("message", ""))

    async def test_05b_start_llonebot_and_export_qrcode_to_desktop(self):
        """验证启动 LLOneBot 时不误杀 QQ 进程，且检测到需要扫码登录时将二维码复制到桌面。"""
        import tempfile
        from pathlib import Path
        from unittest.mock import Mock
        from llonebot_mcp.config import DESKTOP_QR_FILENAME

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            install = tmp_path / "llbot"
            data_dir = install / "data"
            data_dir.mkdir(parents=True)
            (install / "llbot.js").write_bytes(b"entry")
            (install / "default_config.json").write_text('{"ob11":{"enable":true,"connect":[]}}',
                                                         encoding="utf-8")
            fake_qr = data_dir / "qrcode.png"
            desktop_dir = tmp_path / "Desktop"
            desktop_dir.mkdir()
            launched = {"called": False}

            def fake_launch(*args, **kwargs):
                launched["called"] = True
                fake_qr.write_bytes(b"PNG_QR_DATA_" * 20)
                return True, "started", Mock()

            with patch.multiple(
                "llonebot_mcp.client",
                is_port_listening=lambda *a, **kw: False,
                resolve_install_root=lambda: str(install),
                is_llbot_installed=lambda *a, **kw: True,
                ensure_onebot_token=lambda: "tok",
                get_current_account=lambda: "123456",
                ensure_onebot_config=lambda *a, **kw: True,
                launch_llonebot_process=fake_launch,
                find_qrcode_image=lambda *a, **kw: str(fake_qr) if fake_qr.exists() else None,
            ), patch("llonebot_mcp.config.get_desktop_dir", return_value=str(desktop_dir)):
                res = await self.server.client.start_llonebot_process(uin="123456", wait_seconds=2)

            expected_desktop_qr = str(desktop_dir / DESKTOP_QR_FILENAME)
            self.assertTrue(launched["called"])
            self.assertTrue(res["login_required"])
            self.assertEqual(res["desktop_qrcode_path"], expected_desktop_qr)
            self.assertTrue(os.path.exists(expected_desktop_qr))
            self.assertIn("桌面", res["message"])
            self.assertIn("扫描", res["message"])
            self.assertIn("webui_url", res)
            self.assertNotIn("killed_qq_processes", res)

    async def test_05c_legacy_tool_names_remain_callable(self):
        """验证 NapCat 旧工具名仍然可通过 tools/call 调用。"""
        resp = await self.server.handle_request({
            "jsonrpc": "2.0",
            "id": 55,
            "method": "tools/call",
            "params": {"name": "list_supported_napcat_actions", "arguments": {"category": "all"}}
        })
        payload = json.loads(resp["result"]["content"][0]["text"])
        self.assertGreaterEqual(payload["total_count"], 125)
        legacy = self.server.registry.get_tool("check_napcat_environment")
        self.assertEqual(legacy["compat_alias_of"], "check_llonebot_environment")

    async def test_06_message_buffer_and_hooks(self):
        """验证实时消息缓存池、CQ码净化、条件过滤与事件钩子机制"""
        buf = MessageBuffer(maxlen=50)
        hook_received = []
        buf.register_event_hook(lambda ev: hook_received.append(ev))

        # 模拟私聊事件
        buf.push_event({
            "post_type": "message",
            "message_id": 1001,
            "message_type": "private",
            "user_id": 10001,
            "sender": {"user_id": 10001, "nickname": "测试好友"},
            "message": [
                {"type": "text", "data": {"text": "请把汇总表发我"}},
                {"type": "file", "data": {"file": "模板.xlsx", "file_id": "/f123", "file_size": 10240}}
            ],
            "raw_message": "请把汇总表发我",
            "time": 1700000000
        })

        # 模拟群聊事件
        buf.push_event({
            "post_type": "message",
            "message_id": 1002,
            "message_type": "group",
            "group_id": 999888,
            "user_id": 10002,
            "sender": {"user_id": 10002, "card": "班长"},
            "message": [
                {"type": "at", "data": {"qq": "all", "name": "全体成员"}},
                {"type": "text", "data": {"text": "下午三点开班会"}}
            ],
            "raw_message": "[CQ:at,qq=all] 下午三点开班会",
            "time": 1700000010
        })

        self.assertEqual(len(hook_received), 2)

        all_msgs = buf.get_recent_messages(count=10)
        self.assertEqual(len(all_msgs), 2)
        self.assertIn("模板.xlsx", all_msgs[0]["clean_text"])
        self.assertIn("@全体成员", all_msgs[1]["clean_text"])

        # 按群号过滤
        grp_msgs = buf.get_recent_messages(chat_type="group", target_id=999888)
        self.assertEqual(len(grp_msgs), 1)
        self.assertEqual(grp_msgs[0]["message_id"], 1002)

        # 按关键词过滤
        kw_msgs = buf.get_recent_messages(keyword="汇总表")
        self.assertEqual(len(kw_msgs), 1)
        self.assertEqual(kw_msgs[0]["user_id"], 10001)

        # 验证异步协程事件钩子与 raw_event / target_id 字段
        async_received = []

        async def async_hook(ev):
            async_received.append(ev)

        buf.register_event_hook(async_hook)
        buf.push_event({
            "post_type": "message_sent",
            "message_id": 1003,
            "message_type": "private",
            "user_id": 10000,
            "target_id": 10001,
            "message": [{"type": "text", "data": {"text": "收到"}}],
            "raw_message": "收到",
            "time": 1700000020
        })
        import asyncio
        await asyncio.sleep(0.05)
        self.assertEqual(len(async_received), 1)
        self.assertEqual(async_received[0]["target_id"], 10001)
        self.assertIn("raw_event", async_received[0])

    async def test_07_stdio_subprocess_end_to_end(self):
        """验证通过子进程 stdio 兼容模式与 run_mcp.py 进行真实 JSON-RPC 交互"""
        run_script = os.path.join(PROJECT_ROOT, "llonebot_mcp", "run_mcp.py")
        proc = subprocess.Popen(
            [sys.executable, run_script, "--transport", "stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8"
        )
        try:
            init_line = json.dumps({
                "jsonrpc": "2.0",
                "id": 10,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"}
            }) + "\n"
            proc.stdin.write(init_line)
            proc.stdin.flush()

            out_line = proc.stdout.readline()
            self.assertTrue(out_line)
            resp = json.loads(out_line)
            self.assertEqual(resp["id"], 10)
            self.assertEqual(resp["result"]["serverInfo"]["name"], "omniqq-mcp")
        finally:
            proc.terminate()
            proc.wait(timeout=5)
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                if pipe:
                    pipe.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
