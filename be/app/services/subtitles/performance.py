"""Persist stage timings, including failures, without storing prompts or keys."""
import asyncio
from datetime import UTC, datetime
import json
import time


async def measure_stage(name, operation, work_dir):
    started = time.perf_counter()
    outcome = 'completed'
    try:
        return await operation
    except asyncio.CancelledError:
        outcome = 'cancelled'
        raise
    except Exception as exc:
        outcome = type(exc).__name__
        raise
    finally:
        elapsed = time.perf_counter() - started
        path = work_dir / 'stage-performance.json'
        try:
            data = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
            previous = data.get(name, {})
            data[name] = {'last_seconds': round(elapsed, 3),
                          'total_seconds': round(previous.get('total_seconds', 0) + elapsed, 3),
                          'attempts': previous.get('attempts', 0) + 1, 'outcome': outcome,
                          'updated_at': datetime.now(UTC).isoformat()}
            pending = path.with_suffix('.pending.json')
            pending.write_text(json.dumps(data, indent=2), encoding='utf-8')
            pending.replace(path)
        except (OSError, ValueError, TypeError, AttributeError):
            # Measurement must never overwrite the real processing failure.
            pass
