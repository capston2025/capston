# GAIA Adaptive Scheduler

**Adaptive priority-based test execution scheduling for GAIA QA automation**

## 📋 Overview

The Adaptive Scheduler dynamically adjusts test execution order based on:
- **Base Priority**: MUST (100), SHOULD (60), MAY (30)
- **DOM Exploration**: +15 per new element discovered
- **URL Novelty**: +20 for unexplored URLs
- **Failure Retry**: +10 for recently failed tests
- **Stagnation Penalty**: -25 for tests with no DOM changes

## 🏗️ Architecture

```
External Agent (Node.js)
        ↓
   [Checklist with priorities]
        ↓
  Adaptive Scheduler
    ├─ Scoring Engine (scoring.py)
    ├─ Priority Queue (priority_queue.py)
    ├─ State Tracker (state.py)
    └─ Logger (logger.py)
        ↓
   MCP Host (Phase 4)
        ↓
   [Execution Results + DOM]
        ↓
   Re-score & Repeat
```

## 🚀 Quick Start

### Basic Usage

```python
from gaia.src.scheduler import AdaptiveScheduler

# Initialize scheduler
scheduler = AdaptiveScheduler(
    max_queue_size=100,
    top_n_execution=5,
    log_file="priority_log.json"
)

# Ingest test items from agent
items = [
    {
        "id": "TC001",
        "priority": "MUST",
        "name": "Login functionality",
        "steps": [...]
    },
    {
        "id": "TC002",
        "priority": "SHOULD",
        "name": "Search feature",
        "steps": [...]
    }
]
scheduler.ingest_items(items)

# Define executor function
def execute_test(item):
    # Call MCP host or Playwright
    return {
        "status": "success",
        "dom_signature": "abc123",
        "new_elements": 5
    }

# Run adaptive execution
summary = scheduler.execute_until_complete(
    executor=execute_test,
    max_rounds=20,
    completion_threshold=0.9
)

print(summary["execution_stats"])
```

### Integration with GAIA Pipeline

```python
from gaia.src.scheduler.integration import create_scheduler_pipeline

# Agent output from /api/analyze
agent_data = {
    "checklist": [
        {
            "id": "TC001",
            "priority": "MUST",
            "name": "Login",
            "steps": [...]
        }
    ]
}

# Run full pipeline
summary = create_scheduler_pipeline(
    agent_output=agent_data,
    mcp_host_url="http://localhost:8001"
)

print(f"Completed: {summary['state_summary']['completed_tests']}")
print(f"Success rate: {summary['execution_stats']['total_success']}/{summary['execution_stats']['total_executed']}")
```

## 📊 Scoring Formula

```python
score = base_priority
      + (new_elements * 15)
      + (unseen_url ? 20 : 0)
      + (recent_fail ? 10 : 0)
      - (no_dom_change ? 25 : 0)
```

### Examples

| Priority | New Elements | Unseen URL | Recent Fail | No DOM Change | **Final Score** |
|----------|--------------|------------|-------------|---------------|-----------------|
| MUST     | 0            | No         | No          | No            | **100**         |
| MUST     | 2            | Yes        | No          | No            | **150**         |
| SHOULD   | 1            | Yes        | Yes         | No            | **105**         |
| MUST     | 0            | No         | No          | Yes           | **75**          |

## 🔄 Execution Flow

```
1️⃣ Ingest items from agent
    ↓
2️⃣ Compute priority scores
    ↓
3️⃣ Execute top N items
    ↓
4️⃣ Update GAIA state
    ↓
5️⃣ Detect DOM changes → Re-score
    ↓
6️⃣ Check completion criteria
    ↓
7️⃣ Repeat or Exit
```

## 📁 Module Structure

```
gaia/src/scheduler/
├── __init__.py               # Module exports
├── adaptive_scheduler.py     # Main orchestrator
├── scoring.py                # Score calculation
├── priority_queue.py         # Heap-based queue
├── state.py                  # State management
├── logger.py                 # Priority logging
├── integration.py            # Phase integration
├── DESIGN_SPEC.json          # Full specification
└── README.md                 # This file

gaia/tests/
└── test_scheduler.py         # Unit tests
```

## 🧪 Testing

