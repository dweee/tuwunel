import importlib.util
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
                    patch.object(module.subprocess, 'run') as call:
                module.transfer('import', directory, 1024)
            self.assertEqual(call.call_args_list[0].args[0], ['nix-store', '--check-validity', root])
            self.assertEqual(call.call_args_list[1].args[0],
                             ['nix', 'store', 'copy-sigs', '--substituter',
                              'https://cache.nixos.org', '--recursive', root])
            self.assertTrue(call.call_args_list[1].kwargs['check'])
            self.assertEqual(call.call_args_list[1].kwargs['timeout'], 120)

    def test_transfer_refuses_upload_credentials(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {'ATTIC_TOKEN':'synthetic-fixture'}):
            with self.assertRaises(ValueError): module.transfer('export', Path(temporary), 1024)

    def test_workflow_token_is_confined_to_the_isolated_publisher(self):
        for path in Path(__file__).resolve().parents[1].joinpath('.github/workflows').glob('*actions.yml'):
            with self.subTest(workflow=path.name):
                build, separator, publisher = path.read_text().partition('\n  publish:\n')
                self.assertTrue(separator)
                self.assertNotIn('secrets.ATTIC_TOKEN', build)
                self.assertEqual(publisher.count('secrets.ATTIC_TOKEN'),1)
                self.assertIn('needs: [plan, build]', publisher)
                self.assertIn('--max-jobs 0 --builders', publisher)
                self.assertIn('github.event.repository.default_branch', publisher)
                self.assertLess(publisher.index('Install the trusted pinned Attic client'), publisher.index('ci/transfer-attic.py import'))
                self.assertLess(publisher.index('ci/transfer-attic.py import'), publisher.index('secrets.ATTIC_TOKEN'))
                self.assertNotIn('Evaluate and build the selected native package', publisher)


if __name__ == '__main__': unittest.main()
