#!/usr/bin/env python3
"""Describe missing Prometheus evaluations without filling or changing samples."""
import argparse
import hashlib
import json
import math
from pathlib import Path


def coverage(source):
    data = json.loads(source)
    start, end, step = (data[k] for k in ('start_unix', 'end_unix', 'step_seconds'))
    if (any(type(v) is not int for v in (start, end, step))
            or not 0 < step <= 15 or not start < end
            or (end - start) // step > 200000):
        raise ValueError('Invalid evaluation interval')
    grid = list(range(start, end + 1, step))
    expected = set(grid)
    gaps, identities = [], set()
    if not data.get('series'):
        raise ValueError('No hardware series')
    for group, series in data['series'].items():
        if not series:
            raise ValueError('Empty hardware group')
        for item in series:
            labels, values = item['labels'], item['values']
            identity = (group, json.dumps(labels, sort_keys=True))
            if identity in identities or not values:
                raise ValueError('Duplicate or empty series')
            identities.add(identity)
            if any(type(v) not in (int, float) or not math.isfinite(v)
                   for pair in values for v in pair):
                raise ValueError('Non-finite sample')
            times = [pair[0] for pair in values]
            if (len(times) != len(set(times)) or times != sorted(times)
                    or not set(times) <= expected):
                raise ValueError('Samples do not follow the evaluation grid')
            present = set(times)
            missing = sorted(expected - present)
            if missing:
                gaps.append({'group': group, 'labels': labels,
                             'missing_evaluations': len(missing),
                             'missing_timestamps_unix': missing})
    return {
        'source_sha256': hashlib.sha256(source).hexdigest(),
        'start_unix': start, 'end_unix': end, 'step_seconds': step,
        'series_count': len(identities),
        'fully_covered': not gaps,
        'affected_series': len(gaps),
        'missing_evaluations': sum(g['missing_evaluations'] for g in gaps),
        'gaps': gaps,
        'meaning': 'Evaluation-grid coverage, not continuous observation or source scrape cadence. Missing samples are unknown, never interpolated.',
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hardware', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    report = coverage(args.hardware.read_bytes())
    if args.check:
        if json.loads(args.output.read_text()) != report:
            raise SystemExit('Hardware coverage or source hash differs')
    else:
        with args.output.open('x') as target:
            json.dump(report, target, ensure_ascii=False, indent=2, allow_nan=False)
            target.write('\n')
    print('Hardware coverage:', report['affected_series'], 'affected series,',
          report['missing_evaluations'], 'missing evaluations')
