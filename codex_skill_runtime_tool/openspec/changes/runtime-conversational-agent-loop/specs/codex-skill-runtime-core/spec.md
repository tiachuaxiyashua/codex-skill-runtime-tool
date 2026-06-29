## ADDED Requirements

### Requirement: Natural-Language Chat Uses Runtime Agent Loop

Runtime SHALL execute natural-language chat through a runtime-owned action loop that supports the same structured action semantics as slash-command strict execution.

#### Scenario: Chat invokes a matching skill

- **GIVEN** a loaded skill repository exposes a model-invocable skill
- **AND** the user submits a natural-language chat request matching that skill
- **WHEN** the model returns a `skill` action
- **THEN** runtime loads the skill content through `ToolExecutor`
- **AND** the loop continues with the loaded skill instructions in subsequent model context
- **AND** the session records tool transcript, invoked skill state, events, and final result.

#### Scenario: Chat uses tools without explicit slash command

- **GIVEN** a user submits a natural-language implementation request
- **WHEN** the model returns runtime actions such as `read_file`, `write_file`, `bash`, `Task`, `MCP`, or `brief`
- **THEN** runtime executes those actions through the shared `ToolExecutor`
- **AND** permission, hook, memory, artifact, and transcript handling match slash-command strict execution.

### Requirement: User Questions Are Explicit Runtime Pauses

Runtime SHALL create a waiting user state only from explicit structured question actions or existing pending question files.

#### Scenario: AskUserQuestion pauses chat

- **GIVEN** the conversational loop is running
- **WHEN** the model returns an `ask_user_question` action
- **THEN** runtime writes `pending-question.json`
- **AND** session status becomes `waiting_user`
- **AND** the UI displays the question in the conversation stream.

#### Scenario: Question-looking assistant text does not pause

- **GIVEN** the model returns `status: final`
- **AND** the final assistant text contains a question mark
- **WHEN** runtime records the result
- **THEN** no pending question is created
- **AND** session status is complete rather than `waiting_user`.

### Requirement: Step Budget Is A Safety Guard

Runtime SHALL treat `max_steps` as an execution safety budget and not as a successful stop condition.

#### Scenario: Budget is exhausted

- **GIVEN** the model repeatedly returns `action_required`
- **WHEN** the loop reaches `max_steps`
- **THEN** runtime returns a blocked result explaining budget exhaustion
- **AND** runtime does not mark the workflow as successfully complete.

### Requirement: UI Separates Dialogue From Process Monitoring

Runtime UI SHALL render human-facing conversation separately from process events.

#### Scenario: Process events stay out of middle conversation

- **GIVEN** a session contains job, tool, hook, model_start, model_finish, memory, MCP, and task-tree events
- **WHEN** the middle conversation pane is rendered
- **THEN** those process events are not rendered as assistant dialogue
- **AND** they remain visible in process/status panes.

#### Scenario: Real assistant messages enter conversation

- **GIVEN** a session contains assistant messages from Codex stdout, transcript assistant output, explicit `brief` records, or pending questions
- **WHEN** the middle conversation pane is rendered
- **THEN** those human-facing messages appear in chronological conversation order.

#### Scenario: Reasoning summaries stay in process monitoring

- **GIVEN** a session contains visible reasoning summary events or model stream events
- **WHEN** the UI is rendered
- **THEN** those rows are available in the process/status panes
- **AND** they are not rendered as direct assistant dialogue in the middle conversation.

### Requirement: Chat Runtime Remains Generic

Runtime SHALL not special-case any one skill, game engine, art/audio pipeline, local path, API provider, or project name to make conversational execution work.

#### Scenario: Hardcoding scan

- **WHEN** changed runtime, UI, test, and OpenSpec files are scanned
- **THEN** no new absolute local drive paths, user-home paths, CCGS-only branches, game-only branches, Forge-only branches, ComfyUI-only branches, or provider-only branches are introduced.
