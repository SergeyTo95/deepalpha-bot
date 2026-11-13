"""Inspect or explicitly change ONE sandbox subscription; never touches live.

Run from the repository root with python -m scripts.velia_creem_plan_change_probe.
Supply VELIA_CREEM_TEST_API_KEY through the environment, never CLI arguments.
"""
import argparse
import json
import os
import sys

from services.velia_creem_billing import BillingError
from services.velia_creem_plan_change import BEHAVIORS, SandboxAPI, discover_sandbox, probe_change, snapshot


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--subscription')
    action.add_argument('--discover', action='store_true')
    action.add_argument('--predeploy-discover', action='store_true')
    parser.add_argument('--target-product')
    parser.add_argument('--behavior', choices=sorted(BEHAVIORS))
    parser.add_argument('--apply-test-change', action='store_true')
    args = parser.parse_args(argv)
    if not args.subscription and (args.apply_test_change or args.target_product or args.behavior):
        parser.error('discovery is read-only and cannot change a subscription')
    if args.apply_test_change and not (args.target_product and args.behavior):
        parser.error('--apply-test-change requires --target-product and --behavior')
    if not args.apply_test_change and (args.target_product or args.behavior):
        parser.error('target/behavior require explicit --apply-test-change')
    prefix = 'VELIA_CREEM_SANDBOX_DISCOVERY ' if args.predeploy_discover else ''
    try:
        key = os.getenv('VELIA_CREEM_TEST_API_KEY', '')
        if args.predeploy_discover and not key:
            print(prefix+json.dumps({'ok':True, 'skipped':True, 'reason':'test_api_key_not_configured'}))
            return 0
        api = SandboxAPI(key)
        if args.discover or args.predeploy_discover:
            result = discover_sandbox(api)
        else:
            result = (probe_change(api, args.subscription, args.target_product, args.behavior)
                      if args.apply_test_change else {'mode': 'test', 'snapshot': snapshot(api, args.subscription)})
        print(prefix+json.dumps(result, ensure_ascii=False, indent=None if prefix else 2))
        return 0
    except BillingError as error:
        print(prefix+json.dumps({'ok': False, 'error': error.code,
                               'outcome': 'inspect_subscription_before_retry' if args.apply_test_change else 'read_failed'}))
        # Optional sandbox discovery must never take down working live checkout.
        # This exit policy does not weaken any mandatory production CI check.
        return 0 if args.predeploy_discover else 1
    except Exception:
        print(prefix+json.dumps({'ok':False, 'error':'sandbox_probe_unavailable'}))
        return 0 if args.predeploy_discover else 1


if __name__ == '__main__':
    sys.exit(main())
