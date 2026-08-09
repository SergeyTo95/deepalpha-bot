from pathlib import Path

path = Path('bot/admin.py')
text = path.read_text(encoding='utf-8')
old = '''            for r in found_rows:\n                lines.append(f"id={r[0]} address={_mask_ton_admin(r[1])} status={r[4] or 'unknown'} balance={r[5] or 0} created_at={r[6] or '—'} seed_reveal_used={bool(r[7])}")\n            if len(found_rows) > 1:\n'''
new = '''            for r in found_rows:\n                lines.append(f"id={r[0]} address={_mask_ton_admin(r[1])} status={r[4] or 'unknown'} balance={r[5] or 0} created_at={r[6] or '—'} seed_reveal_used={bool(r[7])}")\n                lines.append(f"Status: {r[4] or 'unknown'}")\n            if len(found_rows) > 1:\n'''
if text.count(old) != 1:
    raise RuntimeError(f'expected one status block, got {text.count(old)}')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
print('Gram admin status compatibility restored')
