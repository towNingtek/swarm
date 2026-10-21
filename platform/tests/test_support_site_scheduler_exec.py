import subprocess
import unittest
from unittest import mock

import common
import support_site_scheduler as scheduler


class DockerRunTargetsTheSiteContainer(unittest.TestCase):
    """Scheduled runs must exec into the container the site engine created."""

    def _exec_target(self):
        done = subprocess.CompletedProcess([], 0, stdout='done', stderr='')
        with mock.patch.object(scheduler.subprocess, 'run', return_value=done) as run:
            ok, answer = scheduler.SiteScheduler._docker_run('acme', 'prompt')
        self.assertTrue(ok)
        self.assertEqual(answer, 'done')
        argv = run.call_args.args[0]
        return argv[argv.index('-w') + 2]

    def test_uses_the_engine_container_name(self):
        self.assertEqual(self._exec_target(), common.container_name('acme'))

    def test_follows_the_configured_prefix(self):
        with mock.patch.object(common, 'CONTAINER_PREFIX', 'custom-'):
            self.assertEqual(self._exec_target(), 'custom-acme')

    def test_bad_site_name_never_reaches_docker(self):
        with mock.patch.object(scheduler.subprocess, 'run') as run:
            ok, _ = scheduler.SiteScheduler._docker_run('../etc', 'prompt')
        self.assertFalse(ok)
        run.assert_not_called()

    def test_scheduled_model_is_an_extra_patch_and_is_removed(self):
        done = subprocess.CompletedProcess([], 0, stdout='done', stderr='')
        with mock.patch.object(scheduler.subprocess, 'run', return_value=done) as run:
            ok, _ = scheduler.SiteScheduler._docker_run('acme', 'prompt', model=('nvidia', 'm/x'))
        self.assertTrue(ok)
        calls = [c.args[0] for c in run.call_args_list]
        put, main, rm = calls[0], calls[1], calls[2]
        overlay = put[-1]
        self.assertTrue(overlay.startswith('/tmp/swarm-schedule-model-'))
        self.assertIn("provider: 'nvidia'", run.call_args_list[0].kwargs['input'])
        self.assertEqual(main[main.index(overlay) - 1], '--patch')          # layered after the site profile
        self.assertEqual(main.count('--patch'), 2)
        self.assertEqual(rm[-3:], ['rm', '-f', overlay])                    # cleaned up


if __name__ == '__main__':
    unittest.main()