Run unit tests:

```bash
pytest gaia/tests/test_scheduler.py -v
```

Test coverage:
- State management
- Score calculations
- Queue operations
- Execution loop
- DOM change detection

## 📄 Log Format

Priority log entries are saved to `priority_log.json`:

```json
{
  "id": "TC001",
  "action": "executed",
  "result": "success",
  "score": 135,
  "priority": "MUST",
  "base_score": 100,
  "dom_bonus": 15,
  "url_bonus": 20,
  "fail_bonus": 0,
  "no_change_penalty": 0,
  "timestamp": "2025-10-22T14:00:00Z",
  "execution_round": 1
}
```

## 🔧 Configuration

Adjust scoring constants in `scoring.py`:

```python
PRIORITY_SCORES = {
    "MUST": 100,
    "SHOULD": 60,
    "MAY": 30,
}

BONUS_NEW_ELEMENTS = 15
BONUS_UNSEEN_URL = 20
BONUS_RECENT_FAIL = 10
PENALTY_NO_DOM_CHANGE = 25
```

## 🔌 MCP Integration

The scheduler integrates with the MCP host (`gaia/src/phase4/mcp_host.py`):

```python
from gaia.src.scheduler.integration import SchedulerIntegration

integration = SchedulerIntegration(mcp_host_url="http://localhost:8001")

# Receive from agent
integration.receive_from_agent(agent_output)

# Run with MCP execution
summary = integration.run_adaptive_execution(
    max_rounds=20,
    completion_threshold=0.9
)
```

## 📈 Statistics & Monitoring

Get real-time statistics:

```python
stats = scheduler.get_stats()
# {
#   "total_received": 25,
#   "total_executed": 20,
#   "total_success": 18,
#   "total_failed": 2,
#   "total_skipped": 0,
#   "rescore_count": 3
# }

state = scheduler.get_state()
print(f"Visited URLs: {len(state.visited_urls)}")
print(f"Completed tests: {len(state.completed_test_ids)}")
```

## 🎯 Key Features

✅ **Dynamic Re-scoring**: Queue priorities adjust based on DOM changes
✅ **Failure Retry**: Failed tests get retry bonus (+10)
✅ **Exploration Bonus**: New URLs and DOM elements prioritized
✅ **Stagnation Penalty**: Tests with no DOM changes deprioritized
✅ **Completion Tracking**: Automatic removal of completed tests
✅ **Detailed Logging**: Full execution history in JSON format
✅ **Heap Efficiency**: O(log n) insertion and extraction

## 🚧 Limitations (v1.0)

- No exponential backoff for retries
- Fixed scoring weights (not ML-based)
- Single-threaded execution
- No distributed support

## 🔮 Future Enhancements

**v1.1**:
- Configurable scoring weights via JSON
- Exponential backoff for retries
- Real-time dashboard

**v2.0**:
- ML-based priority prediction
- Distributed execution across multiple MCP hosts
- Advanced analytics and reporting

## 📚 API Reference

See [DESIGN_SPEC.json](./DESIGN_SPEC.json) for complete API documentation.

### Core Classes

- **`AdaptiveScheduler`**: Main orchestrator
- **`GAIAState`**: State tracking
- **`AdaptivePriorityQueue`**: Heap-based queue
- **`PriorityLogger`**: Execution logging
- **`SchedulerIntegration`**: Phase integration

### Key Functions

- **`compute_priority_score(item, state)`**: Calculate score
- **`compute_dom_signature(dom_data)`**: Generate DOM hash
- **`create_scheduler_pipeline(agent_output)`**: Full pipeline

## 🤝 Contributing

When modifying the scheduler:

1. Update scoring constants in `scoring.py`
2. Add tests to `test_scheduler.py`
3. Update `DESIGN_SPEC.json` if API changes
4. Run full test suite: `pytest gaia/tests/ -v`

## 📞 Support

For issues or questions:
- See [PROJECT_CONTEXT.md](../../docs/PROJECT_CONTEXT.md)
- Check [IMPLEMENTATION_GUIDE.md](../../docs/IMPLEMENTATION_GUIDE.md)

---

**Version**: 1.0.0
**Author**: Claude AI
**Date**: 2025-10-22
