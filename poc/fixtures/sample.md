# Hello from the Temporal PoC

This tiny markdown fixture is rendered to PDF by the `render_markdown_to_pdf`
Activity, inside a workflow that is deliberately interrupted mid-flight by
killing its worker process, and that **automatically resumes on a second
worker** once Temporal's heartbeat timeout fires.

- This list item proves list rendering works.
- So does this one.

The rendered PDF and the Temporal event history together are the acceptance
evidence for `scimbe/CADS-agent-marketplace#30`.
