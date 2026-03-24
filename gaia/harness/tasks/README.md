# GAIA harness tasks

Store JSON task registries here when you want a local, file-based set of one-shot runs.

Canonical registry shape:

```json
{
  "version": 1,
  "suite_id": "local-smoke",
  "suite_name": "local-smoke",
  "grader_configs": {
    "reason_codes": {
      "forbidden_reason_codes": ["user_intervention_missing"]
    }
  },
  "tasks": [
    {
      "id": "login-flow",
      "url": "https://example.com",
      "goal": "Log in and reach the dashboard",
      "metadata": {
        "owner": "qa"
      },
      "grader_configs": {
        "status": {
          "expected_statuses": ["passed"]
        }
      }
    }
  ]
}
```

Accepted aliases when loading:

- `tasks` or `scenarios` for the top-level array
- `suite_id` for the registry id
- `metadata` or `suite_metadata` for suite-level metadata
- `metadata` or `task_metadata` for task-level metadata
- `grader_configs`, `graders`, or `grader_overrides` for grader config data
- `id`, `task_id`, or `name` for the task id
- `url` or `start_url` for the target URL
- `goal`, `query`, or `scenario` for the one-shot prompt

Any extra top-level fields are preserved as suite metadata and passed through
to the runner. Suite-level `grader_configs` are inherited by every task in the
suite, and task-level grader config fields override the inherited defaults.

Legacy built-in suite files that still use `harness.tags`, `harness.graders`,
or `harness.grader_overrides` are still accepted.

Common service-task grader patterns:

```json
{
  "grader_configs": {
    "membership": {
      "expected_present": true,
      "destination_terms": ["위시리스트"]
    },
    "blocked_vs_fail": {
      "allowed_blocked_statuses": ["blocked_user_action"],
      "allowed_blocked_markers": ["사용자 개입", "captcha", "로그인"],
      "forbidden_fail_markers": ["timeout", "exception", "request_exception"]
    }
  }
}
```

Notes:

- `membership` is best for add/remove/apply tasks where the destination surface is explicit
  such as `위시리스트`, `시간표`, or `내 시간표`.
- `blocked_vs_fail` is additive. It does not replace the default `status` grader; it gives the
  report an explicit signal for expected blocked outcomes such as login or CAPTCHA gates.
- Prefer suite-level `reason_codes.forbidden_reason_codes` for generic regressions like
  `user_intervention_missing`, and task-level `membership` / `blocked_vs_fail` for specific
  service flows.
