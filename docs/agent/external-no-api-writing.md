# External No-API Chapter Writing

This is the single chapter-writing path for an external MCP or local CLI model when Siming does not supply the model call. The database remains authoritative; project mirrors are read-only.

## Contract

- The user's latest message is the task. The currently open chapter or selected outline node is not an implicit target.
- The Agent reads project entities and chooses a real chapter-level `outline_node_id`. Siming validates project ownership and entity type; it does not infer the target from natural language.
- Only the quality writing prompt is available. De-AI rewriting and quality scoring are separate user actions that read the current editor draft.
- Generation creates one independent unsaved draft. It never updates a saved chapter.
- `save_external_chapter_draft` is terminal: after it succeeds, the model must stop. It must not save a formal chapter, start cataloging, poll a job, update derived story data, or write another chapter in the same turn.
- The author chooses **Save and catalog** or **Save only** in the UI. Cataloging is not started by draft generation.
- A pending unsaved draft or unfinished cataloging job blocks another chapter-writing turn. The application returns the durable state without calling the model again.

## Authoritative flow

1. Call `list_projects` or `get_project_info` to bind the task to the correct project.
2. Use outline/chapter search tools to inspect real entities and select the target required by the latest message. Do not reuse the UI selection as a target.
3. Call `prepare_task_context` or `prepare_external_writing_context` with the selected chapter-level `outline_node_id`.
4. If focused evidence is missing, use `search_task_context`; submit required evidence with `submit_context_evidence`.
5. Generate exactly one quality-mode chapter draft.
6. Call `save_external_chapter_draft` with the same `project_id`, `outline_node_id`, and `context_manifest_id`.
7. Stop immediately. The returned draft is loaded into the editor and remains outside the formal chapter table until the author acts.

Example terminal write:

```json
{
  "tool": "save_external_chapter_draft",
  "arguments": {
    "project_id": "PROJECT_ID",
    "outline_node_id": "CHAPTER_OUTLINE_ID",
    "context_manifest_id": "MANIFEST_ID",
    "content": "Generated chapter text...",
    "source_agent": "external_cli"
  }
}
```

The result contains `draft_id`, the draft content, `turn_terminal=true`, and the two author actions `save_and_catalog` and `save_only`.

## Separate author actions

The following actions are not part of the generation turn:

- **De-AI** and **quality scoring** operate on the current editor text, including an unsaved draft.
- **Save only** promotes the pending draft to a formal chapter without starting cataloging.
- **Save and catalog** promotes the draft and starts the one canonical cataloging job.
- If the author explicitly starts API-free cataloging, the external Agent uses the staged loop `facts -> candidates -> apply -> verify`, completing one chapter before requesting another.

## Failure behavior

- A missing or wrong-type outline ID is rejected; the Agent must read real entities and choose another ID.
- A missing or stale context manifest is returned as `needs_confirmation`; no alternate context renderer is used.
- A pending draft is returned as a blocking state and is never overwritten.
- A non-terminal cataloging job is returned as a blocking state and is never polled by the chat model.
- Database or worker failures are persisted as actionable task states. They do not trigger a hidden fallback workflow.
