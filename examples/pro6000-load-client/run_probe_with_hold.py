"""Bound a diagnostic process and retain its pod for evidence collection.

This is not a daemon, retry loop, or workflow controller. The Kubernetes Job
must also have activeDeadlineSeconds; after the bounded hold this wrapper exits
with the diagnostic's own exit status.
"""
import argparse
import json
import subprocess
import time


def bounded_run(command, timeout):
    process = subprocess.Popen(command)
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return 124


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=int, required=True)
    parser.add_argument('--hold', type=int, default=3600)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command or not 1 <= args.timeout <= 7200 or not 0 <= args.hold <= 3600:
        parser.error('Require command, timeout1..7200, hold0..3600')
    started = time.time()
    code = bounded_run(command, args.timeout)
    print(json.dumps({'event': 'diagnostic_finished_collect_evidence', 'exit_code': code,
                      'started_at': started, 'finished_at': time.time(),
                      'hold_seconds': args.hold}), flush=True)
    time.sleep(args.hold)
    raise SystemExit(code)


if __name__ == '__main__':
    main()
