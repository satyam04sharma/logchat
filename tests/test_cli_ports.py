import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import typer

from cli.main import base_url, up


class LocalPortTests(unittest.TestCase):
    def stack(self, contents=None):
        temporary=tempfile.TemporaryDirectory();root=Path(temporary.name)
        (root/'compose.yaml').write_text('services: {}\n')
        if contents is not None:
            (root/'.logchat').mkdir();(root/'.logchat/.secrets').write_text(contents)
        self.addCleanup(temporary.cleanup)
        return root

    def test_reads_stack_ports_without_using_project_working_directory(self):
        stack=self.stack('POSTGRES_PASSWORD=not-read-by-parser\nAPI_PORT=8081\nWEB_PORT=3001\n')
        with tempfile.TemporaryDirectory() as project, patch.dict(os.environ,{'LOGCHAT_STACK_DIR':str(stack)},clear=True), patch('os.getcwd',return_value=project):
            Path(project,'.logchat').mkdir();Path(project,'.logchat/.secrets').write_text('API_PORT=9999\nWEB_PORT=9998\n')
            self.assertEqual(base_url(),'http://127.0.0.1:8081')

    def test_explicit_local_api_override_wins(self):
        stack=self.stack('API_PORT=8081\n')
        with patch.dict(os.environ,{'LOGCHAT_STACK_DIR':str(stack),'LOGCHAT_API_URL':'http://localhost:9090/'},clear=True):
            self.assertEqual(base_url(),'http://localhost:9090')

    def test_rejects_remote_or_malformed_api_override(self):
        for value in ('https://example.com:8080','http://user@localhost:8080','http://localhost:70000','ftp://localhost:8080',''):
            with self.subTest(value=value),patch.dict(os.environ,{'LOGCHAT_API_URL':value},clear=True):
                with self.assertRaises(typer.BadParameter):base_url()

    def test_rejects_invalid_stack_ports(self):
        for value in ('0','65536','eight','-1'):
            stack=self.stack('API_PORT='+value+'\n')
            with self.subTest(value=value),patch.dict(os.environ,{'LOGCHAT_STACK_DIR':str(stack)},clear=True):
                with self.assertRaises(typer.BadParameter):base_url()

    def test_up_starts_headless_stack(self):
        stack=self.stack('API_PORT=8081\n')
        completed=type('Completed',(),{'returncode':0})()
        with patch.dict(os.environ,{'LOGCHAT_STACK_DIR':str(stack)},clear=True), \
             patch('cli.main.subprocess.run',return_value=completed) as run,patch('cli.main._docker_environment',return_value={}):
            up(docker=True)
        commands=[call.args[0] for call in run.call_args_list]
        self.assertTrue(any('--profile' in args and 'docker' in args for args in commands))
        self.assertFalse(any('web' in args for args in commands))


if __name__=='__main__':unittest.main()
