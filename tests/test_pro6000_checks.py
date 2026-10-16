"""Keep functional chat/replay checks separate from long-output capacity claims."""
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'results/pro6000-heavy'


class FunctionalChecks(unittest.TestCase):
    def test_ram_replay_is_bound_to_completed_capacity_but_not_32k_output(self):
        replay = json.loads((DATA / 'gemma-cache-offload-replay.json').read_text())
        client = json.loads((DATA / 'gemma-cache-262144ctx-client.json').read_text())
        summary = json.loads((DATA / 'gemma-cache-262144ctx-summary.json').read_text())
        self.assertEqual(replay['capacity_run_id'], client['run_id'])
        for key, file in [('capacity_client_sha256', 'gemma-cache-262144ctx-client.json'),
                          ('runtime_sha256', 'gemma-cache-262144ctx-runtime.json')]:
            self.assertEqual(replay[key], hashlib.sha256((DATA / file).read_bytes()).hexdigest())
        self.assertEqual(summary['completed'], 10)
        self.assertGreater(replay['started_at'], max(r['completed_at'] for r in summary['requests']))
        self.assertLess(replay['started_at'], replay['ended_at'])
        self.assertEqual(replay['usage']['completion_tokens'], 1)
        self.assertEqual(replay['usage']['prompt_tokens'], summary['input_tokens_each'])
        self.assertEqual(replay['usage']['total_tokens'], summary['input_tokens_each'] + 1)
        delta = replay['cpu_to_gpu_bytes_after'] - replay['cpu_to_gpu_bytes_before']
        self.assertGreater(delta, 0)
        self.assertEqual(delta, replay['cpu_to_gpu_bytes_delta'])
        self.assertTrue(replay['restore_proved'])
        self.assertIn('not a repeat of the 32K-output series', ' '.join(replay['limitations']))

    def test_real_chat_image_and_limits_are_preserved(self):
        receipt = json.loads((DATA / 'webui-knowledge-receipt.json').read_text())
        image = DATA / receipt['screenshot']
        self.assertEqual(receipt['screenshot_sha256'], hashlib.sha256(image.read_bytes()).hexdigest())
        self.assertTrue(image.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertTrue(receipt['done'])
        self.assertTrue(receipt['ordinary_password_account'])
        self.assertTrue(receipt['knowledge_and_attachment_citations'])
        self.assertEqual(receipt['source_containers'], 2)
        self.assertEqual(receipt['retrieved_chunks_shown_in_ui'], 6)
        self.assertEqual(receipt['usage']['total_tokens'],
                         receipt['usage']['prompt_tokens'] + receipt['usage']['completion_tokens'])
        self.assertLess(receipt['usage']['completion_tokens'], 32768)
        limits = ' '.join(receipt['limitations'])
        self.assertIn('not a separately tested external network', limits)
        self.assertIn('does not establish the final public ACL', limits)
        self.assertFalse(any(k in receipt for k in ('token', 'key', 'password', 'chat_id', 'participant_user_id')))


if __name__ == '__main__':
    unittest.main()
