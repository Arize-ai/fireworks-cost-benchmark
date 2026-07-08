"""cpst — cost-per-successful-task harness.

An instrumented, model-agnostic agent that runs Terminal-Bench tasks in Docker,
grades them with each task's own test script, and records what it costs to
successfully complete a task per model. We use Terminal-Bench only as a source
of tasks; execution and grading are our own.
"""
