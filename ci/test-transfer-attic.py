import importlib.util
import contextlib
import errno
import io
import gzip
import subprocess
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

spec = importlib.util.spec_from_file_location('transfer', Path(__file__).with_name('transfer-attic.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TransferSafetyTests(unittest.TestCase):
    def test_transfer_limit_is_bounded_at_eight_gib(self):
        for value, expected in [('6144', 0), ('8192', 0), ('8193', 1)]:
            with self.subTest(value=value), patch.dict(os.environ, {'ATTIC_MAX_CLOSURE_MIB': value}), \
                    patch.object(module, 'transfer') as transfer, \
                    patch('sys.argv', ['transfer', 'export', '--directory', '/tmp/fixture']), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(module.main(), expected)
                if expected:
                    transfer.assert_not_called()
                else:
                    self.assertEqual(transfer.call_args.args[2], int(value) * 1024 * 1024)

    def test_transfer_failure_categories_do_not_expose_details(self):
        for error, expected in [(module.TransferSizeExceeded('secret'), 'exceeded ATTIC_MAX_CLOSURE_MIB'),
                                (OSError(errno.ENOSPC, 'secret'), 'disk space or quota'),
                                (ValueError('secret'), 'transfer failed')]:
            with self.subTest(error=error), patch.object(module, 'transfer', side_effect=error), \
                    patch('sys.argv', ['transfer', 'export', '--directory', '/tmp/fixture']), \
                    contextlib.redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(module.main(), 1)
                self.assertIn(expected, stderr.getvalue())
                self.assertNotIn('secret', stderr.getvalue())

    def test_oversize_stream_is_rejected_before_excess_is_written(self):
        target = io.BytesIO()
        with self.assertRaises(ValueError): module.copy_bounded(io.BytesIO(b'x' * 1025), target, 1024)
        self.assertEqual(target.getvalue(), b'')

    def test_bounded_stream_round_trips(self):
        target = io.BytesIO()
        self.assertEqual(module.copy_bounded(io.BytesIO(b'public fixture'), target, 100), 14)
        self.assertEqual(target.getvalue(), b'public fixture')

    def test_manifest_excludes_non_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / 'outputs.txt'
            for value in ['', '/tmp/credential', '/nix/store/' + 'a' * 32 + '-file.drv', '/nix/store/' + 'a' * 32 + '-file/child']:
                manifest.write_text(value)
                with self.subTest(value=value), self.assertRaises(ValueError): module.roots(manifest)

    def test_manifest_preserves_only_unique_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / 'outputs.txt';value='/nix/store/'+'a'*32+'-public'
            manifest.write_text(value+'\n'+value+'\n')
            self.assertEqual(module.roots(manifest), [value])

    def test_import_restores_official_signatures_before_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            root = '/nix/store/' + 'a' * 32 + '-custom-package'
            (directory / 'outputs.txt').write_text(root + '\n')
            with gzip.open(directory / 'closure.nar.gz', 'wb') as stream:
                stream.write(b'public data fixture')
            process = MagicMock()
            process.stdin = io.BytesIO()
            process.wait.return_value = 0
            process.poll.return_value = 0
            with patch.dict(os.environ, {}, clear=True), \
                    patch.object(module.subprocess, 'Popen', return_value=process), \
                    patch.object(module.subprocess, 'run') as call, patch.object(module.policy, 'runtime', return_value=[root]):
                module.transfer('import', directory, 1024)
            self.assertEqual(call.call_args_list[0].args[0], ['nix-store', '--check-validity', root])
            self.assertEqual(call.call_args_list[1].args[0],
                             ['nix', 'store', 'copy-sigs', '--substituter',
                              'https://cache.nixos.org', '--recursive', root])
            self.assertTrue(call.call_args_list[1].kwargs['check'])
            self.assertEqual(call.call_args_list[1].kwargs['timeout'], 120)

    def test_forbidden_runtime_never_starts_export_or_creates_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'artifact'
            with patch.dict(os.environ, {}, clear=True), \
                    patch.object(module, 'roots', return_value=['/nix/store/'+'a'*32+'-root']), \
                    patch.object(module.policy, 'runtime', side_effect=ValueError('blocked')), \
                    patch.object(module.subprocess, 'Popen') as process:
                with self.assertRaises(ValueError): module.transfer('export', directory, 1024)
                process.assert_not_called()
                self.assertFalse((directory / 'closure.nar.gz').exists())

    def test_transfer_refuses_upload_credentials(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {'ATTIC_TOKEN':'synthetic-fixture'}):
            with self.assertRaises(ValueError): module.transfer('export', Path(temporary), 1024)

    def test_workflow_token_is_confined_to_the_isolated_publisher(self):
        import re
        for path in Path(__file__).resolve().parents[1].joinpath('.github/workflows').glob('*actions.yml'):
            with self.subTest(workflow=path.name):
                sections = re.split(r'(?m)^  ([A-Za-z_][A-Za-z0-9_-]*):\n', path.read_text().split('\njobs:\n', 1)[1])[1:]
                jobs = dict(zip(sections[::2], sections[1::2]))
                self.assertEqual(set(jobs), {'plan', 'dependencies', 'publish-dependencies', 'build', 'publish'})
                for name, job in jobs.items():
                    if name not in {'publish', 'publish-dependencies'}:
                        lines = job.splitlines()
                        for index, line in enumerate(lines):
                            if 'secrets.ATTIC_TOKEN' in line:
                                preceding = '\n'.join(lines[max(0, index-5):index])
                                self.assertIn("github.event_name == 'push'", preceding)
                                self.assertIn('github.event.repository.default_branch', preceding)
                        continue
                    self.assertEqual(job.count('secrets.ATTIC_TOKEN'), 1)
                    dependency = 'dependencies' if name == 'publish-dependencies' else 'build'
                    self.assertIn('needs: [plan, ' + dependency + ']', job)
                    self.assertIn('--max-jobs 0 --builders', job)
                    self.assertIn('github.event.repository.default_branch', job)
                    self.assertIn("github.event_name == 'push'", re.sub(r"\s+", " ", job))
                    self.assertIn("github.event_name == 'workflow_dispatch'", re.sub(r"\s+", " ", job))
                    self.assertLess(job.index('Install the trusted pinned Attic client'), job.index('ci/transfer-attic.py import'))
                    self.assertLess(job.index('ci/transfer-attic.py import'), job.index('secrets.ATTIC_TOKEN'))
                    self.assertNotIn('Evaluate and build the selected native package', job)


if __name__ == '__main__': unittest.main()
