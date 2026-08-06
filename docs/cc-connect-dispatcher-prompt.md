# cc-connect dispatcher — expand request handling

When a user replies in Feishu with something that looks like an expand
request, the cc-connect Claude session should:

1. Call the helper to check whether it's an expand request:

   ```bash
   python -c "
   import sys; sys.path.insert(0, '/path/to/goal-scheduler/scripts')
   import dispatcher
   is_exp, tid = dispatcher.is_expand_request('''$USER_TEXT''')
   print(is_exp, tid)
   "
   ```

2. If the helper returns `(True, task_id)`:
   - Run `python /path/to/goal-scheduler/scripts/cli.py task show <task_id>` to read `description`.
   - Read the goal context if helpful: `python .../cli.py goal show <goal_slug>`.
   - Generate a 10-30 line expansion: WHY each step matters, code example, common pitfalls, verification checklist.
   - Send as a plain Feishu message (NOT as a new timer — the cc-connect `timer add` is for scheduling, not for ad-hoc messages).

3. If the helper returns `(False, None)`: fall through to existing dispatcher logic (status update / focus change / general chat).

## Trigger words (closed set)

`展开` / `详细` / `怎么做`. Anything else: not an expand request.

## Examples

| User says | Helper returns | Dispatcher action |
|---|---|---|
| `T001 展开` | `(True, 'T001')` | expand T001 |
| `展开 T001` | `(True, 'T001')` | expand T001 |
| `T001 详细` | `(True, 'T001')` | expand T001 |
| `T001 怎么做` | `(True, 'T001')` | expand T001 |
| `remotion-finance-T001 展开` | `(True, 'remotion-finance-T001')` | expand T001 (resolves to same DB row) |
| `T001 完成了` | `(False, None)` | mark T001 done (existing branch) |
| `今天天气怎么样` | `(False, None)` | general chat |

(Substitute the actual path `/path/to/goal-scheduler/` for the user's install path when integrating.)