"""Docker 默认部署模式回归测试（不依赖真实 Docker 守护进程）。"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from napcat_mcp import config, docker_runtime
from napcat_mcp.client import NapCatClient
from napcat_mcp.deployer import check_napcat_environment, deploy_or_update_napcat


class DockerConfigTests(unittest.TestCase):
    def test_default_deployment_mode_is_docker(self):
        self.assertEqual(config.DEPLOY_MODE, 'docker')
        self.assertTrue(config.is_docker_mode())
        self.assertIn(os.path.join('docker', 'napcat', 'config'), config.NAPCAT_CONFIG_DIR)
        self.assertIn(os.path.join('docker', 'napcat', 'cache'), config.QR_CACHE_IMAGE)


class DockerDeployTests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_requires_confirmation_without_docker_calls(self):
        with patch('napcat_mcp.deployer.fetch_github_latest_release',
                   AsyncMock(return_value={'latest_tag': 'v4.18.42'})), \
             patch('napcat_mcp.deployer.is_docker_mode', return_value=True), \
             patch('napcat_mcp.deployer.docker_runtime.docker_available') as available, \
             patch('napcat_mcp.deployer.docker_runtime.deploy_container') as deploy:
            result = await deploy_or_update_napcat(action='deploy', confirmed=False)
        self.assertEqual(result['status'], 'confirmation_required')
        self.assertTrue(result['requires_user_permission'])
        self.assertEqual(result['deployment_mode'], 'docker')
        self.assertEqual(result['container_name'], docker_runtime.CONTAINER_NAME)
        self.assertEqual(result['ports']['http'], config.DEFAULT_HTTP_PORT)
        self.assertIn('/app/napcat', result['volumes']['napcat']['container'])
        available.assert_not_called()
        deploy.assert_not_called()

    async def test_confirmed_deploy_writes_config_and_starts_container(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / 'data'
            qq_dir = root / 'qq'
            qr_path = data_dir / 'cache' / 'qrcode.png'
            with patch('napcat_mcp.deployer.fetch_github_latest_release',
                       AsyncMock(return_value={'latest_tag': 'v4.18.42'})), \
                 patch('napcat_mcp.deployer.is_docker_mode', return_value=True), \
                 patch('napcat_mcp.deployer.docker_runtime.docker_available', return_value=True), \
                 patch('napcat_mcp.deployer.docker_runtime.deploy_container',
                       return_value={'status': 'success', 'ports': {'http': 3000, 'ws': 3001, 'webui': 6099}}) as deploy, \
                 patch('napcat_mcp.deployer.ensure_onebot_config', return_value=True) as write_config, \
                 patch('napcat_mcp.deployer.check_installed_runtime', return_value={'deployment_mode': 'docker'}), \
                 patch.multiple('napcat_mcp.deployer', DOCKER_DATA_DIR=str(data_dir),
                                DOCKER_QQ_DIR=str(qq_dir), QR_CACHE_IMAGE=str(qr_path)):
                result = await deploy_or_update_napcat(action='update', confirmed=True, target_uin='123456')
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['deployment_mode'], 'docker')
        write_config.assert_called_once_with('123456')
        deploy.assert_called_once()
        args = deploy.call_args.args
        self.assertEqual(args[0], str(data_dir))
        self.assertEqual(args[1], str(qq_dir))

    async def test_confirmed_deploy_fails_when_docker_is_unavailable(self):
        with patch('napcat_mcp.deployer.fetch_github_latest_release', AsyncMock(return_value={})), \
             patch('napcat_mcp.deployer.is_docker_mode', return_value=True), \
             patch('napcat_mcp.deployer.docker_runtime.docker_available', return_value=False):
            result = await deploy_or_update_napcat(action='deploy', confirmed=True)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('Docker', result['error'])

    async def test_environment_reports_docker_runtime(self):
        status = {'name': 'napcat-mcp', 'exists': True, 'running': True, 'image': 'mlikiowa/napcat-docker:latest'}
        with patch('napcat_mcp.deployer.is_docker_mode', return_value=True), \
             patch('napcat_mcp.deployer.docker_runtime.docker_available', return_value=True), \
             patch('napcat_mcp.deployer.docker_runtime.container_status', return_value=status), \
             patch('napcat_mcp.deployer.docker_runtime.image_exists', return_value=True), \
             patch('napcat_mcp.deployer.docker_runtime.docker_version', return_value='27.0.0'), \
             patch('napcat_mcp.deployer.get_local_napcat_version', return_value={}), \
             patch('napcat_mcp.deployer.is_port_listening', return_value=True), \
             patch('napcat_mcp.deployer.fetch_github_latest_release', AsyncMock(return_value={})):
            result = await check_napcat_environment()
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['runtime']['deployment_mode'], 'docker')
        self.assertTrue(result['runtime']['container_running'])
        self.assertEqual(result['runtime']['docker_version'], '27.0.0')


class DockerRuntimeCommandTests(unittest.TestCase):
    def test_create_container_builds_expected_ports_and_volumes(self):
        completed = Mock(returncode=0, stdout='', stderr='')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir, qq_dir = str(root / 'napcat'), str(root / 'qq')
            with patch('napcat_mcp.docker_runtime.container_exists', return_value=False), \
                 patch('napcat_mcp.docker_runtime._run', return_value=completed) as run:
                result = docker_runtime.create_container(data_dir, qq_dir, 3000, 3001, 6099)
        self.assertEqual(result['status'], 'created')
        command = run.call_args.args[0]
        self.assertIn('-p', command)
        self.assertIn('127.0.0.1:3000:3000', command)
        self.assertIn('127.0.0.1:3001:3001', command)
        self.assertIn(f"{data_dir.replace(chr(92), '/')}:/app/napcat", command)
        self.assertIn(f"{qq_dir.replace(chr(92), '/')}:/app/.config/QQ", command)

    def test_deploy_container_pulls_and_recreates(self):
        with patch('napcat_mcp.docker_runtime.pull_image', return_value={'status': 'pulled'}) as pull, \
             patch('napcat_mcp.docker_runtime.remove_container', return_value=True) as remove, \
             patch('napcat_mcp.docker_runtime.create_container', return_value={'status': 'created'}) as create, \
             patch('napcat_mcp.docker_runtime.start_container', return_value={'status': 'started'}) as start:
            result = docker_runtime.deploy_container('/tmp/napcat', '/tmp/qq')
        self.assertEqual(result['status'], 'success')
        pull.assert_called_once()
        remove.assert_called_once()
        create.assert_called_once()
        start.assert_called_once()

    def test_local_version_falls_back_to_configured_image(self):
        with patch('napcat_mcp.docker_runtime.container_image', return_value=''), \
             patch('napcat_mcp.docker_runtime.container_status',
                   return_value={'exists': False, 'running': False, 'image': ''}):
            version = docker_runtime.local_version()
        self.assertEqual(version['deployment_mode'], 'docker')
        self.assertEqual(version['image'], docker_runtime.IMAGE)


class DockerClientStartTests(unittest.IsolatedAsyncioTestCase):
    async def test_docker_client_start_reports_running_container(self):
        client = NapCatClient(token='test', auto_heal=False)
        with patch('napcat_mcp.client.is_docker_mode', return_value=True), \
             patch('napcat_mcp.client.is_port_listening', return_value=True):
            result = await client.start_napcat_process()
        self.assertEqual(result['status'], 'already_running')
        self.assertEqual(result['deployment_mode'], 'docker')
        await client.close()

    async def test_docker_client_start_reports_missing_docker(self):
        client = NapCatClient(token='test', auto_heal=False)
        with patch('napcat_mcp.client.is_docker_mode', return_value=True), \
             patch('napcat_mcp.client.is_port_listening', return_value=False), \
             patch('napcat_mcp.docker_runtime.docker_available', return_value=False):
            result = await client.start_napcat_process()
        self.assertEqual(result['status'], 'not_installed')
        self.assertIn('Docker', result['error'])
        await client.close()


if __name__ == '__main__':
    unittest.main()
