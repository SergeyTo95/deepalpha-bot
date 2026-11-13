"""Inspect or explicitly change ONE sandbox subscription; never touches live.

Run from the repository root with python -m scripts.velia_creem_plan_change_probe.
Supply VELIA_CREEM_TEST_API_KEY through the environment, never CLI arguments.
"""
import argparse
import json
import os
import sys

from services.velia_creem_billing import BillingError
from services.velia_creem_plan_change import BEHAVIORS, SandboxAPI, probe_change, snapshot


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--subscription', required=True)
    parser.add_argument('--target-product')
    parser.add_argument('--behavior', choices=sorted(BEHAVIORS))
    parser.add_argument('--apply-test-change', action='store_true')
    args = parser.parse_args(argv)
    if args.apply_test_change and not (args.target_product and args.behavior):
        parser.error('--apply-test-change requires --target-product and --behavior')
    if not args.apply_test_change and (args.target_product or args.behavior):
        parser.error('target/behavior require explicit --apply-test-change')
    try:
        api = SandboxAPI(os.getenv('VELIA_CREEM_TEST_API_KEY', ''))
        result = (probe_change(api, args.subscription, args.target_product, args.behavior)
                  if args.apply_test_change else {'mode': 'test', 'snapshot': snapshot(api, args.subscription)})
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except BillingError as error:
        print(json.dumps({'ok': False, 'error': error.code,
                          'outcome': 'inspect_subscription_before_retry' if args.apply_test_change else 'read_failed'}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
